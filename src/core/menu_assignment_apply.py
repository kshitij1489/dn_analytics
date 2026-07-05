"""
Per-order-item assignment extraction and apply (plan §2.2–§2.3, Phase C3).

The durable truth of menu clustering is row-shaped: order_item_id →
(menu_item_id, variant_id, is_verified). This module turns any menu merge
event — schema v2 with an explicit "assignments" array, or legacy v1 via its
history_payload — into that row shape, and applies it with a per-row
server_seq guard so replay is idempotent and order-independent.

Extraction semantics are shared with the Dachnona server
(desktop_analytics_app_sync/services/assignment_state.py) and pinned by
contracts/menu_merge_event_fixtures.json in both repos.

Normalized assignment dicts always carry order_item_id and menu_item_id.
The variant_id / is_verified keys are OMITTED when the event does not specify
them (the applier then leaves the current value untouched); variant_id None
means the SQL NULL variant.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

from src.core.menu_assignment_schema import ensure_assignment_sync_schema


logger = logging.getLogger(__name__)

NULL_VARIANT_SENTINEL = "__NULL_VARIANT__"
EVENT_TYPE_UNDONE = "menu_merge.undone"

MENU_SYNC_ASSIGNMENT_APPLY_ENV = "MENU_SYNC_ASSIGNMENT_APPLY"


def is_assignment_apply_enabled() -> bool:
    """
    Feature flag for the assignment-based applier (plan rollout step 3).
    Default on; the env var is the emergency off switch for one release.
    """
    raw = os.environ.get(MENU_SYNC_ASSIGNMENT_APPLY_ENV)
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _normalize_variant_value(variant_id: Any) -> Optional[str]:
    """Map the wire variant value to a local column value (None = SQL NULL)."""
    if variant_id in (None, "None", NULL_VARIANT_SENTINEL):
        return None
    return str(variant_id)


def coerce_server_seq(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Extraction (shared with server; see contracts/menu_merge_event_fixtures.json)
# ---------------------------------------------------------------------------


def _normalize_explicit_assignments(raw: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        order_item_id = str(entry.get("order_item_id") or "").strip()
        menu_item_id = str(entry.get("menu_item_id") or "").strip()
        if not order_item_id or not menu_item_id:
            continue
        normalized: Dict[str, Any] = {
            "order_item_id": order_item_id,
            "menu_item_id": menu_item_id,
        }
        if "variant_id" in entry:
            normalized["variant_id"] = _normalize_variant_value(entry.get("variant_id"))
        if "is_verified" in entry and entry.get("is_verified") is not None:
            try:
                normalized["is_verified"] = 1 if int(entry["is_verified"]) else 0
            except (TypeError, ValueError):
                pass
        out.append(normalized)
    return out


def extract_assignments(event: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """
    Return the normalized assignment list for a menu merge event, or None when
    the event carries no explicit assignments and none can be derived from its
    v1 history_payload (caller falls back to legacy replay and logs loudly).

    For menu_merge.undone events the result is the PRIOR assignments: v2 undo
    events carry them explicitly; v1 undo events derive them from the old_*
    fields / the source side of the operation.
    """
    if not isinstance(event, dict):
        return None
    merge_payload = event.get("merge_payload")
    if not isinstance(merge_payload, dict):
        return None

    raw_assignments = merge_payload.get("assignments")
    if isinstance(raw_assignments, list):
        return _normalize_explicit_assignments(raw_assignments)

    undo = str(event.get("event_type") or "").strip() == EVENT_TYPE_UNDONE

    history = merge_payload.get("history_payload")
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except json.JSONDecodeError:
            history = None

    kind = str(merge_payload.get("kind") or "").strip()
    if not kind and isinstance(history, dict):
        kind = str(history.get("kind") or "").strip()

    source_item = event.get("source_item")
    target_item = event.get("target_item")
    source_id = str((source_item or {}).get("menu_item_id") or "").strip() if isinstance(source_item, dict) else ""
    target_id = str((target_item or {}).get("menu_item_id") or "").strip() if isinstance(target_item, dict) else ""

    if kind == "basic_merge_v1":
        if isinstance(history, dict):
            affected_ids = history.get("affected_order_item_ids")
        elif isinstance(history, list):
            # Oldest payload shape: bare list of order_item_ids.
            affected_ids = history
        else:
            affected_ids = None
        if not isinstance(affected_ids, list):
            return None
        destination = source_id if undo else target_id
        if not destination:
            return None
        return [
            {"order_item_id": str(order_item_id).strip(), "menu_item_id": destination}
            for order_item_id in affected_ids
            if str(order_item_id or "").strip()
        ]

    if kind == "variant_merge_v1":
        mapping_rows = history.get("mapping_rows") if isinstance(history, dict) else None
        if not isinstance(mapping_rows, list):
            return None
        destination = source_id if undo else target_id
        if not destination:
            return None
        out: List[Dict[str, Any]] = []
        for row in mapping_rows:
            if not isinstance(row, dict):
                continue
            order_item_id = str(row.get("order_item_id") or "").strip()
            if not order_item_id:
                continue
            variant_value = row.get("old_variant_id") if undo else row.get("new_variant_id")
            out.append(
                {
                    "order_item_id": order_item_id,
                    "menu_item_id": destination,
                    "variant_id": _normalize_variant_value(variant_value),
                }
            )
        return out

    if kind == "resolution_variant_v1":
        mapping_rows = history.get("mapping_rows") if isinstance(history, dict) else None
        if not isinstance(mapping_rows, list):
            return None
        out = []
        for row in mapping_rows:
            if not isinstance(row, dict):
                continue
            order_item_id = str(row.get("order_item_id") or "").strip()
            if not order_item_id:
                continue
            if undo:
                menu_item_id = str(row.get("old_menu_item_id") or source_id or "").strip()
                variant_value = row.get("old_variant_id")
                verified = row.get("old_is_verified", 0)
            else:
                menu_item_id = str(row.get("new_menu_item_id") or target_id or "").strip()
                variant_value = row.get("new_variant_id")
                verified = row.get("new_is_verified", 1)
            if not menu_item_id:
                continue
            try:
                verified_int = 1 if int(verified) else 0
            except (TypeError, ValueError):
                verified_int = 0 if undo else 1
            out.append(
                {
                    "order_item_id": order_item_id,
                    "menu_item_id": menu_item_id,
                    "variant_id": _normalize_variant_value(variant_value),
                    "is_verified": verified_int,
                }
            )
        return out

    return None


# ---------------------------------------------------------------------------
# Snapshot backfill helpers (shared by legacy replay and assignment apply)
# ---------------------------------------------------------------------------


def ensure_menu_item_exists(conn, item: Dict[str, Any]) -> None:
    menu_item_id = str(item.get("menu_item_id") or "").strip()
    name = item.get("name")
    item_type = item.get("type")
    if not menu_item_id or not name or not item_type:
        return

    row = conn.execute(
        "SELECT 1 FROM menu_items WHERE menu_item_id = ? LIMIT 1",
        (menu_item_id,),
    ).fetchone()
    if row:
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO menu_items (menu_item_id, name, type, is_verified)
        VALUES (?, ?, ?, ?)
        """,
        (
            menu_item_id,
            str(name),
            str(item_type),
            1 if item.get("is_verified") else 0,
        ),
    )


def ensure_variant_exists(conn, variant_id: Any, variant_name: Any) -> None:
    normalized_variant_id = _normalize_variant_value(variant_id)
    if normalized_variant_id is None:
        return

    row = conn.execute(
        "SELECT 1 FROM variants WHERE variant_id = ? LIMIT 1",
        (normalized_variant_id,),
    ).fetchone()
    if row:
        return

    normalized_variant_name = str(variant_name or "").strip()
    if not normalized_variant_name:
        normalized_variant_name = normalized_variant_id

    conn.execute(
        """
        INSERT OR IGNORE INTO variants (variant_id, variant_name, is_verified)
        VALUES (?, ?, 1)
        """,
        (normalized_variant_id, normalized_variant_name),
    )


def _build_item_snapshots(event: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    snapshots: Dict[str, Dict[str, Any]] = {}
    for key in ("source_item", "target_item"):
        item = event.get(key)
        if isinstance(item, dict):
            menu_item_id = str(item.get("menu_item_id") or "").strip()
            if menu_item_id:
                snapshots[menu_item_id] = item
    return snapshots


def _build_variant_names(event: Dict[str, Any]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    merge_payload = event.get("merge_payload")
    if not isinstance(merge_payload, dict):
        return names

    def note(variant_id: Any, variant_name: Any) -> None:
        normalized = _normalize_variant_value(variant_id)
        if normalized and variant_name:
            names[normalized] = str(variant_name)

    mappings = merge_payload.get("variant_mappings")
    if isinstance(mappings, list):
        for mapping in mappings:
            if isinstance(mapping, dict):
                note(mapping.get("source_variant_id"), mapping.get("source_variant_name"))
                note(mapping.get("target_variant_id"), mapping.get("target_variant_name"))

    resolution = merge_payload.get("resolution")
    if isinstance(resolution, dict):
        note(resolution.get("source_variant_id"), resolution.get("source_variant_name"))
        note(resolution.get("target_variant_id"), resolution.get("target_variant_name"))

    return names


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _row_authored_locally(conn, assignment_seq: Any) -> bool:
    """
    True when the event that last wrote this row (by server_seq) was one of our
    own — i.e. its remote_event_id exists in our outbox. Used to notify the
    local user when a peer's later decision overwrites theirs.
    """
    seq = coerce_server_seq(assignment_seq)
    if seq is None:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM menu_merge_remote_events r
        JOIN menu_merge_sync_events s ON s.event_id = r.remote_event_id
        WHERE r.server_seq = ?
        LIMIT 1
        """,
        (seq,),
    ).fetchone()
    return row is not None


def insert_supersede_notice(
    conn,
    order_item_id: str,
    local_merge_id: Optional[int],
    superseded_by_event_id: Optional[str],
    attribution: Any,
) -> None:
    ensure_assignment_sync_schema(conn)
    attribution_text = None
    if attribution is not None:
        attribution_text = json.dumps(attribution, sort_keys=True, default=str)
    conn.execute(
        """
        INSERT INTO menu_sync_supersede_notices (
            order_item_id, local_merge_id, superseded_by_event_id, attribution
        )
        VALUES (?, ?, ?, ?)
        """,
        (str(order_item_id), local_merge_id, superseded_by_event_id, attribution_text),
    )


def list_supersede_notices(conn, include_acknowledged: bool = False) -> List[Dict[str, Any]]:
    ensure_assignment_sync_schema(conn)
    where = "" if include_acknowledged else "WHERE acknowledged_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT notice_id, order_item_id, local_merge_id, superseded_by_event_id,
               attribution, created_at, acknowledged_at
        FROM menu_sync_supersede_notices
        {where}
        ORDER BY created_at DESC, notice_id DESC
        """
    ).fetchall()
    notices = []
    for row in rows:
        notice = dict(row)
        if notice.get("attribution"):
            try:
                notice["attribution"] = json.loads(notice["attribution"])
            except (TypeError, json.JSONDecodeError):
                pass
        notices.append(notice)
    return notices


def acknowledge_supersede_notice(conn, notice_id: int) -> bool:
    ensure_assignment_sync_schema(conn)
    cur = conn.execute(
        """
        UPDATE menu_sync_supersede_notices
        SET acknowledged_at = CURRENT_TIMESTAMP
        WHERE notice_id = ? AND acknowledged_at IS NULL
        """,
        (int(notice_id),),
    )
    return bool(cur.rowcount)


def lookup_event_by_server_seq(conn, server_seq: Any) -> Optional[Dict[str, Any]]:
    seq = coerce_server_seq(server_seq)
    if seq is None:
        return None
    row = conn.execute(
        """
        SELECT remote_event_id, payload
        FROM menu_merge_remote_events
        WHERE server_seq = ?
        LIMIT 1
        """,
        (seq,),
    ).fetchone()
    if not row:
        return None
    result = {"remote_event_id": str(row["remote_event_id"]), "attribution": None}
    try:
        payload = json.loads(row["payload"])
        if isinstance(payload, dict):
            result["attribution"] = payload.get("attribution")
    except (TypeError, json.JSONDecodeError):
        pass
    return result


def apply_assignments(
    conn,
    assignments: List[Dict[str, Any]],
    server_seq: Optional[int],
    event: Dict[str, Any],
    detect_supersede: bool = True,
    write_is_verified: bool = False,
) -> Dict[str, Any]:
    """
    Apply normalized assignments with the per-row seq guard (plan §2.3 step 2).

    Rows whose assignment_seq is already >= server_seq are skipped and reported
    in "stale_rows" (the echo/ack path uses that to emit supersede notices).
    Rows that were pending_local, or whose last write came from one of our own
    events, and whose value actually changes, are reported in "superseded" —
    the caller turns those into user-visible notices when detect_supersede.

    is_verified is seeded on a brand-new row but, by default, left untouched on
    an existing one: on the incremental merge stream the verification stream is
    the flag's sole owner (guarded by verification_seq), so rewriting it here
    would let merge vs verification apply order diverge it across installs. The
    snapshot bootstrap / force-reseed adopt the server's authoritative full
    state — including the materialized flag — so they pass write_is_verified so
    the flag lands on rows this install already had from POS ingest.

    Does not commit; the caller owns the per-event transaction.
    """
    ensure_assignment_sync_schema(conn)

    snapshots = _build_item_snapshots(event)
    variant_names = _build_variant_names(event)

    stats: Dict[str, Any] = {
        "rows_applied": 0,
        "rows_missing": 0,
        "stale_rows": [],
        "superseded": [],
        "touched_menu_item_ids": set(),
    }

    for assignment in assignments:
        order_item_id = str(assignment.get("order_item_id") or "").strip()
        menu_item_id = str(assignment.get("menu_item_id") or "").strip()
        if not order_item_id or not menu_item_id:
            continue

        variant_specified = "variant_id" in assignment
        variant_value = assignment.get("variant_id") if variant_specified else None
        verify_specified = "is_verified" in assignment
        verify_value = assignment.get("is_verified") if verify_specified else None

        row = conn.execute(
            """
            SELECT menu_item_id, variant_id, is_verified, assignment_seq, pending_local
            FROM menu_item_variants
            WHERE order_item_id = ?
            LIMIT 1
            """,
            (order_item_id,),
        ).fetchone()

        if row is not None:
            row_seq = coerce_server_seq(row["assignment_seq"])
            if server_seq is not None and row_seq is not None and row_seq >= server_seq:
                stats["stale_rows"].append(
                    {"order_item_id": order_item_id, "assignment_seq": row_seq}
                )
                continue

        snapshot = snapshots.get(menu_item_id)
        if snapshot:
            ensure_menu_item_exists(conn, snapshot)
        if variant_specified and variant_value is not None:
            ensure_variant_exists(conn, variant_value, variant_names.get(variant_value))

        if row is not None:
            # is_verified is deliberately NOT part of the change set on an
            # existing row: the merge stream reassigns the mapping, but the
            # verification stream is the sole owner of the flag (guarded by its
            # own verification_seq). Rewriting is_verified here would let merge
            # vs verification apply order diverge the flag across installs.
            changes = (
                str(row["menu_item_id"]) != menu_item_id
                or (variant_specified and row["variant_id"] != variant_value)
            )
            # A pending_local row is overwritten silently: server order will
            # judge, and if our own event lands later its echo re-asserts our
            # value (plan §2.3 step 2). Notify only when a peer overwrites an
            # already-acknowledged local decision.
            if (
                detect_supersede
                and changes
                and not int(row["pending_local"] or 0)
                and _row_authored_locally(conn, row["assignment_seq"])
            ):
                stats["superseded"].append({"order_item_id": order_item_id})

            set_clauses = ["menu_item_id = ?"]
            params: List[Any] = [menu_item_id]
            if variant_specified:
                set_clauses.append("variant_id = ?")
                params.append(variant_value)
            if write_is_verified and verify_specified:
                set_clauses.append("is_verified = ?")
                params.append(int(verify_value or 0))
            set_clauses += ["assignment_seq = ?", "pending_local = 0", "updated_at = CURRENT_TIMESTAMP"]
            params.append(server_seq)
            conn.execute(
                f"UPDATE menu_item_variants SET {', '.join(set_clauses)} WHERE order_item_id = ?",
                params + [order_item_id],
            )
            stats["touched_menu_item_ids"].add(str(row["menu_item_id"]))
        else:
            order_row = conn.execute(
                "SELECT 1 FROM order_items WHERE order_item_id = ? LIMIT 1",
                (order_item_id,),
            ).fetchone()
            if order_row is None:
                # This install has never seen the order item; nothing to move.
                stats["rows_missing"] += 1
                continue
            conn.execute(
                """
                INSERT INTO menu_item_variants (
                    order_item_id, menu_item_id, variant_id, is_verified,
                    assignment_seq, pending_local
                )
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (
                    order_item_id,
                    menu_item_id,
                    variant_value,
                    int(verify_value) if verify_specified and verify_value is not None else 1,
                    server_seq,
                ),
            )

        if variant_specified:
            conn.execute(
                """
                UPDATE order_items
                SET menu_item_id = ?, variant_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE order_item_id = ?
                """,
                (menu_item_id, variant_value, order_item_id),
            )
            conn.execute(
                """
                UPDATE order_item_addons
                SET menu_item_id = ?, variant_id = ?
                WHERE order_item_id = ?
                """,
                (menu_item_id, variant_value, order_item_id),
            )
        else:
            conn.execute(
                """
                UPDATE order_items
                SET menu_item_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE order_item_id = ?
                """,
                (menu_item_id, order_item_id),
            )
            conn.execute(
                """
                UPDATE order_item_addons
                SET menu_item_id = ?
                WHERE order_item_id = ?
                """,
                (menu_item_id, order_item_id),
            )

        stats["touched_menu_item_ids"].add(menu_item_id)
        stats["rows_applied"] += 1

    return stats
