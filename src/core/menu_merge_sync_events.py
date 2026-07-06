"""
Append-only sync events for menu merge / resolution history.
"""

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from src.core.menu_assignment_schema import ensure_assignment_sync_schema
from src.core.menu_sync_quarantine import ensure_menu_sync_quarantine_table
from src.core.sync_identity import get_sync_attribution
from utils import menu_utils


# v2 events additionally carry merge_payload.assignments (explicit per-order-item
# rows) so peers can apply them without the source cluster still existing.
SCHEMA_VERSION = 2
EVENT_TYPE_APPLIED = "menu_merge.applied"
EVENT_TYPE_UNDONE = "menu_merge.undone"


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_menu_merge_sync_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_merge_sync_events (
            event_id TEXT PRIMARY KEY,
            merge_id INTEGER,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            upload_attempted_at TEXT,
            uploaded_at TEXT,
            last_error TEXT,
            CHECK (event_type IN ('menu_merge.applied', 'menu_merge.undone'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_merge_remote_events (
            remote_event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            reverts_remote_event_id TEXT,
            local_merge_id INTEGER,
            payload TEXT NOT NULL,
            remote_cursor TEXT,
            occurred_at TEXT,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK (event_type IN ('menu_merge.applied', 'menu_merge.undone'))
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_menu_merge_sync_events_pending
        ON menu_merge_sync_events(uploaded_at, occurred_at, created_at);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_menu_merge_sync_events_merge_type
        ON menu_merge_sync_events(merge_id, event_type);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_menu_merge_remote_events_reverts
        ON menu_merge_remote_events(reverts_remote_event_id);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_menu_merge_remote_events_local_merge_id
        ON menu_merge_remote_events(local_merge_id);
        """
    )
    ensure_menu_sync_quarantine_table(conn)
    ensure_assignment_sync_schema(conn)


def has_menu_merge_sync_table(conn) -> bool:
    if conn is None:
        return False
    return _table_exists(conn, "menu_merge_sync_events")


def _make_event_id() -> str:
    return uuid.uuid4().hex


def _normalize_history_payload(raw_payload: Any) -> Any:
    if raw_payload is None:
        return []
    if isinstance(raw_payload, str):
        try:
            return json.loads(raw_payload)
        except json.JSONDecodeError:
            return []
    return raw_payload


def _normalize_variant_key(variant_id: Any) -> str:
    if variant_id in (None, "None"):
        return menu_utils.NULL_VARIANT_SENTINEL
    return str(variant_id)


def _lookup_variant_name(conn, variant_id: Any) -> str:
    normalized_variant_id = _normalize_variant_key(variant_id)
    if normalized_variant_id == menu_utils.NULL_VARIANT_SENTINEL:
        return menu_utils.NULL_VARIANT_LABEL

    row = conn.execute(
        "SELECT variant_name FROM variants WHERE variant_id = ? LIMIT 1",
        (normalized_variant_id,),
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return normalized_variant_id


def _lookup_menu_item_snapshot(
    conn,
    menu_item_id: str,
    fallback_name: Optional[str] = None,
    fallback_type: Optional[str] = None,
) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT menu_item_id, name, type, is_verified
        FROM menu_items
        WHERE menu_item_id = ?
        LIMIT 1
        """,
        (menu_item_id,),
    ).fetchone()
    if row:
        return {
            "menu_item_id": str(row["menu_item_id"]),
            "name": str(row["name"]),
            "type": str(row["type"]),
            "is_verified": bool(row["is_verified"]),
        }
    return {
        "menu_item_id": str(menu_item_id),
        "name": fallback_name,
        "type": fallback_type,
        "is_verified": None,
    }


def _lookup_merge_row(conn, merge_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM merge_history WHERE merge_id = ? LIMIT 1",
        (merge_id,),
    ).fetchone()
    return dict(row) if row else None


def _lookup_event_id(conn, merge_id: int, event_type: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT event_id
        FROM menu_merge_sync_events
        WHERE merge_id = ? AND event_type = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (merge_id, event_type),
    ).fetchone()
    return str(row["event_id"]) if row else None


def _insert_event(conn, merge_id: Optional[int], event_type: str, occurred_at: str, payload: Dict[str, Any]) -> str:
    ensure_menu_merge_sync_tables(conn)

    if merge_id is not None:
        existing_event_id = _lookup_event_id(conn, merge_id, event_type)
        if existing_event_id:
            return existing_event_id

    event_id = str(payload["remote_event_id"])
    conn.execute(
        """
        INSERT INTO menu_merge_sync_events (
            event_id,
            merge_id,
            event_type,
            payload,
            occurred_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            event_id,
            merge_id,
            event_type,
            json.dumps(payload, sort_keys=True, default=str),
            occurred_at,
        ),
    )
    return event_id


def _dedupe_variant_mappings(conn, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        source_variant_id = _normalize_variant_key(row.get("old_variant_id"))
        target_variant_id = _normalize_variant_key(row.get("new_variant_id"))
        deduped[source_variant_id] = {
            "source_variant_id": source_variant_id,
            "source_variant_name": _lookup_variant_name(conn, source_variant_id),
            "target_variant_id": target_variant_id,
            "target_variant_name": _lookup_variant_name(conn, target_variant_id),
        }
    return list(deduped.values())


def _current_mapping_state(conn, order_item_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT variant_id, is_verified
        FROM menu_item_variants
        WHERE order_item_id = ?
        LIMIT 1
        """,
        (str(order_item_id),),
    ).fetchone()
    if not row:
        return None
    return {"variant_id": row["variant_id"], "is_verified": int(row["is_verified"] or 0)}


def _build_emit_assignments(
    conn,
    history_row: Dict[str, Any],
    history_payload: Any,
    kind: str,
    undo: bool,
) -> List[Dict[str, Any]]:
    """
    Build the explicit per-order-item assignment list for a v2 event
    (plan §2.2). Emit is called inside the same transaction as the merge (or
    undo), after its row updates, so reading current row state gives the
    post-operation values — the applier can stay stateless.

    For undo events the list is the PRIOR assignments (the state the undo
    restored), so a peer applies the undo as an ordinary LWW write.
    """
    source_id = str(history_row["source_id"])
    target_id = str(history_row["target_id"])
    destination = source_id if undo else target_id
    assignments: List[Dict[str, Any]] = []

    # order_item_remap_v1 shares the resolution shape: mapping_rows carry the
    # explicit old/new per-order-item state.
    if kind in ("resolution_variant_v1", "order_item_remap_v1") and isinstance(history_payload, dict):
        for row in history_payload.get("mapping_rows", []):
            if not isinstance(row, dict):
                continue
            order_item_id = str(row.get("order_item_id") or "").strip()
            if not order_item_id:
                continue
            if undo:
                menu_item_id = str(row.get("old_menu_item_id") or source_id)
                variant_value = row.get("old_variant_id")
                verified = int(row.get("old_is_verified", 0) or 0)
            else:
                menu_item_id = str(row.get("new_menu_item_id") or target_id)
                variant_value = row.get("new_variant_id")
                verified = int(row.get("new_is_verified", 1) or 0)
            assignments.append(
                {
                    "order_item_id": order_item_id,
                    "menu_item_id": menu_item_id,
                    "variant_id": None if variant_value in (None, "None", menu_utils.NULL_VARIANT_SENTINEL) else str(variant_value),
                    "is_verified": verified,
                }
            )
        return assignments

    if kind == "variant_merge_v1" and isinstance(history_payload, dict):
        for row in history_payload.get("mapping_rows", []):
            if not isinstance(row, dict):
                continue
            order_item_id = str(row.get("order_item_id") or "").strip()
            if not order_item_id:
                continue
            variant_value = row.get("old_variant_id") if undo else row.get("new_variant_id")
            assignment: Dict[str, Any] = {
                "order_item_id": order_item_id,
                "menu_item_id": destination,
                "variant_id": None if variant_value in (None, "None", menu_utils.NULL_VARIANT_SENTINEL) else str(variant_value),
            }
            state = _current_mapping_state(conn, order_item_id)
            if state is not None:
                assignment["is_verified"] = state["is_verified"]
            assignments.append(assignment)
        return assignments

    # basic_merge_v1 (and legacy list payloads): affected ids move wholesale;
    # emit the row's current variant/verify values so the applier is stateless.
    if isinstance(history_payload, dict):
        affected_ids = history_payload.get("affected_order_item_ids", [])
    elif isinstance(history_payload, list):
        affected_ids = history_payload
    else:
        affected_ids = []
    for order_item_id in affected_ids or []:
        normalized_id = str(order_item_id or "").strip()
        if not normalized_id:
            continue
        assignment = {"order_item_id": normalized_id, "menu_item_id": destination}
        state = _current_mapping_state(conn, normalized_id)
        if state is not None:
            assignment["variant_id"] = state["variant_id"]
            assignment["is_verified"] = state["is_verified"]
        assignments.append(assignment)
    return assignments


def _build_merge_payload(conn, history_row: Dict[str, Any], undo: bool = False) -> Dict[str, Any]:
    history_payload = _normalize_history_payload(history_row.get("affected_order_items"))
    if isinstance(history_payload, dict):
        history_kind = str(history_payload.get("kind") or "").strip()
    else:
        history_kind = ""

    if history_kind == "variant_merge_v1":
        variant_rows = history_payload.get("mapping_rows", [])
        if not isinstance(variant_rows, list):
            variant_rows = []
        variant_mappings = _dedupe_variant_mappings(conn, variant_rows)
        payload = {
            "kind": "variant_merge_v1",
            "variant_mappings": variant_mappings,
            "history_payload": history_payload,
        }
    elif history_kind == "resolution_variant_v1":
        source_variant_id = _normalize_variant_key(history_payload.get("source_variant_id"))
        target_variant_id = _normalize_variant_key(history_payload.get("target_variant_id"))
        payload = {
            "kind": "resolution_variant_v1",
            "resolution": {
                "source_variant_id": source_variant_id,
                "source_variant_name": history_payload.get("source_variant_name") or _lookup_variant_name(conn, source_variant_id),
                "target_variant_id": target_variant_id,
                "target_variant_name": history_payload.get("target_variant_name") or _lookup_variant_name(conn, target_variant_id),
            },
            "history_payload": history_payload,
        }
    elif history_kind == "order_item_remap_v1":
        source_variant_id = _normalize_variant_key(history_payload.get("source_variant_id"))
        target_variant_id = _normalize_variant_key(history_payload.get("target_variant_id"))
        # order_item_id participates in the signature so two remaps between the
        # same clusters but for different order items never dedupe together.
        payload = {
            "kind": "order_item_remap_v1",
            "remap": {
                "order_item_id": str(history_payload.get("order_item_id") or ""),
                "source_variant_id": source_variant_id,
                "source_variant_name": _lookup_variant_name(conn, source_variant_id),
                "target_variant_id": target_variant_id,
                "target_variant_name": _lookup_variant_name(conn, target_variant_id),
            },
            "history_payload": history_payload,
        }
    else:
        payload = {
            "kind": "basic_merge_v1",
            "history_payload": history_payload,
        }

    signature_payload = {
        "source_id": str(history_row["source_id"]),
        "target_id": str(history_row["target_id"]),
        "merge_payload": payload,
    }
    # Signature is computed BEFORE assignments are attached so v1↔v2 events for
    # the same operation keep matching in dedupe (plan C3.2).
    payload["operation_signature"] = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    payload["assignments"] = _build_emit_assignments(
        conn,
        history_row,
        history_payload,
        str(payload["kind"]),
        undo=undo,
    )
    return payload


def build_menu_merge_event_payload(
    conn,
    merge_id: int,
    event_type: str,
    *,
    reverts_remote_event_id: Optional[str] = None,
    occurred_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build a menu merge sync event payload from in-transaction state.

    Intended for strict-mode capture before rollback — does not write to the outbox.
    """
    history_row = _lookup_merge_row(conn, merge_id)
    if not history_row:
        return None
    payload = _build_event_payload(conn, history_row, event_type, reverts_remote_event_id=reverts_remote_event_id)
    if occurred_at:
        payload["occurred_at"] = occurred_at
    return payload


def _build_event_payload(conn, history_row: Dict[str, Any], event_type: str, reverts_remote_event_id: Optional[str] = None) -> Dict[str, Any]:
    source_item = _lookup_menu_item_snapshot(
        conn,
        str(history_row["source_id"]),
        fallback_name=history_row.get("source_name"),
        fallback_type=history_row.get("source_type"),
    )
    target_item = _lookup_menu_item_snapshot(conn, str(history_row["target_id"]))
    payload = {
        "remote_event_id": _make_event_id(),
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "occurred_at": history_row.get("merged_at"),
        "attribution": get_sync_attribution(conn),
        "source_item": source_item,
        "target_item": target_item,
        "merge_payload": _build_merge_payload(conn, history_row, undo=(event_type == EVENT_TYPE_UNDONE)),
        "local_refs": {
            "merge_id": int(history_row["merge_id"]),
        },
    }
    if reverts_remote_event_id:
        payload["reverts_remote_event_id"] = reverts_remote_event_id
    return payload


def record_menu_merge_applied_event(conn, merge_id: int) -> Optional[str]:
    ensure_menu_merge_sync_tables(conn)

    history_row = _lookup_merge_row(conn, merge_id)
    if not history_row:
        return None

    payload = _build_event_payload(conn, history_row, EVENT_TYPE_APPLIED)
    payload["occurred_at"] = history_row.get("merged_at")
    return _insert_event(
        conn,
        merge_id=merge_id,
        event_type=EVENT_TYPE_APPLIED,
        occurred_at=str(history_row.get("merged_at") or ""),
        payload=payload,
    )


def record_menu_merge_undone_event(conn, history_row: Dict[str, Any], occurred_at: str) -> Optional[str]:
    ensure_menu_merge_sync_tables(conn)

    merge_id = int(history_row["merge_id"])
    applied_event_id = _lookup_event_id(conn, merge_id, EVENT_TYPE_APPLIED)
    if not applied_event_id:
        applied_event_id = record_menu_merge_applied_event(conn, merge_id)
    if not applied_event_id:
        return None

    payload = _build_event_payload(
        conn,
        history_row,
        EVENT_TYPE_UNDONE,
        reverts_remote_event_id=applied_event_id,
    )
    payload["occurred_at"] = occurred_at
    payload["undo_metadata"] = {
        "original_merged_at": history_row.get("merged_at"),
    }
    return _insert_event(
        conn,
        merge_id=merge_id,
        event_type=EVENT_TYPE_UNDONE,
        occurred_at=occurred_at,
        payload=payload,
    )


def backfill_menu_merge_sync_events(conn) -> Dict[str, int]:
    ensure_menu_merge_sync_tables(conn)

    counts = {"applied": 0}
    # Only locally-authored history rows belong in the outbox. Rows written by
    # the remote applier carry origin='remote' (older local rows predate the
    # column and are NULL); re-emitting them would republish a peer's decision
    # under a new event id / server_seq and duplicate or reorder it.
    rows = conn.execute(
        """
        SELECT merge_id FROM merge_history
        WHERE COALESCE(origin, 'local') != 'remote'
        ORDER BY merge_id ASC
        """
    ).fetchall()
    for row in rows:
        merge_id = int(row["merge_id"])
        if _lookup_event_id(conn, merge_id, EVENT_TYPE_APPLIED) is not None:
            continue
        if record_menu_merge_applied_event(conn, merge_id):
            counts["applied"] += 1
    return counts
