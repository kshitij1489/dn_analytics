"""
Remote pull/apply for menu merge collaboration events.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.menu_assignment_apply import (
    apply_assignments,
    coerce_server_seq,
    extract_assignments,
    insert_supersede_notice,
    is_assignment_apply_enabled,
    lookup_event_by_server_seq,
)
from src.core.menu_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
    ensure_menu_merge_sync_tables,
)
from src.core.menu_sync_quarantine import (
    ensure_menu_sync_quarantine_table,
    fetch_unresolved_quarantined_events,
    mark_quarantined_event_resolved,
    quarantine_event,
    record_quarantine_retry_failure,
)
from src.core.sync_cursor_migration import ensure_sync_cursor_schema
from utils import menu_utils


logger = logging.getLogger(__name__)

MENU_MERGE_PULL_CURSOR_KEY = "menu_merge_pull_cursor"
QUARANTINE_STREAM_MENU_MERGE = "menu_merge"
DEFAULT_PULL_LIMIT = 100


def get_menu_merge_pull_endpoint(conn) -> Optional[str]:
    from src.core.config.client_learning_config import CLIENT_LEARNING_MENU_MERGE_PULL_URL

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url}/desktop-analytics-sync/menu-merges"
    return CLIENT_LEARNING_MENU_MERGE_PULL_URL or None


def _ensure_pull_tables(conn) -> None:
    ensure_menu_merge_sync_tables(conn)
    ensure_menu_sync_quarantine_table(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    ensure_sync_cursor_schema(conn)


def get_menu_merge_pull_cursor(conn) -> Optional[str]:
    _ensure_pull_tables(conn)
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = ? LIMIT 1",
        (MENU_MERGE_PULL_CURSOR_KEY,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def set_menu_merge_pull_cursor(conn, cursor: Optional[str]) -> None:
    _ensure_pull_tables(conn)
    if cursor is None:
        conn.execute("DELETE FROM system_config WHERE key = ?", (MENU_MERGE_PULL_CURSOR_KEY,))
        return
    conn.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (MENU_MERGE_PULL_CURSOR_KEY, cursor),
    )


def _normalize_variant_key(variant_id: Any) -> str:
    if variant_id in (None, "None"):
        return menu_utils.NULL_VARIANT_SENTINEL
    return str(variant_id)


def _normalize_merge_payload(raw_payload: Any) -> Dict[str, Any]:
    if not isinstance(raw_payload, dict):
        return {"kind": "basic_merge_v1"}
    payload = dict(raw_payload)
    payload["kind"] = str(payload.get("kind") or "basic_merge_v1")
    return payload


def _event_signature(source_id: str, target_id: str, merge_payload: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(merge_payload.get("kind") or "basic_merge_v1")
    signature: Dict[str, Any] = {
        "source_id": str(source_id),
        "target_id": str(target_id),
        "kind": kind,
    }
    if kind == "variant_merge_v1":
        mappings = merge_payload.get("variant_mappings", [])
        if not isinstance(mappings, list):
            mappings = []
        signature["variant_mappings"] = sorted(
            (
                _normalize_variant_key(mapping.get("source_variant_id")),
                _normalize_variant_key(mapping.get("target_variant_id")),
            )
            for mapping in mappings
            if isinstance(mapping, dict)
        )
    elif kind == "resolution_variant_v1":
        resolution = merge_payload.get("resolution", {})
        if not isinstance(resolution, dict):
            resolution = {}
        signature["resolution"] = {
            "source_variant_id": _normalize_variant_key(resolution.get("source_variant_id")),
            "target_variant_id": _normalize_variant_key(resolution.get("target_variant_id")),
        }
    elif kind == "order_item_remap_v1":
        remap = merge_payload.get("remap", {})
        if not isinstance(remap, dict):
            remap = {}
        # order_item_id keeps two remaps between the same clusters distinct.
        signature["remap"] = {
            "order_item_id": str(remap.get("order_item_id") or ""),
            "source_variant_id": _normalize_variant_key(remap.get("source_variant_id")),
            "target_variant_id": _normalize_variant_key(remap.get("target_variant_id")),
        }
    return signature


def _history_signature(history_row: Dict[str, Any]) -> Dict[str, Any]:
    history_payload = history_row.get("affected_order_items")
    if isinstance(history_payload, str):
        try:
            history_payload = json.loads(history_payload)
        except json.JSONDecodeError:
            history_payload = []

    if isinstance(history_payload, dict):
        history_kind = str(history_payload.get("kind") or "basic_merge_v1")
    else:
        history_kind = "basic_merge_v1"

    merge_payload: Dict[str, Any] = {"kind": history_kind}
    if history_kind == "variant_merge_v1":
        mapping_rows = history_payload.get("mapping_rows", [])
        if not isinstance(mapping_rows, list):
            mapping_rows = []
        deduped = {}
        for row in mapping_rows:
            if not isinstance(row, dict):
                continue
            deduped[_normalize_variant_key(row.get("old_variant_id"))] = {
                "source_variant_id": _normalize_variant_key(row.get("old_variant_id")),
                "target_variant_id": _normalize_variant_key(row.get("new_variant_id")),
            }
        merge_payload["variant_mappings"] = list(deduped.values())
    elif history_kind == "resolution_variant_v1":
        merge_payload["resolution"] = {
            "source_variant_id": _normalize_variant_key(history_payload.get("source_variant_id")),
            "target_variant_id": _normalize_variant_key(history_payload.get("target_variant_id")),
        }
    elif history_kind == "order_item_remap_v1":
        merge_payload["remap"] = {
            "order_item_id": str(history_payload.get("order_item_id") or ""),
            "source_variant_id": _normalize_variant_key(history_payload.get("source_variant_id")),
            "target_variant_id": _normalize_variant_key(history_payload.get("target_variant_id")),
        }

    return _event_signature(
        str(history_row["source_id"]),
        str(history_row["target_id"]),
        merge_payload,
    )


def _find_matching_local_merge(conn, event: Dict[str, Any]) -> Optional[int]:
    source_item = event.get("source_item")
    target_item = event.get("target_item")
    if not isinstance(source_item, dict) or not isinstance(target_item, dict):
        return None

    source_id = str(source_item.get("menu_item_id") or "").strip()
    target_id = str(target_item.get("menu_item_id") or "").strip()
    if not source_id or not target_id:
        return None

    event_signature = _event_signature(source_id, target_id, _normalize_merge_payload(event.get("merge_payload")))
    rows = conn.execute(
        """
        SELECT *
        FROM merge_history
        WHERE source_id = ? AND target_id = ?
        ORDER BY merge_id DESC
        """,
        (source_id, target_id),
    ).fetchall()
    for row in rows:
        history_row = dict(row)
        if _history_signature(history_row) == event_signature:
            return int(history_row["merge_id"])
    return None


def _remote_event_exists(conn, remote_event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM menu_merge_remote_events WHERE remote_event_id = ? LIMIT 1",
        (remote_event_id,),
    ).fetchone()
    return row is not None


def _lookup_remote_local_merge_id(conn, remote_event_id: str) -> Optional[int]:
    row = conn.execute(
        """
        SELECT local_merge_id
        FROM menu_merge_remote_events
        WHERE remote_event_id = ?
        LIMIT 1
        """,
        (remote_event_id,),
    ).fetchone()
    if not row or row["local_merge_id"] is None:
        return None
    return int(row["local_merge_id"])


def _record_remote_event(
    conn,
    remote_event_id: str,
    event_type: str,
    local_merge_id: Optional[int],
    payload: Dict[str, Any],
    remote_cursor: Optional[str],
    occurred_at: Optional[str],
    reverts_remote_event_id: Optional[str] = None,
    server_seq: Optional[int] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO menu_merge_remote_events (
            remote_event_id,
            event_type,
            reverts_remote_event_id,
            local_merge_id,
            payload,
            remote_cursor,
            occurred_at,
            server_seq
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            remote_event_id,
            event_type,
            reverts_remote_event_id,
            local_merge_id,
            json.dumps(payload, sort_keys=True, default=str),
            remote_cursor,
            occurred_at,
            server_seq,
        ),
    )


def _ensure_menu_item_exists(conn, item: Dict[str, Any]) -> None:
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


def _ensure_variant_exists(conn, variant_id: Any, variant_name: Any) -> None:
    normalized_variant_id = _normalize_variant_key(variant_id)
    if normalized_variant_id == menu_utils.NULL_VARIANT_SENTINEL:
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


def _merge_has_local_event(conn, merge_id: int) -> bool:
    """
    True when this merge_history row has an outbox event — i.e. the merge was
    authored on this install (remote-applied merges never emit outbox events).
    """
    row = conn.execute(
        "SELECT 1 FROM menu_merge_sync_events WHERE merge_id = ? LIMIT 1",
        (int(merge_id),),
    ).fetchone()
    return row is not None


def _insert_remote_merge_history(
    conn,
    event: Dict[str, Any],
    assignments: List[Dict[str, Any]],
) -> Optional[int]:
    """
    Record a merge_history row (origin='remote') for an applied remote event so
    Resolution History and undo keep working without replaying through
    menu_utils (plan §2.3 step 4).
    """
    source_item = event.get("source_item") if isinstance(event.get("source_item"), dict) else {}
    target_item = event.get("target_item") if isinstance(event.get("target_item"), dict) else {}
    source_id = str(source_item.get("menu_item_id") or "").strip()
    target_id = str(target_item.get("menu_item_id") or "").strip()
    if not source_id or not target_id:
        return None

    merge_payload = _normalize_merge_payload(event.get("merge_payload"))
    history_payload = merge_payload.get("history_payload")
    if not isinstance(history_payload, (dict, list)):
        history_payload = {
            "kind": merge_payload["kind"],
            "assignments": assignments,
            "synthesized_from": "assignments_v2",
        }

    cur = conn.execute(
        """
        INSERT INTO merge_history (
            source_id, target_id, source_name, source_type,
            affected_order_items, merged_at, origin
        )
        VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), 'remote')
        """,
        (
            source_id,
            target_id,
            str(source_item.get("name") or source_id),
            str(source_item.get("type") or "Unknown"),
            json.dumps(history_payload, sort_keys=True, default=str),
            str(event.get("occurred_at") or "") or None,
        ),
    )
    return int(cur.lastrowid)


def _notice_for_stale_rows(conn, stale_rows: List[Dict[str, Any]], local_merge_id: Optional[int]) -> None:
    """
    Our own event echoed back, but some rows already carry a higher server_seq:
    a peer's later decision outranked ours between our edit and our ack. The
    winning value is already on the rows; tell the user (fixes I13's loser
    notification).
    """
    for stale in stale_rows:
        winner = lookup_event_by_server_seq(conn, stale.get("assignment_seq"))
        insert_supersede_notice(
            conn,
            stale["order_item_id"],
            local_merge_id,
            winner["remote_event_id"] if winner else None,
            winner["attribution"] if winner else None,
        )


def _is_self_merge_event(event: Dict[str, Any]) -> bool:
    """Source and target are the same menu item (legacy audit events only)."""
    source_item = event.get("source_item")
    target_item = event.get("target_item")
    if not isinstance(source_item, dict) or not isinstance(target_item, dict):
        return False
    source_id = str(source_item.get("menu_item_id") or "").strip()
    target_id = str(target_item.get("menu_item_id") or "").strip()
    return bool(source_id) and source_id == target_id


def _record_self_merge_noop(
    conn,
    event: Dict[str, Any],
    remote_cursor: Optional[str],
    event_type: str,
) -> Dict[str, Any]:
    """Mark a same-item no-assignment event as seen without touching any rows."""
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    _record_remote_event(
        conn,
        remote_event_id=remote_event_id,
        event_type=event_type,
        local_merge_id=None,
        payload=event,
        remote_cursor=remote_cursor,
        occurred_at=str(event.get("occurred_at") or ""),
        server_seq=coerce_server_seq(event.get("server_seq")),
    )
    logger.info(
        "Menu merge event %s is a same-item no-op (no derivable assignments); recorded without replay",
        remote_event_id,
    )
    return {"status": "noop", "local_merge_id": None, "touched_menu_item_ids": set()}


def _apply_remote_merge_event_assignments(
    conn,
    event: Dict[str, Any],
    remote_cursor: Optional[str],
) -> Dict[str, Any]:
    """Assignment-based apply (plan §2.3) for menu_merge.applied events."""
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    if not remote_event_id:
        raise ValueError("Menu merge event is missing remote_event_id")
    if _remote_event_exists(conn, remote_event_id):
        return {"status": "duplicate", "local_merge_id": _lookup_remote_local_merge_id(conn, remote_event_id)}

    assignments = extract_assignments(event)
    if assignments is None:
        if _is_self_merge_event(event):
            # Legacy same-item events (April mapping_audit_v1 consolidations:
            # source == target by design) carry no derivable assignments and
            # cannot move any mapping. Legacy replay would raise "Cannot merge
            # item into itself" and quarantine them forever; the server's
            # materializer likewise skips them. Record as an applied no-op.
            return _record_self_merge_noop(
                conn, event, remote_cursor, EVENT_TYPE_APPLIED
            )
        logger.error(
            "Menu merge event %s carries no derivable assignments; using legacy cluster replay",
            remote_event_id,
        )
        return _apply_remote_merge_event_legacy(conn, event, remote_cursor)

    server_seq = coerce_server_seq(event.get("server_seq"))

    existing_merge_id = _find_matching_local_merge(conn, event)
    if existing_merge_id is not None:
        # Same operation already exists locally. Stamp the server's ordering on
        # the affected rows and clear pending_local; when it is the echo of our
        # own event and some rows were meanwhile outranked, notify the user.
        result = apply_assignments(conn, assignments, server_seq, event, detect_supersede=False)
        if _merge_has_local_event(conn, existing_merge_id):
            _notice_for_stale_rows(conn, result["stale_rows"], existing_merge_id)
        _record_remote_event(
            conn,
            remote_event_id=remote_event_id,
            event_type=EVENT_TYPE_APPLIED,
            local_merge_id=existing_merge_id,
            payload=event,
            remote_cursor=remote_cursor,
            occurred_at=str(event.get("occurred_at") or ""),
            server_seq=server_seq,
        )
        return {
            "status": "duplicate",
            "local_merge_id": existing_merge_id,
            "touched_menu_item_ids": result["touched_menu_item_ids"],
        }

    source_item = event.get("source_item")
    target_item = event.get("target_item")
    if isinstance(target_item, dict):
        _ensure_menu_item_exists(conn, target_item)
    if isinstance(source_item, dict):
        _ensure_menu_item_exists(conn, source_item)

    result = apply_assignments(conn, assignments, server_seq, event, detect_supersede=True)
    # Include both endpoints so the batch epilogue garbage-collects a source
    # item resurrected from the snapshot that ends up with no mappings.
    for endpoint_item in (source_item, target_item):
        if isinstance(endpoint_item, dict):
            endpoint_id = str(endpoint_item.get("menu_item_id") or "").strip()
            if endpoint_id:
                result["touched_menu_item_ids"].add(endpoint_id)
    for superseded in result["superseded"]:
        insert_supersede_notice(
            conn,
            superseded["order_item_id"],
            None,
            remote_event_id,
            event.get("attribution"),
        )

    local_merge_id = _insert_remote_merge_history(conn, event, assignments)
    _record_remote_event(
        conn,
        remote_event_id=remote_event_id,
        event_type=EVENT_TYPE_APPLIED,
        local_merge_id=local_merge_id,
        payload=event,
        remote_cursor=remote_cursor,
        occurred_at=str(event.get("occurred_at") or ""),
        server_seq=server_seq,
    )
    return {
        "status": "applied",
        "local_merge_id": local_merge_id,
        "rows_applied": result["rows_applied"],
        "rows_stale": len(result["stale_rows"]),
        "touched_menu_item_ids": result["touched_menu_item_ids"],
    }


def _apply_remote_undo_event_assignments(
    conn,
    event: Dict[str, Any],
    remote_cursor: Optional[str],
) -> Dict[str, Any]:
    """
    Assignment-based apply for menu_merge.undone: the undo is just another LWW
    write of the prior assignments at its own server_seq (fixes I10 — no need
    for the original merge to have applied locally).
    """
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    if not remote_event_id:
        raise ValueError("Menu merge undo event is missing remote_event_id")
    if _remote_event_exists(conn, remote_event_id):
        return {"status": "duplicate", "local_merge_id": _lookup_remote_local_merge_id(conn, remote_event_id)}

    assignments = extract_assignments(event)
    if assignments is None:
        if _is_self_merge_event(event):
            return _record_self_merge_noop(
                conn, event, remote_cursor, EVENT_TYPE_UNDONE
            )
        logger.error(
            "Menu merge undo event %s carries no derivable assignments; using legacy undo replay",
            remote_event_id,
        )
        return _apply_remote_undo_event_legacy(conn, event, remote_cursor)

    server_seq = coerce_server_seq(event.get("server_seq"))
    reverted_remote_event_id = str(event.get("reverts_remote_event_id") or "").strip() or None

    local_merge_id = None
    if reverted_remote_event_id:
        local_merge_id = _lookup_remote_local_merge_id(conn, reverted_remote_event_id)
    if local_merge_id is None:
        local_merge_id = _find_matching_local_merge(conn, event)

    result = apply_assignments(conn, assignments, server_seq, event, detect_supersede=True)
    for endpoint_key in ("source_item", "target_item"):
        endpoint_item = event.get(endpoint_key)
        if isinstance(endpoint_item, dict):
            endpoint_id = str(endpoint_item.get("menu_item_id") or "").strip()
            if endpoint_id:
                result["touched_menu_item_ids"].add(endpoint_id)
    for superseded in result["superseded"]:
        insert_supersede_notice(
            conn,
            superseded["order_item_id"],
            local_merge_id,
            remote_event_id,
            event.get("attribution"),
        )

    if local_merge_id is not None:
        # Mirror local undo semantics: the reverted merge leaves Resolution History.
        conn.execute("DELETE FROM merge_history WHERE merge_id = ?", (local_merge_id,))

    _record_remote_event(
        conn,
        remote_event_id=remote_event_id,
        event_type=EVENT_TYPE_UNDONE,
        local_merge_id=local_merge_id,
        payload=event,
        remote_cursor=remote_cursor,
        occurred_at=str(event.get("occurred_at") or ""),
        reverts_remote_event_id=reverted_remote_event_id,
        server_seq=server_seq,
    )
    return {
        "status": "applied",
        "local_merge_id": local_merge_id,
        "rows_applied": result["rows_applied"],
        "rows_stale": len(result["stale_rows"]),
        "touched_menu_item_ids": result["touched_menu_item_ids"],
    }


def _apply_remote_merge_event(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    if is_assignment_apply_enabled():
        return _apply_remote_merge_event_assignments(conn, event, remote_cursor)
    return _apply_remote_merge_event_legacy(conn, event, remote_cursor)


def _apply_remote_merge_event_legacy(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    if not remote_event_id:
        raise ValueError("Menu merge event is missing remote_event_id")
    if _remote_event_exists(conn, remote_event_id):
        return {"status": "duplicate", "local_merge_id": _lookup_remote_local_merge_id(conn, remote_event_id)}

    existing_merge_id = _find_matching_local_merge(conn, event)
    if existing_merge_id is not None:
        _record_remote_event(
            conn,
            remote_event_id=remote_event_id,
            event_type=EVENT_TYPE_APPLIED,
            local_merge_id=existing_merge_id,
            payload=event,
            remote_cursor=remote_cursor,
            occurred_at=str(event.get("occurred_at") or ""),
        )
        return {"status": "duplicate", "local_merge_id": existing_merge_id}

    source_item = event.get("source_item")
    target_item = event.get("target_item")
    if not isinstance(source_item, dict) or not isinstance(target_item, dict):
        raise ValueError("Menu merge event is missing source_item or target_item")

    source_id = str(source_item.get("menu_item_id") or "").strip()
    target_id = str(target_item.get("menu_item_id") or "").strip()
    if not source_id or not target_id:
        raise ValueError("Menu merge event is missing source or target menu_item_id")

    # Backfill both endpoints from the event snapshot. The source item may be
    # absent locally (e.g. already merged away, or this device ingested the menu
    # later than the origin device); recreating it from the snapshot lets the
    # merge replay instead of failing with "Source item was not found".
    _ensure_menu_item_exists(conn, target_item)
    _ensure_menu_item_exists(conn, source_item)

    merge_payload = _normalize_merge_payload(event.get("merge_payload"))
    merge_kind = merge_payload["kind"]

    if merge_kind == "basic_merge_v1":
        result = menu_utils.merge_menu_items(conn, source_id, target_id, emit_sync_event=False)
    elif merge_kind == "variant_merge_v1":
        variant_mappings = merge_payload.get("variant_mappings", [])
        if not isinstance(variant_mappings, list) or not variant_mappings:
            raise ValueError("Variant merge event is missing variant_mappings")

        resolved_mappings: List[Dict[str, Any]] = []
        for mapping in variant_mappings:
            if not isinstance(mapping, dict):
                continue
            source_variant_id = _normalize_variant_key(mapping.get("source_variant_id"))
            target_variant_id = _normalize_variant_key(mapping.get("target_variant_id"))
            target_variant_name = mapping.get("target_variant_name")
            _ensure_variant_exists(conn, target_variant_id, target_variant_name)
            _ensure_variant_exists(conn, source_variant_id, mapping.get("source_variant_name"))
            resolved_mappings.append(
                {
                    "source_variant_id": source_variant_id,
                    "target_variant_id": None
                    if target_variant_id == menu_utils.NULL_VARIANT_SENTINEL
                    else target_variant_id,
                    "new_variant_name": None
                    if target_variant_id != menu_utils.NULL_VARIANT_SENTINEL
                    else target_variant_name,
                }
            )

        result = menu_utils.merge_menu_items_with_variant_mappings(
            conn,
            source_id,
            target_id,
            resolved_mappings,
            emit_sync_event=False,
        )
    elif merge_kind == "resolution_variant_v1":
        resolution = merge_payload.get("resolution", {})
        if not isinstance(resolution, dict):
            resolution = {}

        target_variant_id = _normalize_variant_key(resolution.get("target_variant_id"))
        target_variant_name = resolution.get("target_variant_name")
        source_variant_id = _normalize_variant_key(resolution.get("source_variant_id"))
        _ensure_menu_item_exists(conn, target_item)
        _ensure_variant_exists(conn, target_variant_id, target_variant_name)
        _ensure_variant_exists(conn, source_variant_id, resolution.get("source_variant_name"))

        result = menu_utils.resolve_menu_item_variant(
            conn,
            source_menu_item_id=source_id,
            source_variant_id=_normalize_variant_key(resolution.get("source_variant_id")),
            target_menu_item_id=target_id,
            new_name=target_item.get("name") if source_id == target_id else None,
            new_type=target_item.get("type") if source_id == target_id else None,
            target_variant_id=None if target_variant_id == menu_utils.NULL_VARIANT_SENTINEL else target_variant_id,
            new_variant_name=target_variant_name if target_variant_id == menu_utils.NULL_VARIANT_SENTINEL else None,
            emit_sync_event=False,
            emit_mapping_verification_events=False,
        )
    else:
        raise ValueError(f"Unsupported menu merge kind '{merge_kind}'")

    if result.get("status") == "error":
        raise ValueError(str(result.get("message") or "Menu merge apply failed"))

    local_merge_id = result.get("merge_id")
    if local_merge_id is None:
        local_merge_id = _find_matching_local_merge(conn, event)
    if local_merge_id is None:
        raise ValueError(f"Could not resolve local merge_id for remote event {remote_event_id}")

    _record_remote_event(
        conn,
        remote_event_id=remote_event_id,
        event_type=EVENT_TYPE_APPLIED,
        local_merge_id=int(local_merge_id),
        payload=event,
        remote_cursor=remote_cursor,
        occurred_at=str(event.get("occurred_at") or ""),
    )
    return {"status": "applied", "local_merge_id": int(local_merge_id)}


def _apply_remote_undo_event(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    if is_assignment_apply_enabled():
        return _apply_remote_undo_event_assignments(conn, event, remote_cursor)
    return _apply_remote_undo_event_legacy(conn, event, remote_cursor)


def _apply_remote_undo_event_legacy(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    if not remote_event_id:
        raise ValueError("Menu merge undo event is missing remote_event_id")
    if _remote_event_exists(conn, remote_event_id):
        return {"status": "duplicate", "local_merge_id": _lookup_remote_local_merge_id(conn, remote_event_id)}

    reverted_remote_event_id = str(event.get("reverts_remote_event_id") or "").strip()
    if not reverted_remote_event_id:
        raise ValueError(f"Undo event {remote_event_id} is missing reverts_remote_event_id")

    local_merge_id = _lookup_remote_local_merge_id(conn, reverted_remote_event_id)
    if local_merge_id is None:
        local_merge_id = _find_matching_local_merge(conn, event)
    if local_merge_id is None:
        raise ValueError(f"Undo event {remote_event_id} references unknown remote merge event {reverted_remote_event_id}")

    row = conn.execute(
        "SELECT 1 FROM merge_history WHERE merge_id = ? LIMIT 1",
        (local_merge_id,),
    ).fetchone()
    if row is not None:
        result = menu_utils.undo_merge(conn, local_merge_id, emit_sync_event=False)
        if result.get("status") == "error":
            raise ValueError(str(result.get("message") or "Menu merge undo failed"))
        status = "applied"
    else:
        status = "duplicate"

    _record_remote_event(
        conn,
        remote_event_id=remote_event_id,
        event_type=EVENT_TYPE_UNDONE,
        local_merge_id=int(local_merge_id),
        payload=event,
        remote_cursor=remote_cursor,
        occurred_at=str(event.get("occurred_at") or ""),
        reverts_remote_event_id=reverted_remote_event_id,
    )
    return {"status": status, "local_merge_id": int(local_merge_id)}


def _fetch_remote_events(
    endpoint: str,
    auth: Optional[str],
    cursor: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    params: Dict[str, str] = {}
    if cursor:
        params["cursor"] = cursor
    if limit > 0:
        params["limit"] = str(limit)

    try:
        import requests

        response = requests.get(endpoint, headers=headers, params=params or None, timeout=60)
        if response.status_code >= 400:
            return {"events": [], "next_cursor": cursor, "error": f"HTTP {response.status_code}"}
        data = response.json()
    except Exception as exc:
        return {"events": [], "next_cursor": cursor, "error": str(exc)}

    if not isinstance(data, dict):
        return {"events": [], "next_cursor": cursor, "error": "Invalid response payload"}

    events = data.get("events")
    if not isinstance(events, list):
        events = data.get("items")
    if not isinstance(events, list):
        events = []

    next_cursor = data.get("next_cursor")
    if next_cursor is None:
        next_cursor = data.get("cursor_after")
    if next_cursor is None and events:
        next_cursor = data.get("cursor")

    from src.core.sync_identity import extract_menu_scope_state

    return {
        "events": events,
        "next_cursor": next_cursor,
        "has_more": limit > 0 and len(events) >= limit,
        "scope_state": extract_menu_scope_state(data),
        "error": None,
    }


def lookup_applied_remote_event_id(conn, merge_id: int) -> Optional[str]:
    """Return the remote_event_id for a merge's applied event (remote table or local outbox)."""
    row = conn.execute(
        """
        SELECT remote_event_id
        FROM menu_merge_remote_events
        WHERE local_merge_id = ? AND event_type = ?
        LIMIT 1
        """,
        (int(merge_id), EVENT_TYPE_APPLIED),
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    from src.core.menu_merge_sync_events import _lookup_event_id

    return _lookup_event_id(conn, int(merge_id), EVENT_TYPE_APPLIED)


def apply_remote_menu_merge_event(
    conn,
    event: Dict[str, Any],
    remote_cursor: Optional[str],
) -> Dict[str, Any]:
    """Public wrapper over the pull applier for server-accepted mutations."""
    return _apply_remote_event_by_type(conn, event, remote_cursor)


def _apply_remote_event_by_type(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    event_type = str(event.get("event_type") or "").strip()
    if event_type == EVENT_TYPE_APPLIED:
        return _apply_remote_merge_event(conn, event, remote_cursor)
    if event_type == EVENT_TYPE_UNDONE:
        return _apply_remote_undo_event(conn, event, remote_cursor)
    raise ValueError(f"Unsupported remote event_type '{event_type}'")


def _run_assignment_batch_epilogue(conn, touched_menu_item_ids: Set[str]) -> None:
    """
    Batch epilogue after assignment-based applies (plan §2.3 step 3): stats
    recompute + resolution-state sync (which garbage-collects menu items left
    with zero mappings and zero usage), forecast cache clears, one backup
    export. Best-effort — a failure here must not fail the pull.
    """
    menu_item_ids = sorted({str(mid) for mid in touched_menu_item_ids if mid})
    if not menu_item_ids:
        return

    cursor = conn.cursor()
    try:
        for menu_item_id in menu_item_ids:
            menu_utils._recalculate_menu_item_stats(cursor, menu_item_id)
        for menu_item_id in menu_item_ids:
            menu_utils._sync_menu_item_resolution_state(cursor, menu_item_id)
        menu_utils._clear_item_and_volume_forecast_cache(cursor, menu_item_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Assignment apply epilogue failed for items %s", menu_item_ids)
        return
    finally:
        cursor.close()

    try:
        menu_utils._clear_impacted_models(clear_item_models=True, clear_volume_models=True)
    except Exception:
        logger.exception("Assignment apply epilogue could not clear model artifacts")
    try:
        menu_utils.export_to_backups(conn)
    except Exception:
        logger.exception("Assignment apply epilogue could not export backups")


def retry_quarantined_menu_merge_events(conn) -> Dict[str, Any]:
    """
    Re-attempt quarantined menu merge events at the start of each pull.

    Events that now apply (e.g. the missing peer state has since arrived) are
    marked resolved; the rest stay quarantined with a bumped fail_count.
    """
    stats: Dict[str, Any] = {"attempted": 0, "resolved": 0, "touched_menu_item_ids": set()}
    for row in fetch_unresolved_quarantined_events(conn, QUARANTINE_STREAM_MENU_MERGE):
        stats["attempted"] += 1
        remote_event_id = str(row["remote_event_id"])
        try:
            event = json.loads(row["payload"])
            if not isinstance(event, dict):
                raise ValueError("Quarantined payload is not a JSON object")
            result = _apply_remote_event_by_type(conn, event, None)
            mark_quarantined_event_resolved(conn, remote_event_id)
            conn.commit()
            stats["resolved"] += 1
            if isinstance(result, dict):
                stats["touched_menu_item_ids"] |= set(result.get("touched_menu_item_ids") or ())
        except Exception as exc:
            conn.rollback()
            record_quarantine_retry_failure(conn, remote_event_id, str(exc))
            conn.commit()
    return stats


def pull_and_apply_menu_merge_events(
    conn,
    endpoint: str,
    auth: Optional[str] = None,
    limit: int = DEFAULT_PULL_LIMIT,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_pull_tables(conn)
    retry_stats = retry_quarantined_menu_merge_events(conn)
    touched_menu_item_ids: Set[str] = set(retry_stats.get("touched_menu_item_ids") or ())

    cursor_before = cursor if cursor is not None else get_menu_merge_pull_cursor(conn)
    fetch_result = _fetch_remote_events(endpoint, auth=auth, cursor=cursor_before, limit=limit)
    if fetch_result.get("error"):
        if touched_menu_item_ids and is_assignment_apply_enabled():
            _run_assignment_batch_epilogue(conn, touched_menu_item_ids)
        return {
            "events_fetched": 0,
            "merge_events_applied": 0,
            "undo_events_applied": 0,
            "events_skipped": 0,
            "quarantine_retried": retry_stats["attempted"],
            "quarantine_resolved": retry_stats["resolved"],
            "cursor_before": cursor_before,
            "cursor_after": cursor_before,
            "error": fetch_result["error"],
        }

    events = fetch_result["events"]
    next_cursor = fetch_result.get("next_cursor")
    scope_state = fetch_result.get("scope_state") or {}
    has_more = fetch_result.get("has_more", False)
    stats = {
        "events_fetched": len(events),
        "merge_events_applied": 0,
        "undo_events_applied": 0,
        "events_skipped": 0,
        "events_failed": 0,
        "events_quarantined": 0,
        "quarantine_retried": retry_stats["attempted"],
        "quarantine_resolved": retry_stats["resolved"],
        "cursor_before": cursor_before,
        "cursor_after": cursor_before,
        "has_more": has_more,
        "error": None,
        "last_event_error": None,
    }

    for event in events:
        if not isinstance(event, dict):
            # Malformed entry in the response: skip it instead of aborting the
            # whole page so the remaining events still get a chance to apply.
            stats["events_failed"] += 1
            stats["last_event_error"] = "Invalid menu merge event in response payload"
            logger.warning("Menu merge pull skipping malformed event in response payload")
            continue

        event_type = str(event.get("event_type") or "").strip()
        try:
            if event_type == EVENT_TYPE_APPLIED:
                result = _apply_remote_merge_event(conn, event, next_cursor)
                if result["status"] == "applied":
                    stats["merge_events_applied"] += 1
                else:
                    stats["events_skipped"] += 1
            elif event_type == EVENT_TYPE_UNDONE:
                result = _apply_remote_undo_event(conn, event, next_cursor)
                if result["status"] == "applied":
                    stats["undo_events_applied"] += 1
                else:
                    stats["events_skipped"] += 1
            else:
                raise ValueError(f"Unsupported remote event_type '{event_type}'")
            touched_menu_item_ids |= set(result.get("touched_menu_item_ids") or ())
            conn.commit()
        except Exception as exc:
            # A single un-appliable event (divergent local IDs, already-merged
            # source, an item that can't be merged into itself, etc.) must not
            # block the entire pull. Roll back just this event, quarantine it
            # for retry/user surfacing, and continue so the cursor can still
            # advance past it. Transport-level problems are handled separately
            # via stats["error"].
            conn.rollback()
            stats["events_failed"] += 1
            stats["last_event_error"] = str(exc)
            remote_event_id = str(event.get("remote_event_id") or "").strip()
            if remote_event_id:
                try:
                    quarantine_event(
                        conn,
                        QUARANTINE_STREAM_MENU_MERGE,
                        remote_event_id,
                        event,
                        str(exc),
                    )
                    conn.commit()
                    stats["events_quarantined"] += 1
                except Exception:
                    conn.rollback()
                    logger.exception(
                        "Failed to quarantine menu merge event %s", remote_event_id
                    )
            logger.warning(
                "Menu merge pull quarantined event %s: %s",
                event.get("remote_event_id"),
                exc,
            )
            continue

    cursor_after = cursor_before if next_cursor is None else str(next_cursor)
    try:
        from src.core.sync_identity import apply_pull_scope_state

        advertised_revision = apply_pull_scope_state(
            conn,
            scope_state,
            has_more=has_more,
            stats=stats,
            allow_menu_revision=False,
        )
        set_menu_merge_pull_cursor(conn, cursor_after)
        conn.commit()
        stats["cursor_after"] = cursor_after
        stats["advertised_menu_revision"] = advertised_revision
    except Exception as exc:
        conn.rollback()
        stats["error"] = str(exc)
        return stats

    if touched_menu_item_ids and is_assignment_apply_enabled():
        _run_assignment_batch_epilogue(conn, touched_menu_item_ids)
        stats["epilogue_menu_item_ids"] = len(touched_menu_item_ids)

    return stats
