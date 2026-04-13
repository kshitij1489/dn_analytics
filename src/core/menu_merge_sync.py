"""
Remote pull/apply for menu merge collaboration events.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.menu_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
    ensure_menu_merge_sync_tables,
)
from utils import menu_utils


logger = logging.getLogger(__name__)

MENU_MERGE_PULL_CURSOR_KEY = "menu_merge_pull_cursor"
DEFAULT_PULL_LIMIT = 100


def get_menu_merge_pull_endpoint(conn) -> Optional[str]:
    from src.core.config.client_learning_config import CLIENT_LEARNING_MENU_MERGE_PULL_URL

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url}/desktop-analytics-sync/menu-merges"
    return CLIENT_LEARNING_MENU_MERGE_PULL_URL or None


def _ensure_pull_tables(conn) -> None:
    ensure_menu_merge_sync_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


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
            occurred_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            remote_event_id,
            event_type,
            reverts_remote_event_id,
            local_merge_id,
            json.dumps(payload, sort_keys=True, default=str),
            remote_cursor,
            occurred_at,
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


def _apply_remote_merge_event(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
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

    _ensure_menu_item_exists(conn, target_item)

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
        _ensure_menu_item_exists(conn, target_item)
        _ensure_variant_exists(conn, target_variant_id, target_variant_name)

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

    return {"events": events, "next_cursor": next_cursor, "error": None}


def pull_and_apply_menu_merge_events(
    conn,
    endpoint: str,
    auth: Optional[str] = None,
    limit: int = DEFAULT_PULL_LIMIT,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_pull_tables(conn)

    cursor_before = cursor if cursor is not None else get_menu_merge_pull_cursor(conn)
    fetch_result = _fetch_remote_events(endpoint, auth=auth, cursor=cursor_before, limit=limit)
    if fetch_result.get("error"):
        return {
            "events_fetched": 0,
            "merge_events_applied": 0,
            "undo_events_applied": 0,
            "events_skipped": 0,
            "cursor_before": cursor_before,
            "cursor_after": cursor_before,
            "error": fetch_result["error"],
        }

    events = fetch_result["events"]
    next_cursor = fetch_result.get("next_cursor")
    stats = {
        "events_fetched": len(events),
        "merge_events_applied": 0,
        "undo_events_applied": 0,
        "events_skipped": 0,
        "cursor_before": cursor_before,
        "cursor_after": cursor_before,
        "error": None,
    }

    for event in events:
        if not isinstance(event, dict):
            stats["error"] = "Invalid menu merge event in response payload"
            return stats

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
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("Menu merge pull failed for event %s: %s", event.get("remote_event_id"), exc)
            stats["error"] = str(exc)
            return stats

    cursor_after = cursor_before if next_cursor is None else str(next_cursor)
    try:
        set_menu_merge_pull_cursor(conn, cursor_after)
        conn.commit()
        stats["cursor_after"] = cursor_after
    except Exception as exc:
        conn.rollback()
        stats["error"] = str(exc)
        return stats

    return stats
