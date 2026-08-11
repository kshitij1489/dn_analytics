"""
Server-authoritative menu mutation commit (strict mode).

Capture local edits in a rolled-back transaction, POST one mutation to the server,
then apply the accepted response through the existing pull appliers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.menu_mapping_verification_sync import (
    apply_remote_menu_mapping_verification_event,
    get_menu_mapping_verification_pull_endpoint,
    pull_and_apply_menu_mapping_verification_events,
)
from src.core.menu_merge_sync import (
    _run_assignment_batch_epilogue,
    apply_remote_menu_merge_event,
    get_menu_merge_pull_endpoint,
    pull_and_apply_menu_merge_events,
)
from src.core.sync_identity import (
    apply_menu_scope_state,
    extract_menu_scope_state,
    get_menu_state_revision,
    get_sync_attribution,
    set_menu_state_revision,
    should_apply_pulled_menu_revision,
)

logger = logging.getLogger(__name__)

MAX_COMMIT_ATTEMPTS = 2
MAX_PULL_STABILIZATION_ROUNDS = 3
MAX_PULL_PAGES_PER_STREAM = 1000
COMMIT_SCHEMA_VERSION = 1
NETWORK_ERROR_MESSAGE = "Menu changes require a cloud connection."
MENU_CONFLICT_USER_MESSAGE = (
    "Menu changed on another installation. Sync latest menu state and try again."
)
STRICT_MODE_NOT_READY_MESSAGE = "Menu editing requires a cloud connection."
LOCAL_APPLY_FAILED_MESSAGE = "Server accepted the change, but local refresh is required."
RETRY_AFTER_RECONCILE = "retry_after_reconcile"

MUTATION_TYPE_MENU_MERGE_APPLIED = "menu_merge.applied"
MUTATION_TYPE_MENU_MERGE_UNDONE = "menu_merge.undone"
MUTATION_TYPE_RESOLUTION_VARIANT = "resolution_variant"
MUTATION_TYPE_ORDER_ITEM_REMAP = "order_item_remap"
MUTATION_TYPE_VERIFY = "verify"
MUTATION_TYPE_CATALOG_UPDATE = "catalog_update"
MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC = "derived_assignment.sync"


@dataclass
class MutationPlan:
    mutation_id: str
    mutation_type: str
    event: Optional[Dict[str, Any]]
    verification_events: List[Dict[str, Any]] = field(default_factory=list)
    catalog_delta: Dict[str, Any] = field(default_factory=lambda: {"items": [], "variants": []})
    order_item_ids: List[str] = field(default_factory=list)
    expected_menu_revision: Optional[int] = None


@dataclass
class CommitResult:
    status: str
    message: str = ""
    merge_id: Optional[int] = None
    conflict: Optional[Dict[str, Any]] = None
    skipped_existing: List[str] = field(default_factory=list)


class MenuStatePullError(RuntimeError):
    """The local menu state could not be brought to a safe server revision."""


def strict_mode_ready(conn) -> bool:
    """
    True when cloud sync is configured and the server has advertised menu_revision
    at least once. Older servers that omit revision keep strict mode disabled.
    """
    base_url, api_key = get_cloud_sync_config(conn)
    if not base_url or not api_key:
        return False
    return get_menu_state_revision(conn) is not None


def strict_mode_active(conn) -> bool:
    """True when the client is ready for server-authoritative commits. The server is
    always strict — the only remaining gate is local readiness."""
    return strict_mode_ready(conn)


def strict_mode_editing_blocked(conn) -> bool:
    """True when strict mode is required but local readiness is missing."""
    return not strict_mode_ready(conn)


def strict_mode_edit_blocked_response(conn, *, emit_sync_event: bool = True) -> Optional[Dict[str, Any]]:
    """Return a blocked-response dict for human edits when strict mode is on but not ready."""
    if not emit_sync_event:
        return None
    if strict_mode_editing_blocked(conn):
        return {
            "status": "error",
            "message": STRICT_MODE_NOT_READY_MESSAGE,
            "code": "strict_mode_not_ready",
        }
    return None


def extract_conflict_attribution(conflicting_events: Any) -> List[Dict[str, Any]]:
    """Collect unique attribution blobs from conflicting server events for UI display."""
    seen: set = set()
    attribution: List[Dict[str, Any]] = []
    for event in conflicting_events or []:
        if not isinstance(event, dict):
            continue
        attr = event.get("attribution")
        if not isinstance(attr, dict):
            continue
        employee = attr.get("employee") if isinstance(attr.get("employee"), dict) else {}
        device = attr.get("device") if isinstance(attr.get("device"), dict) else {}
        key = (
            str(employee.get("employee_id") or employee.get("name") or ""),
            str(device.get("device_id") or device.get("install_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        attribution.append(attr)
    return attribution


def build_menu_edit_http_exception(res: Dict[str, Any]):
    """Return an HTTPException for non-success menu edit responses, else None."""
    from fastapi import HTTPException

    status = res.get("status")
    if status == "success":
        return None
    if status == "conflict":
        conflicting = res.get("conflicting_events") or []
        return HTTPException(
            status_code=409,
            detail={
                "message": MENU_CONFLICT_USER_MESSAGE,
                "current_menu_revision": res.get("current_menu_revision"),
                "conflicting_events": conflicting,
                "attribution": extract_conflict_attribution(conflicting),
            },
        )
    if res.get("code") == "strict_mode_not_ready" or (
        status == "error" and res.get("message") == STRICT_MODE_NOT_READY_MESSAGE
    ):
        return HTTPException(status_code=503, detail=STRICT_MODE_NOT_READY_MESSAGE)
    if status == "error":
        message = str(res.get("message") or "Menu edit failed")
        if message == NETWORK_ERROR_MESSAGE:
            return HTTPException(status_code=503, detail=message)
        if message == LOCAL_APPLY_FAILED_MESSAGE:
            return HTTPException(status_code=500, detail=message)
        return HTTPException(status_code=400, detail=message)
    return HTTPException(status_code=400, detail="Unexpected menu edit response")


def build_plan(
    *,
    mutation_type: str,
    event: Optional[Dict[str, Any]] = None,
    verification_events: Optional[List[Dict[str, Any]]] = None,
    catalog_delta: Optional[Dict[str, Any]] = None,
    order_item_ids: Optional[List[str]] = None,
    mutation_id: Optional[str] = None,
) -> MutationPlan:
    """Assemble a mutation plan; mutation_id is generated once and reused across retries."""
    delta = catalog_delta if catalog_delta is not None else {"items": [], "variants": []}
    return MutationPlan(
        mutation_id=mutation_id or str(uuid.uuid4()),
        mutation_type=mutation_type,
        event=event,
        verification_events=list(verification_events or []),
        catalog_delta=delta,
        order_item_ids=[str(oid) for oid in (order_item_ids or []) if str(oid).strip()],
    )


def commit_mutation(conn, plan: MutationPlan) -> CommitResult:
    """POST the mutation with auto-retry on stale revision and reconcile on timeout."""
    last_conflict: Optional[Dict[str, Any]] = None
    for attempt in range(MAX_COMMIT_ATTEMPTS):
        revision = (
            plan.expected_menu_revision
            if attempt == 0 and plan.expected_menu_revision is not None
            else get_menu_state_revision(conn)
        )
        if revision is None:
            return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)

        status_code, body, transport_error = _post_commit(conn, plan, expected_menu_revision=revision)
        if transport_error:
            reconcile = _reconcile_after_timeout(conn, plan, expected_menu_revision=revision)
            if reconcile is not None:
                if reconcile.status == RETRY_AFTER_RECONCILE:
                    continue
                return reconcile
            return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)

        if status_code == 200 and isinstance(body, dict):
            try:
                apply_result = apply_accepted(conn, body, plan)
            except Exception:
                return _handle_local_apply_failed(
                    conn,
                    mutation_id=plan.mutation_id,
                    context="Local apply failed after server accepted mutation",
                )
            return CommitResult(
                status="ok",
                message="accepted",
                merge_id=apply_result.get("local_merge_id"),
                skipped_existing=[
                    str(value)
                    for value in (body.get("skipped_existing") or [])
                    if str(value).strip()
                ],
            )

        if status_code == 409 and isinstance(body, dict):
            last_conflict = body
            try:
                pull_latest_menu_state(conn)
            except MenuStatePullError:
                return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)
            if plan_overlaps_pulled_changes(plan, body.get("conflicting_events")):
                return CommitResult(
                    status="conflict",
                    message=str(body.get("error") or "Menu state conflict"),
                    conflict=body,
                )
            continue

        if status_code and status_code >= 500:
            reconcile = _reconcile_after_timeout(conn, plan, expected_menu_revision=revision)
            if reconcile is not None:
                if reconcile.status == RETRY_AFTER_RECONCILE:
                    continue
                if reconcile.status == "conflict":
                    last_conflict = reconcile.conflict
                    try:
                        pull_latest_menu_state(conn)
                    except MenuStatePullError:
                        return CommitResult(
                            status="error",
                            message=NETWORK_ERROR_MESSAGE,
                        )
                    if plan_overlaps_pulled_changes(plan, (last_conflict or {}).get("conflicting_events")):
                        return reconcile
                    continue
                return reconcile
            return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)

        error_message = NETWORK_ERROR_MESSAGE
        if isinstance(body, dict) and body.get("error"):
            error_message = str(body["error"])
        return CommitResult(status="error", message=error_message)

    return CommitResult(
        status="conflict",
        message=str((last_conflict or {}).get("error") or "Menu state conflict"),
        conflict=last_conflict,
    )


def _catalog_delta_id_sets(catalog_delta: Any) -> Tuple[set, set]:
    """Extract menu_item_id and variant_id sets from a catalog_delta payload."""
    if not isinstance(catalog_delta, dict):
        return set(), set()
    item_ids = set()
    for item in catalog_delta.get("items") or []:
        if not isinstance(item, dict):
            continue
        menu_item_id = str(item.get("menu_item_id") or "").strip()
        if menu_item_id:
            item_ids.add(menu_item_id)
    variant_ids = set()
    for variant in catalog_delta.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        variant_id = str(variant.get("variant_id") or "").strip()
        if variant_id:
            variant_ids.add(variant_id)
    return item_ids, variant_ids


def _catalog_delta_echo_matches(plan_delta: Dict[str, Any], response_delta: Any) -> bool:
    """
    True when the response echoes the same catalog entities as the plan.

    Exact dict/list equality is intentionally avoided — server normalization
    (key order, bool/int coercion, optional fields) must not fail validation.
    """
    plan_items, plan_variants = _catalog_delta_id_sets(plan_delta)
    response_items, response_variants = _catalog_delta_id_sets(response_delta)
    return plan_items == response_items and plan_variants == response_variants


def _validate_accepted_response(body: Dict[str, Any], plan: MutationPlan) -> None:
    """Reject incomplete or mismatched commit responses before local mutation."""
    if body.get("status") != "accepted":
        raise ValueError("Commit response is not accepted.")
    if str(body.get("mutation_id") or "") != plan.mutation_id:
        raise ValueError("Commit response mutation_id does not match the request.")
    if body.get("menu_revision") is None:
        raise ValueError("Commit response is missing menu_revision.")

    expected_remote_ids = set()
    if plan.event:
        expected_remote_ids.add(str(plan.event.get("remote_event_id") or ""))
    expected_remote_ids.update(
        str(event.get("remote_event_id") or "")
        for event in plan.verification_events
        if isinstance(event, dict)
    )
    if "" in expected_remote_ids:
        raise ValueError("Mutation plan contains an event without remote_event_id.")

    accepted_rows = body.get("accepted_events")
    if not isinstance(accepted_rows, list):
        raise ValueError("Commit response accepted_events must be a list.")
    accepted_remote_ids = set()
    for row in accepted_rows:
        if (
            not isinstance(row, dict)
            or not row.get("remote_event_id")
            or row.get("server_seq") is None
            or not row.get("server_ingested_at")
        ):
            raise ValueError("Commit response contains incomplete event metadata.")
        accepted_remote_ids.add(str(row["remote_event_id"]))
    if accepted_remote_ids != expected_remote_ids:
        raise ValueError("Commit response events do not match the mutation plan.")
    response_catalog_delta = body.get("catalog_delta")
    if response_catalog_delta is not None and not _catalog_delta_echo_matches(
        plan.catalog_delta, response_catalog_delta
    ):
        raise ValueError("Commit response catalog_delta does not match the mutation plan.")


def apply_accepted(conn, body: Dict[str, Any], plan: MutationPlan) -> Dict[str, Any]:
    """Apply a server-accepted mutation in one SQLite transaction via pull appliers."""
    _validate_accepted_response(body, plan)
    accepted_by_remote_id = {
        str(row.get("remote_event_id")): row
        for row in (body.get("accepted_events") or [])
        if isinstance(row, dict) and row.get("remote_event_id")
    }
    local_merge_id: Optional[int] = None
    touched_menu_item_ids: set = set()

    try:
        if plan.event:
            remote_event_id = str(plan.event.get("remote_event_id") or "")
            accepted = accepted_by_remote_id.get(remote_event_id, {})
            event = dict(plan.event)
            if plan.mutation_type == MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC:
                skipped_existing = {
                    str(value)
                    for value in (body.get("skipped_existing") or [])
                    if str(value).strip()
                }
                if skipped_existing:
                    merge_payload = dict(event.get("merge_payload") or {})
                    assignments = [
                        assignment
                        for assignment in (merge_payload.get("assignments") or [])
                        if isinstance(assignment, dict)
                        and str(assignment.get("order_item_id") or "").strip()
                        not in skipped_existing
                    ]
                    merge_payload["assignments"] = assignments
                    event["merge_payload"] = merge_payload
            if accepted.get("server_seq") is not None:
                event["server_seq"] = accepted["server_seq"]
            if accepted.get("server_ingested_at") is not None:
                event["server_ingested_at"] = accepted["server_ingested_at"]
            merge_result = apply_remote_menu_merge_event(conn, event, body.get("merge_cursor"))
            local_merge_id = merge_result.get("local_merge_id")
            touched_menu_item_ids |= set(merge_result.get("touched_menu_item_ids") or ())

        for verification_event in plan.verification_events:
            if not isinstance(verification_event, dict):
                continue
            remote_event_id = str(verification_event.get("remote_event_id") or "")
            accepted = accepted_by_remote_id.get(remote_event_id, {})
            event = dict(verification_event)
            if accepted.get("server_seq") is not None:
                event["server_seq"] = accepted["server_seq"]
            if accepted.get("server_ingested_at") is not None:
                event["server_ingested_at"] = accepted["server_ingested_at"]
            apply_remote_menu_mapping_verification_event(conn, event, body.get("verification_cursor"))

        catalog_delta = body.get("catalog_delta")
        if catalog_delta is None:
            catalog_delta = plan.catalog_delta
        _apply_catalog_delta(conn, catalog_delta)

        if plan.mutation_type == MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC:
            touched_menu_item_ids |= _adopt_skipped_assignment_rows(
                conn,
                body.get("assignment_rows"),
                body.get("skipped_existing"),
            )

        # Deliberately leave the pull cursors alone (plan §12.5). The response's
        # merge_cursor/verification_cursor are the post-commit stream heads; if
        # this install's cursors lag behind the pre-commit heads (broken
        # revision/cursor invariant), advancing to them would permanently skip
        # never-applied peer events. The next pull replays the small gap, and
        # replaying this mutation's own events is idempotent under the
        # remote-event-id dedupe and seq guards.

        if body.get("menu_revision") is not None:
            set_menu_state_revision(conn, int(body["menu_revision"]))
        scope_state = extract_menu_scope_state(body)
        if scope_state:
            apply_menu_scope_state(conn, scope_state)

        parity_mismatches = check_assignment_parity(conn, body.get("assignment_rows"))
        if parity_mismatches:
            logger.warning(
                "assignment_rows parity mismatch after mutation %s: %s",
                plan.mutation_id,
                parity_mismatches,
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    # Same batch epilogue peers run when they pull these events (stats
    # recompute, resolution-state sync/GC, forecast-cache clears). The capture
    # transaction that originally did this work was rolled back before the
    # commit, so the committing install must redo it.
    if touched_menu_item_ids:
        _run_assignment_batch_epilogue(conn, touched_menu_item_ids)

    return {"local_merge_id": local_merge_id, "touched_menu_item_ids": touched_menu_item_ids}


def _adopt_skipped_assignment_rows(
    conn,
    assignment_rows: Any,
    skipped_existing: Any,
) -> set:
    """
    Converge rows the server skipped as already-assigned (derived flush only).

    A skipped_existing key means the server holds an authoritative assignment
    (usually from a peer's earlier decision) for a row this install still has
    unstamped (assignment_seq NULL). Left alone, the row is re-selected and
    re-flushed every Sync DB cycle forever — and if the originating server
    event is already behind this install's merge cursor, no pull will ever
    stamp it. The commit response's assignment_rows carry the authoritative
    server value for every touched key, so adopt it here: take the server
    mapping and stamp its seq, which drops the row from the pending set.

    Returns the menu_item_ids touched (old and new) for the batch epilogue.
    """
    from src.core.menu_assignment_apply import (
        _menu_item_exists,
        _normalize_variant_value,
        coerce_server_seq,
        ensure_variant_exists,
    )
    from src.core.order_item_key import update_local_order_rows_for_assignment_key

    skipped_ids = {
        str(value).strip()
        for value in (skipped_existing or [])
        if str(value or "").strip()
    }
    if not skipped_ids:
        return set()

    rows_by_id = {
        str(row.get("order_item_id") or "").strip(): row
        for row in (assignment_rows or [])
        if isinstance(row, dict) and str(row.get("order_item_id") or "").strip()
    }
    touched: set = set()
    for order_item_id in sorted(skipped_ids):
        server_row = rows_by_id.get(order_item_id)
        if server_row is None:
            logger.warning(
                "skipped_existing key %s missing from assignment_rows; leaving pending",
                order_item_id,
            )
            continue
        server_seq = coerce_server_seq(server_row.get("assignment_seq"))
        if server_seq is None:
            logger.warning(
                "skipped_existing key %s has no server assignment_seq; leaving pending",
                order_item_id,
            )
            continue
        local = conn.execute(
            """
            SELECT menu_item_id, variant_id, assignment_seq, pending_local
            FROM menu_item_variants
            WHERE order_item_id = ?
            LIMIT 1
            """,
            (order_item_id,),
        ).fetchone()
        # Only rows still in the flush pending set are adopted; a stamped or
        # pending_local row already has an owner (pull applier / outbox echo).
        if (
            local is None
            or local["assignment_seq"] is not None
            or int(local["pending_local"] or 0)
        ):
            continue
        menu_item_id = str(server_row.get("menu_item_id") or "").strip()
        if not menu_item_id:
            continue
        variant_id = _normalize_variant_value(server_row.get("variant_id"))
        if variant_id is not None:
            ensure_variant_exists(conn, variant_id, None)
        if not _menu_item_exists(conn, menu_item_id):
            # FK would abort the whole apply; the row stays pending and heals
            # after the next bootstrap/catalog pull materializes the item.
            logger.warning(
                "skipped_existing key %s references unknown menu_item_id %s; leaving pending",
                order_item_id,
                menu_item_id,
            )
            continue
        is_verified = server_row.get("is_verified")
        verification_seq = coerce_server_seq(server_row.get("verification_seq"))
        conn.execute(
            """
            UPDATE menu_item_variants
            SET menu_item_id = ?,
                variant_id = ?,
                is_verified = ?,
                assignment_seq = ?,
                verification_seq = COALESCE(?, verification_seq),
                pending_local = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE order_item_id = ?
            """,
            (
                menu_item_id,
                variant_id,
                1 if is_verified else 0,
                server_seq,
                verification_seq,
                order_item_id,
            ),
        )
        update_local_order_rows_for_assignment_key(
            conn,
            order_item_id,
            menu_item_id=menu_item_id,
            variant_id=variant_id,
            variant_specified=True,
        )
        touched.add(str(local["menu_item_id"]))
        touched.add(menu_item_id)
    return touched


def _drain_menu_stream(pull_fn, conn, endpoint: str, api_key: str) -> Dict[str, Any]:
    """Drain one cursor stream and reject transport or event-apply failures."""
    last_stats: Dict[str, Any] = {}
    for _page in range(MAX_PULL_PAGES_PER_STREAM):
        last_stats = pull_fn(conn, endpoint, auth=api_key)
        if last_stats.get("error"):
            raise MenuStatePullError(str(last_stats["error"]))
        if last_stats.get("events_failed") or last_stats.get("events_quarantined"):
            raise MenuStatePullError("Menu event apply did not complete cleanly.")
        # `deferred` is NOT fatal: a verification event whose order line this
        # install has never ingested is persisted to
        # menu_mapping_verification_deferred and retried by the flush/retry pass
        # (it applies idempotently once/if the row appears). Some order lines are
        # from peer installs and will never arrive here, so failing the whole
        # menu pull on a permanent deferral would wedge every sync ("Sync
        # Failed") forever. The cursor safely advances past it — the deferred
        # copy is retained, so nothing is lost.
        if not last_stats.get("has_more"):
            return last_stats
    raise MenuStatePullError("Menu event pull exceeded the page safety limit.")


def pull_latest_menu_state(conn, *, already_locked: bool = False) -> Dict[str, Any]:
    """
    Drain both menu streams to the same advertised global revision.

    A peer commit between the two stream reads yields different advertised
    revisions, so both streams are drained again before menu_state_revision moves.

    Serializes through CLOUD_PULL_LOCK (plan C5.1) unless already_locked=True —
    the orchestrator passes that when it already holds the lock.
    """
    from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK

    acquired = False
    if not already_locked:
        CLOUD_PULL_LOCK.acquire(blocking=True)
        acquired = True
    try:
        return _pull_latest_menu_state_locked(conn)
    finally:
        if acquired:
            CLOUD_PULL_LOCK.release()


def _pull_latest_menu_state_locked(conn) -> Dict[str, Any]:
    _base_url, api_key = get_cloud_sync_config(conn)
    merge_endpoint = get_menu_merge_pull_endpoint(conn)
    verification_endpoint = get_menu_mapping_verification_pull_endpoint(conn)
    if not merge_endpoint or not verification_endpoint or not api_key:
        raise MenuStatePullError("Both menu pull streams must be configured.")

    for _round in range(MAX_PULL_STABILIZATION_ROUNDS):
        merge_stats = _drain_menu_stream(
            pull_and_apply_menu_merge_events,
            conn,
            merge_endpoint,
            api_key,
        )
        verification_stats = _drain_menu_stream(
            pull_and_apply_menu_mapping_verification_events,
            conn,
            verification_endpoint,
            api_key,
        )
        merge_revision = merge_stats.get("advertised_menu_revision")
        verification_revision = verification_stats.get("advertised_menu_revision")
        if (
            merge_revision is not None
            and verification_revision is not None
            and int(merge_revision) == int(verification_revision)
            and should_apply_pulled_menu_revision(
                conn,
                has_more=False,
                stats={},
            )
        ):
            set_menu_state_revision(conn, int(merge_revision))
            conn.commit()
            return {
                "menu_revision": int(merge_revision),
                "menu_merges": merge_stats,
                "menu_mapping_verifications": verification_stats,
            }

    raise MenuStatePullError(
        "Menu streams did not stabilize at the same server revision."
    )


_PARITY_FIELDS = (
    "menu_item_id",
    "variant_id",
    "is_verified",
    "assignment_seq",
    "verification_seq",
)


def _normalize_assignment_parity_value(field: str, value: Any) -> Any:
    if field == "variant_id":
        if value in (None, "", "__NULL_VARIANT__"):
            return None
        return str(value)
    if field == "is_verified":
        return 1 if value else 0
    if field in {"assignment_seq", "verification_seq"}:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if value is None:
        return None
    return str(value)


def check_assignment_parity(conn, assignment_rows: Any) -> List[Dict[str, Any]]:
    """
    Compare server assignment_rows against local menu_item_variants (parity check only).

    Returns a list of mismatch records; empty when local rows match the server projection.
    """
    mismatches: List[Dict[str, Any]] = []
    for row in assignment_rows or []:
        if not isinstance(row, dict):
            continue
        order_item_id = str(row.get("order_item_id") or "").strip()
        if not order_item_id:
            continue
        local = conn.execute(
            """
            SELECT menu_item_id, variant_id, is_verified, assignment_seq, verification_seq
            FROM menu_item_variants
            WHERE order_item_id = ?
            """,
            (order_item_id,),
        ).fetchone()
        if local is None:
            mismatches.append(
                {
                    "order_item_id": order_item_id,
                    "reason": "missing_local_row",
                    "server": dict(row),
                }
            )
            continue
        field_diffs: Dict[str, Dict[str, Any]] = {}
        for field in _PARITY_FIELDS:
            server_value = _normalize_assignment_parity_value(field, row.get(field))
            local_value = _normalize_assignment_parity_value(field, local[field])
            if server_value != local_value:
                field_diffs[field] = {"server": server_value, "local": local_value}
        if field_diffs:
            mismatches.append(
                {
                    "order_item_id": order_item_id,
                    "reason": "field_mismatch",
                    "diff": field_diffs,
                }
            )
    return mismatches


def plan_overlaps_pulled_changes(plan: MutationPlan, conflicting_events: Any) -> bool:
    """
    Return True for a real overlap or when the server cannot prove non-overlap.

    Missing conflict metadata must fail closed; otherwise a legacy revision bump
    without a complete event list could cause a stale plan to be retried.
    """
    if not isinstance(conflicting_events, list) or not conflicting_events:
        return True
    plan_ids = {str(oid) for oid in plan.order_item_ids if str(oid).strip()}
    if not plan_ids:
        return True
    for event in conflicting_events:
        if not isinstance(event, dict) or not isinstance(
            event.get("order_item_ids"),
            list,
        ):
            return True
        for order_item_id in event["order_item_ids"]:
            if str(order_item_id) in plan_ids:
                return True
    return False


def commit_result_to_menu_response(
    result: CommitResult,
    *,
    success_message: str,
    success_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map CommitResult into the dict shape returned by menu_utils edit functions."""
    if result.status == "ok":
        payload: Dict[str, Any] = {
            "status": "success",
            "message": success_message,
        }
        if result.merge_id is not None:
            payload["merge_id"] = result.merge_id
        if success_extra:
            payload.update(success_extra)
        return payload
    if result.status == "conflict":
        conflict = result.conflict or {}
        return {
            "status": "conflict",
            "message": MENU_CONFLICT_USER_MESSAGE,
            "current_menu_revision": conflict.get("current_menu_revision"),
            "conflicting_events": conflict.get("conflicting_events"),
        }
    return {"status": "error", "message": result.message or NETWORK_ERROR_MESSAGE}


def _reconcile_after_timeout(
    conn,
    plan: MutationPlan,
    *,
    expected_menu_revision: int,
) -> Optional[CommitResult]:
    status_code, body, transport_error = _get_mutation_status(conn, plan.mutation_id)
    if transport_error:
        return None
    if status_code == 200 and isinstance(body, dict):
        try:
            apply_result = apply_accepted(conn, body, plan)
        except Exception:
            return _handle_local_apply_failed(
                conn,
                mutation_id=plan.mutation_id,
                context="Local apply failed during timeout reconcile",
            )
        return CommitResult(
            status="ok",
            message="accepted",
            merge_id=apply_result.get("local_merge_id"),
            skipped_existing=[
                str(value)
                for value in (body.get("skipped_existing") or [])
                if str(value).strip()
            ],
        )

    if status_code != 404:
        return None

    retry_code, retry_body, retry_error = _post_commit(conn, plan, expected_menu_revision=expected_menu_revision)
    if retry_error:
        return None
    if retry_code == 200 and isinstance(retry_body, dict):
        try:
            apply_result = apply_accepted(conn, retry_body, plan)
        except Exception:
            return _handle_local_apply_failed(
                conn,
                mutation_id=plan.mutation_id,
                context="Local apply failed after timeout re-POST",
            )
        return CommitResult(
            status="ok",
            message="accepted",
            merge_id=apply_result.get("local_merge_id"),
            skipped_existing=[
                str(value)
                for value in (retry_body.get("skipped_existing") or [])
                if str(value).strip()
            ],
        )
    if retry_code == 409 and isinstance(retry_body, dict):
        try:
            pull_latest_menu_state(conn)
        except MenuStatePullError:
            return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)
        if plan_overlaps_pulled_changes(plan, retry_body.get("conflicting_events")):
            return CommitResult(
                status="conflict",
                message=str(retry_body.get("error") or "Menu state conflict"),
                conflict=retry_body,
            )
        return CommitResult(status=RETRY_AFTER_RECONCILE, message="retry")
    return None


def _handle_local_apply_failed(conn, *, mutation_id: str, context: str) -> CommitResult:
    """
    Server accepted the mutation but local apply failed.

    Attempt a snapshot force-reseed so the install converges; always surface the
    operator-facing refresh message to the UI.
    """
    logger.exception("%s: %s", context, mutation_id)
    _attempt_force_reseed_after_local_apply_failure(conn, mutation_id)
    return CommitResult(status="error", message=LOCAL_APPLY_FAILED_MESSAGE)


def _attempt_force_reseed_after_local_apply_failure(conn, mutation_id: str) -> None:
    try:
        pull_latest_menu_state(conn)
        logger.info(
            "Replayed menu event streams after local apply failure for %s",
            mutation_id,
        )
        return
    except Exception:
        logger.exception(
            "Menu pull replay failed after local apply failure for %s",
            mutation_id,
        )

    try:
        from src.core.menu_assignment_bootstrap import (
            force_reseed_menu_assignments,
            get_menu_assignments_snapshot_endpoint,
        )

        endpoint = get_menu_assignments_snapshot_endpoint(conn)
        if not endpoint:
            logger.warning(
                "Cannot force-reseed after local apply failure for %s: cloud sync URL not configured",
                mutation_id,
            )
            return

        _, api_key = get_cloud_sync_config(conn)
        reseed = force_reseed_menu_assignments(
            conn,
            endpoint,
            auth=api_key,
            apply_scope_state=False,
        )
        if reseed.get("status") == "reseeded":
            logger.info(
                "Force-reseeded menu assignments after local apply failure for %s (%s rows applied)",
                mutation_id,
                reseed.get("rows_applied"),
            )
            return

        logger.warning(
            "Force reseed failed after local apply failure for %s: %s",
            mutation_id,
            reseed.get("error") or reseed,
        )
    except Exception:
        logger.exception("Force reseed raised after local apply failure for %s", mutation_id)


def _cloud_config(conn) -> Tuple[str, str]:
    base_url, api_key = get_cloud_sync_config(conn)
    if not base_url or not api_key:
        raise RuntimeError(NETWORK_ERROR_MESSAGE)
    return base_url.rstrip("/"), api_key


def _commit_url(base_url: str) -> str:
    return f"{base_url}/desktop-analytics-sync/menu-mutations/commit"


def _status_url(base_url: str, mutation_id: str) -> str:
    return f"{base_url}/desktop-analytics-sync/menu-mutations/{mutation_id}"


def _auth_headers(conn, api_key: str) -> Dict[str, str]:
    from src.core.central_api import scoped_headers

    return scoped_headers(
        conn, auth_kind="sync", credential=api_key, content_type="application/json"
    )


def _build_commit_request(conn, plan: MutationPlan, *, expected_menu_revision: int) -> Dict[str, Any]:
    attribution = get_sync_attribution(conn)
    request: Dict[str, Any] = {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "mutation_id": plan.mutation_id,
        "mutation_type": plan.mutation_type,
        "expected_menu_revision": expected_menu_revision,
        "verification_events": plan.verification_events,
        "catalog_delta": plan.catalog_delta,
        "uploaded_by": attribution.get("employee") or None,
        "uploaded_from": attribution.get("device") or None,
    }
    if plan.event is not None:
        request["event"] = plan.event
    return request


def _post_commit(
    conn,
    plan: MutationPlan,
    *,
    expected_menu_revision: int,
) -> Tuple[Optional[int], Optional[Dict[str, Any]], bool]:
    try:
        base_url, api_key = _cloud_config(conn)
    except RuntimeError:
        return None, None, True

    payload = _build_commit_request(conn, plan, expected_menu_revision=expected_menu_revision)
    try:
        import requests

        response = requests.post(
            _commit_url(base_url),
            json=payload,
            headers=_auth_headers(conn, api_key),
            timeout=60,
        )
        body = response.json() if response.content else None
        if not isinstance(body, dict):
            body = None
        if response.status_code >= 400:
            from src.core.central_api import error_from_response

            error_from_response(response, conn=conn)
        return response.status_code, body, False
    except Exception:
        logger.exception("Menu mutation commit POST failed for %s", plan.mutation_id)
        return None, None, True


def _get_mutation_status(
    conn,
    mutation_id: str,
) -> Tuple[Optional[int], Optional[Dict[str, Any]], bool]:
    try:
        base_url, api_key = _cloud_config(conn)
    except RuntimeError:
        return None, None, True

    try:
        import requests

        response = requests.get(
            _status_url(base_url, mutation_id),
            headers=_auth_headers(conn, api_key),
            timeout=60,
        )
        body = response.json() if response.content else None
        if not isinstance(body, dict):
            body = None
        if response.status_code >= 400:
            from src.core.central_api import error_from_response

            error_from_response(response, conn=conn)
        return response.status_code, body, False
    except Exception:
        logger.exception("Menu mutation status GET failed for %s", mutation_id)
        return None, None, True


def _apply_catalog_delta(conn, catalog_delta: Any) -> None:
    if not isinstance(catalog_delta, dict):
        return
    variant_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(variants)").fetchall()
        if len(row) > 1
    }
    for item in catalog_delta.get("items") or []:
        if not isinstance(item, dict):
            continue
        menu_item_id = str(item.get("menu_item_id") or "").strip()
        if not menu_item_id:
            continue
        name = item.get("name")
        item_type = item.get("type")
        is_verified = 1 if item.get("is_verified") else 0
        conn.execute(
            """
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(menu_item_id) DO UPDATE SET
                name = COALESCE(excluded.name, menu_items.name),
                type = COALESCE(excluded.type, menu_items.type),
                is_verified = COALESCE(excluded.is_verified, menu_items.is_verified),
                updated_at = CURRENT_TIMESTAMP
            """,
            (menu_item_id, name, item_type, is_verified),
        )
    for variant in catalog_delta.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        variant_id = str(variant.get("variant_id") or "").strip()
        if not variant_id:
            continue
        variant_name = variant.get("variant_name") or variant_id
        is_verified = 1 if variant.get("is_verified", True) else 0
        insert_columns = ["variant_id", "variant_name", "is_verified"]
        values = [variant_id, variant_name, is_verified]
        update_parts = [
            "variant_name = COALESCE(excluded.variant_name, variants.variant_name)",
            "is_verified = COALESCE(excluded.is_verified, variants.is_verified)",
        ]
        for optional_column in ("description", "unit", "value"):
            if optional_column not in variant_columns:
                continue
            insert_columns.append(optional_column)
            values.append(variant.get(optional_column))
            update_parts.append(
                f"{optional_column} = COALESCE(excluded.{optional_column}, variants.{optional_column})"
            )
        if "updated_at" in variant_columns:
            update_parts.append("updated_at = CURRENT_TIMESTAMP")
        placeholders = ", ".join("?" for _ in insert_columns)
        conn.execute(
            f"""
            INSERT INTO variants ({", ".join(insert_columns)})
            VALUES ({placeholders})
            ON CONFLICT(variant_id) DO UPDATE SET
                {", ".join(update_parts)}
            """,
            values,
        )
