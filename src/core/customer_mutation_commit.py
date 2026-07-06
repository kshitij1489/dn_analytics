"""
Server-authoritative customer mutation commit (strict mode).

Capture local edits in a rolled-back transaction, POST one mutation to the server,
then apply the accepted response through the existing pull appliers.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.customer_merge_sync import (
    apply_remote_customer_merge_event,
    apply_remote_customer_undo_event,
    get_customer_merge_pull_cursor,
    get_customer_merge_pull_endpoint,
    pull_and_apply_customer_merge_events,
    set_customer_merge_pull_cursor,
)
from src.core.customer_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
)
from src.core.sync_identity import (
    apply_customer_scope_state,
    extract_customer_scope_state,
    get_customer_state_revision,
    get_customer_strict_mode_enabled,
    get_sync_attribution,
    set_customer_state_revision,
)

logger = logging.getLogger(__name__)

DEFAULT_SCOPE_KEY = "default"
MAX_COMMIT_ATTEMPTS = 2
MAX_PULL_PAGES_PER_STREAM = 1000
COMMIT_SCHEMA_VERSION = 1
NETWORK_ERROR_MESSAGE = "Customer merge requires a cloud connection."
CUSTOMER_CONFLICT_USER_MESSAGE = (
    "Customers changed on another installation. Sync latest data and try again."
)
STRICT_MODE_NOT_READY_MESSAGE = "Customer merging requires a cloud connection."
LOCAL_APPLY_FAILED_MESSAGE = "Server accepted the change, but local refresh is required — run Sync DB."
RETRY_AFTER_RECONCILE = "retry_after_reconcile"

MUTATION_TYPE_CUSTOMER_MERGE_APPLIED = "customer_merge.applied"
MUTATION_TYPE_CUSTOMER_MERGE_UNDONE = "customer_merge.undone"


@dataclass
class MutationPlan:
    mutation_id: str
    mutation_type: str
    event: Optional[Dict[str, Any]]
    customer_keys: List[str] = field(default_factory=list)
    expected_customer_revision: Optional[int] = None


@dataclass
class CommitResult:
    status: str
    message: str = ""
    merge_id: Optional[int] = None
    conflict: Optional[Dict[str, Any]] = None


class CustomerStatePullError(RuntimeError):
    """The local customer merge state could not be brought to a safe server revision."""


def _commit_error_from_pull_failure(exc: CustomerStatePullError) -> CommitResult:
    """Surface the pull failure reason (data/config), not a generic connectivity message."""
    message = str(exc).strip() or NETWORK_ERROR_MESSAGE
    return CommitResult(status="error", message=message)


def strict_mode_ready(conn) -> bool:
    """
    True when cloud sync is configured and the server has advertised customer_revision
    at least once. Older servers that omit revision keep strict mode disabled.
    """
    base_url, api_key = get_cloud_sync_config(conn)
    if not base_url or not api_key:
        return False
    return get_customer_state_revision(conn) is not None


def strict_mode_active(conn) -> bool:
    """True when the server-advertised strict flag is on and readiness holds."""
    return get_customer_strict_mode_enabled(conn) and strict_mode_ready(conn)


def strict_mode_editing_blocked(conn) -> bool:
    """True when the server advertises strict mode but local readiness is missing."""
    return get_customer_strict_mode_enabled(conn) and not strict_mode_ready(conn)


def strict_mode_edit_blocked_response(conn) -> Optional[Dict[str, Any]]:
    """Return a blocked-response dict when strict mode is on but not ready."""
    if strict_mode_editing_blocked(conn):
        return {
            "status": "error",
            "message": STRICT_MODE_NOT_READY_MESSAGE,
            "code": "strict_mode_not_ready",
        }
    return None


def extract_customer_keys_from_event(event: Dict[str, Any]) -> List[str]:
    """Deduplicated non-null phone_hash / name_address_hash values from an event payload."""
    keys: List[str] = []
    for side in ("source_customer", "target_customer"):
        descriptor = event.get(side) if isinstance(event.get(side), dict) else {}
        locators = descriptor.get("portable_locators") if isinstance(descriptor.get("portable_locators"), dict) else {}
        for field_name in ("phone_hash", "name_address_hash"):
            value = locators.get(field_name)
            if value:
                keys.append(str(value))
    return list(dict.fromkeys(keys))


def build_plan(
    *,
    mutation_type: str,
    event: Optional[Dict[str, Any]] = None,
    customer_keys: Optional[List[str]] = None,
    mutation_id: Optional[str] = None,
) -> MutationPlan:
    """Assemble a mutation plan; mutation_id is generated once and reused across retries."""
    keys = list(customer_keys) if customer_keys is not None else []
    if not keys and isinstance(event, dict):
        keys = extract_customer_keys_from_event(event)
    return MutationPlan(
        mutation_id=mutation_id or str(uuid.uuid4()),
        mutation_type=mutation_type,
        event=event,
        customer_keys=keys,
    )


def commit_mutation(conn, plan: MutationPlan) -> CommitResult:
    """POST the mutation with auto-retry on stale revision and reconcile on timeout."""
    last_conflict: Optional[Dict[str, Any]] = None
    for attempt in range(MAX_COMMIT_ATTEMPTS):
        revision = (
            plan.expected_customer_revision
            if attempt == 0 and plan.expected_customer_revision is not None
            else get_customer_state_revision(conn)
        )
        if revision is None:
            return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)

        status_code, body, transport_error = _post_commit(conn, plan, expected_customer_revision=revision)
        if transport_error:
            reconcile = _reconcile_after_timeout(conn, plan, expected_customer_revision=revision)
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
            )

        if status_code == 409 and isinstance(body, dict):
            last_conflict = body
            try:
                pull_latest_customer_state(conn)
            except CustomerStatePullError as exc:
                return _commit_error_from_pull_failure(exc)
            if plan_overlaps_pulled_changes(plan, body.get("conflicting_events")):
                return CommitResult(
                    status="conflict",
                    message=str(body.get("error") or "Customer state conflict"),
                    conflict=body,
                )
            continue

        if status_code and status_code >= 500:
            reconcile = _reconcile_after_timeout(conn, plan, expected_customer_revision=revision)
            if reconcile is not None:
                if reconcile.status == RETRY_AFTER_RECONCILE:
                    continue
                return reconcile
            return CommitResult(status="error", message=NETWORK_ERROR_MESSAGE)

        error_message = NETWORK_ERROR_MESSAGE
        if isinstance(body, dict) and body.get("error"):
            error_message = str(body["error"])
        return CommitResult(status="error", message=error_message)

    return CommitResult(
        status="conflict",
        message=str((last_conflict or {}).get("error") or "Customer state conflict"),
        conflict=last_conflict,
    )


def _validate_accepted_response(body: Dict[str, Any], plan: MutationPlan) -> None:
    """Reject incomplete or mismatched commit responses before local mutation."""
    if body.get("status") != "accepted":
        raise ValueError("Commit response is not accepted.")
    if str(body.get("mutation_id") or "") != plan.mutation_id:
        raise ValueError("Commit response mutation_id does not match the request.")
    if body.get("customer_revision") is None:
        raise ValueError("Commit response is missing customer_revision.")

    if not isinstance(plan.event, dict) or not plan.event.get("remote_event_id"):
        raise ValueError("Mutation plan contains an event without remote_event_id.")

    accepted_rows = body.get("accepted_events")
    if not isinstance(accepted_rows, list):
        raise ValueError("Commit response accepted_events must be a list.")

    expected_remote_id = str(plan.event["remote_event_id"])
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
    if accepted_remote_ids != {expected_remote_id}:
        raise ValueError("Commit response events do not match the mutation plan.")


def apply_accepted(conn, body: Dict[str, Any], plan: MutationPlan) -> Dict[str, Any]:
    """Apply a server-accepted mutation in one SQLite transaction via pull appliers."""
    _validate_accepted_response(body, plan)
    accepted_by_remote_id = {
        str(row.get("remote_event_id")): row
        for row in (body.get("accepted_events") or [])
        if isinstance(row, dict) and row.get("remote_event_id")
    }
    local_merge_id: Optional[int] = None

    try:
        if plan.event:
            remote_event_id = str(plan.event.get("remote_event_id") or "")
            accepted = accepted_by_remote_id.get(remote_event_id, {})
            event = dict(plan.event)
            if accepted.get("server_seq") is not None:
                event["server_seq"] = accepted["server_seq"]
            if accepted.get("server_ingested_at") is not None:
                event["server_ingested_at"] = accepted["server_ingested_at"]

            if plan.mutation_type == MUTATION_TYPE_CUSTOMER_MERGE_UNDONE:
                result = apply_remote_customer_undo_event(conn, event, body.get("customer_merge_cursor"))
            else:
                result = apply_remote_customer_merge_event(conn, event, body.get("customer_merge_cursor"))
            local_merge_id = result.get("local_merge_id")

        _advance_pull_cursor(conn, body.get("customer_merge_cursor"))

        if body.get("customer_revision") is not None:
            set_customer_state_revision(conn, int(body["customer_revision"]))
        scope_state = extract_customer_scope_state(body)
        if scope_state:
            apply_customer_scope_state(conn, scope_state)

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {"local_merge_id": local_merge_id}


def pull_latest_customer_state(conn, *, already_locked: bool = False) -> Dict[str, Any]:
    """
    Drain the customer merge pull stream until no new events remain.

    Serializes through CLOUD_PULL_LOCK (plan C5.1) unless already_locked=True,
    mirroring pull_latest_menu_state so commit-path pulls cannot interleave
    with a scheduler or Sync DB pull cycle.
    """
    from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK

    acquired = False
    if not already_locked:
        CLOUD_PULL_LOCK.acquire(blocking=True)
        acquired = True
    try:
        return _pull_latest_customer_state_locked(conn)
    finally:
        if acquired:
            CLOUD_PULL_LOCK.release()


def _pull_latest_customer_state_locked(conn) -> Dict[str, Any]:
    _base_url, api_key = get_cloud_sync_config(conn)
    endpoint = get_customer_merge_pull_endpoint(conn)
    if not endpoint or not api_key:
        raise CustomerStatePullError("Customer merge pull must be configured.")

    last_stats: Dict[str, Any] = {}
    for _page in range(MAX_PULL_PAGES_PER_STREAM):
        last_stats = pull_and_apply_customer_merge_events(conn, endpoint, auth=api_key)
        if last_stats.get("error"):
            raise CustomerStatePullError(str(last_stats["error"]))
        if not last_stats.get("has_more"):
            return last_stats
    raise CustomerStatePullError("Customer merge pull exceeded the page safety limit.")


def plan_overlaps_pulled_changes(plan: MutationPlan, conflicting_events: Any) -> bool:
    """
    Return True for a real overlap or when the server cannot prove non-overlap.

    Missing conflict metadata must fail closed; otherwise a legacy revision bump
    without a complete event list could cause a stale plan to be retried.
    """
    if not isinstance(conflicting_events, list) or not conflicting_events:
        return True
    plan_keys = {str(key) for key in plan.customer_keys if str(key).strip()}
    if not plan_keys:
        return True
    for event in conflicting_events:
        if not isinstance(event, dict) or not isinstance(event.get("customer_keys"), list):
            return True
        for customer_key in event["customer_keys"]:
            if str(customer_key) in plan_keys:
                return True
    return False


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


def build_customer_edit_http_exception(res: Dict[str, Any]):
    """Return an HTTPException for non-success customer edit responses, else None."""
    from fastapi import HTTPException

    status = res.get("status")
    if status == "success":
        return None
    if status == "conflict":
        conflicting = res.get("conflicting_events") or []
        return HTTPException(
            status_code=409,
            detail={
                "message": CUSTOMER_CONFLICT_USER_MESSAGE,
                "current_customer_revision": res.get("current_customer_revision"),
                "conflicting_events": conflicting,
                "attribution": extract_conflict_attribution(conflicting),
            },
        )
    if res.get("code") == "strict_mode_not_ready" or (
        status == "error" and res.get("message") == STRICT_MODE_NOT_READY_MESSAGE
    ):
        return HTTPException(status_code=503, detail=STRICT_MODE_NOT_READY_MESSAGE)
    if status == "error":
        message = str(res.get("message") or "Customer edit failed")
        if message == NETWORK_ERROR_MESSAGE:
            return HTTPException(status_code=503, detail=message)
        if message == LOCAL_APPLY_FAILED_MESSAGE:
            return HTTPException(status_code=500, detail=message)
        return HTTPException(status_code=400, detail=message)
    return HTTPException(status_code=400, detail="Unexpected customer edit response")


def commit_result_to_customer_response(
    result: CommitResult,
    *,
    success_message: str,
    success_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Map CommitResult into the dict shape returned by customer merge query functions."""
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
            "message": CUSTOMER_CONFLICT_USER_MESSAGE,
            "current_customer_revision": conflict.get("current_customer_revision"),
            "conflicting_events": conflict.get("conflicting_events"),
        }
    return {"status": "error", "message": result.message or NETWORK_ERROR_MESSAGE}


def _reconcile_after_timeout(
    conn,
    plan: MutationPlan,
    *,
    expected_customer_revision: int,
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
        return CommitResult(status="ok", message="accepted", merge_id=apply_result.get("local_merge_id"))

    if status_code != 404:
        return None

    retry_code, retry_body, retry_error = _post_commit(conn, plan, expected_customer_revision=expected_customer_revision)
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
        return CommitResult(status="ok", message="accepted", merge_id=apply_result.get("local_merge_id"))
    if retry_code == 409 and isinstance(retry_body, dict):
        try:
            pull_latest_customer_state(conn)
        except CustomerStatePullError as exc:
            return _commit_error_from_pull_failure(exc)
        if plan_overlaps_pulled_changes(plan, retry_body.get("conflicting_events")):
            return CommitResult(
                status="conflict",
                message=str(retry_body.get("error") or "Customer state conflict"),
                conflict=retry_body,
            )
        return CommitResult(status=RETRY_AFTER_RECONCILE, message="retry")
    return None


def _handle_local_apply_failed(conn, *, mutation_id: str, context: str) -> CommitResult:
    """Server accepted the mutation but local apply failed — reset cursor and replay."""
    logger.exception("%s: %s", context, mutation_id)
    try:
        set_customer_merge_pull_cursor(conn, None)
        conn.commit()
    except Exception:
        conn.rollback()
    try:
        pull_latest_customer_state(conn)
        logger.info(
            "Replayed customer merge stream after local apply failure for %s",
            mutation_id,
        )
    except Exception:
        logger.exception(
            "Customer pull replay failed after local apply failure for %s",
            mutation_id,
        )
    return CommitResult(status="error", message=LOCAL_APPLY_FAILED_MESSAGE)


def _cloud_config(conn) -> Tuple[str, str]:
    base_url, api_key = get_cloud_sync_config(conn)
    if not base_url or not api_key:
        raise RuntimeError(NETWORK_ERROR_MESSAGE)
    return base_url.rstrip("/"), api_key


def _commit_url(base_url: str) -> str:
    return f"{base_url}/desktop-analytics-sync/customer-mutations/commit"


def _status_url(base_url: str, mutation_id: str) -> str:
    return f"{base_url}/desktop-analytics-sync/customer-mutations/{mutation_id}"


def _auth_headers(api_key: str) -> Dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def _build_commit_request(conn, plan: MutationPlan, *, expected_customer_revision: int) -> Dict[str, Any]:
    attribution = get_sync_attribution(conn)
    return {
        "schema_version": COMMIT_SCHEMA_VERSION,
        "mutation_id": plan.mutation_id,
        "mutation_type": plan.mutation_type,
        "expected_customer_revision": expected_customer_revision,
        "event": plan.event,
        "uploaded_by": attribution.get("employee") or None,
        "uploaded_from": attribution.get("device") or None,
        "scope_key": DEFAULT_SCOPE_KEY,
    }


def _post_commit(
    conn,
    plan: MutationPlan,
    *,
    expected_customer_revision: int,
) -> Tuple[Optional[int], Optional[Dict[str, Any]], bool]:
    try:
        base_url, api_key = _cloud_config(conn)
    except RuntimeError:
        return None, None, True

    payload = _build_commit_request(conn, plan, expected_customer_revision=expected_customer_revision)
    try:
        import requests

        response = requests.post(
            _commit_url(base_url),
            json=payload,
            headers=_auth_headers(api_key),
            timeout=60,
        )
        body = response.json() if response.content else None
        if not isinstance(body, dict):
            body = None
        return response.status_code, body, False
    except Exception:
        logger.exception("Customer mutation commit POST failed for %s", plan.mutation_id)
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
            headers=_auth_headers(api_key),
            params={"scope_key": DEFAULT_SCOPE_KEY},
            timeout=60,
        )
        body = response.json() if response.content else None
        if not isinstance(body, dict):
            body = None
        return response.status_code, body, False
    except Exception:
        logger.exception("Customer mutation status GET failed for %s", mutation_id)
        return None, None, True


def _advance_pull_cursor(conn, new_cursor: Optional[str]) -> None:
    if not new_cursor:
        return
    current = get_customer_merge_pull_cursor(conn)
    if str(new_cursor) == str(current):
        return
    from src.core.sync_cursor import pull_cursor_is_ahead

    if pull_cursor_is_ahead(str(new_cursor), None if current is None else str(current)):
        set_customer_merge_pull_cursor(conn, str(new_cursor))
