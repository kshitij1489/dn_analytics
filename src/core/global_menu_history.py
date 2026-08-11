"""Pull, validate, cache, and expose the unified global-menu audit timeline."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.global_menu_schema import (
    GLOBAL_MENU_SCHEMA_VERSION,
    GlobalMenuCapabilityStatus,
    require_global_menu_capability,
    update_global_menu_state,
)
from src.core.global_menu_sync import _fetch_page


GLOBAL_MENU_HISTORY_PAGE_LIMIT = 100
GLOBAL_MENU_HISTORY_MAX_PAGES = 1000
_SOURCE_KINDS = frozenset({"legacy_restaurant_event", "global_menu_event"})
_RESOLUTION_UNDO_EVENT_TYPES = frozenset(
    {"global_item.create", "global_variant.create", "global_locator.map"}
)


class GlobalMenuHistoryError(RuntimeError):
    code = "global_menu_history_invalid"


def get_global_menu_history_endpoint(conn) -> Optional[str]:
    base_url, _ = get_cloud_sync_config(conn)
    if not base_url:
        return None
    return f"{base_url}/desktop-analytics-sync/global-menu/history"


def _nonblank_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GlobalMenuHistoryError(f"{field} must be a non-blank string")
    return value.strip()


def _optional_text(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    return _nonblank_text(value, field)


def _timestamp(value: Any, field: str) -> str:
    text = _nonblank_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GlobalMenuHistoryError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GlobalMenuHistoryError(f"{field} must include a timezone offset")
    return text


def _json_object(value: Any, field: str, *, nullable: bool = False):
    if value is None and nullable:
        return None
    if not isinstance(value, dict):
        suffix = " or null" if nullable else ""
        raise GlobalMenuHistoryError(f"{field} must be an object{suffix}")
    return dict(value)


def _snapshot(value: Any, field: str) -> Optional[Dict[str, Any]]:
    snapshot = _json_object(value, field, nullable=True)
    if snapshot is None:
        return None
    missing = {"global_item_id", "name", "item_type"} - set(snapshot)
    if missing:
        raise GlobalMenuHistoryError(
            f"{field} omitted field(s): {', '.join(sorted(missing))}"
        )
    global_item_id = _optional_text(
        snapshot.get("global_item_id"), f"{field}.global_item_id"
    )
    name = snapshot.get("name")
    item_type = snapshot.get("item_type")
    if not isinstance(name, str):
        raise GlobalMenuHistoryError(f"{field}.name must be a string")
    if not isinstance(item_type, str):
        raise GlobalMenuHistoryError(f"{field}.item_type must be a string")
    return {
        "global_item_id": global_item_id,
        "name": name,
        "item_type": item_type,
    }


def _normalize_history_row(row: Any, index: int) -> Dict[str, Any]:
    field = f"rows[{index}]"
    if not isinstance(row, dict):
        raise GlobalMenuHistoryError(f"{field} must be an object")
    history_id = _nonblank_text(row.get("history_id"), f"{field}.history_id")
    source_event_id = _nonblank_text(
        row.get("source_event_id"), f"{field}.source_event_id"
    )
    source_kind = _nonblank_text(row.get("source_kind"), f"{field}.source_kind")
    if source_kind not in _SOURCE_KINDS:
        raise GlobalMenuHistoryError(f"{field}.source_kind is invalid")
    event_type = _nonblank_text(row.get("event_type"), f"{field}.event_type")
    origin_restaurant_id = _optional_text(
        row.get("origin_restaurant_id"), f"{field}.origin_restaurant_id"
    )
    actor = _optional_text(row.get("actor"), f"{field}.actor")
    attribution = _json_object(
        row.get("attribution"), f"{field}.attribution", nullable=True
    )
    source = _snapshot(row.get("source"), f"{field}.source")
    target = _snapshot(row.get("target"), f"{field}.target")
    mutation_id = _optional_text(row.get("mutation_id"), f"{field}.mutation_id")
    is_undoable = row.get("is_undoable")
    if not isinstance(is_undoable, bool):
        raise GlobalMenuHistoryError(f"{field}.is_undoable must be a boolean")
    detail = _json_object(row.get("detail"), f"{field}.detail")
    if is_undoable and mutation_id is None:
        raise GlobalMenuHistoryError(
            f"{field} cannot be undoable without a mutation_id"
        )
    if source_kind == "legacy_restaurant_event" and (
        mutation_id is not None or is_undoable
    ):
        raise GlobalMenuHistoryError(
            f"{field} gives a legacy restaurant event false undo authority"
        )
    return {
        "history_id": history_id,
        "source_event_id": source_event_id,
        "source_kind": source_kind,
        "event_type": event_type,
        "origin_restaurant_id": origin_restaurant_id,
        "actor": actor,
        "attribution": attribution,
        "occurred_at": _timestamp(row.get("occurred_at"), f"{field}.occurred_at"),
        "server_ingested_at": _timestamp(
            row.get("server_ingested_at"), f"{field}.server_ingested_at"
        ),
        "source": source,
        "target": target,
        "mutation_id": mutation_id,
        "is_undoable": is_undoable,
        "detail": detail,
    }


def validate_global_menu_history_page(
    payload: Any,
    *,
    capability: GlobalMenuCapabilityStatus,
    page_cursor: Optional[str],
) -> Dict[str, Any]:
    """Return the exact cacheable shape or reject the complete page."""
    if not isinstance(payload, dict):
        raise GlobalMenuHistoryError("Global menu history response must be an object")
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise GlobalMenuHistoryError("schema_version must be an integer")
    if schema_version != GLOBAL_MENU_SCHEMA_VERSION:
        raise GlobalMenuHistoryError(
            f"Unsupported global menu history schema version: {schema_version}"
        )
    menu_group_id = _nonblank_text(payload.get("menu_group_id"), "menu_group_id")
    if menu_group_id != capability.menu_group_id:
        raise GlobalMenuHistoryError(
            f"Cross-group history rejected: expected {capability.menu_group_id}, "
            f"received {menu_group_id}"
        )
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise GlobalMenuHistoryError("rows must be an array")
    rows = [_normalize_history_row(row, index) for index, row in enumerate(raw_rows)]
    history_ids = [row["history_id"] for row in rows]
    if len(history_ids) != len(set(history_ids)):
        raise GlobalMenuHistoryError("A history page contains duplicate history_id values")

    has_more = payload.get("has_more")
    if not isinstance(has_more, bool):
        raise GlobalMenuHistoryError("has_more must be a boolean")
    next_cursor = payload.get("next_cursor")
    if next_cursor is not None:
        next_cursor = _nonblank_text(next_cursor, "next_cursor")
    if has_more and not rows:
        raise GlobalMenuHistoryError("A non-final history page cannot be empty")
    if has_more and next_cursor is None:
        raise GlobalMenuHistoryError("A non-final history page omitted next_cursor")
    if has_more and next_cursor == page_cursor:
        raise GlobalMenuHistoryError("History paging did not advance")
    if not has_more and next_cursor is not None:
        raise GlobalMenuHistoryError("A final history page must clear next_cursor")
    return {
        "schema_version": schema_version,
        "menu_group_id": menu_group_id,
        "rows": rows,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def apply_global_menu_history_page(
    conn,
    payload: Any,
    *,
    capability: GlobalMenuCapabilityStatus,
    page_cursor: Optional[str],
) -> Dict[str, Any]:
    """Atomically upsert one valid page and its paging checkpoint."""
    page = validate_global_menu_history_page(
        payload, capability=capability, page_cursor=page_cursor
    )
    conn.execute("SAVEPOINT global_menu_history_page")
    try:
        for row in page["rows"]:
            existing = conn.execute(
                "SELECT menu_group_id FROM global_menu_history WHERE history_id=?",
                (row["history_id"],),
            ).fetchone()
            if existing is not None and str(existing[0]) != page["menu_group_id"]:
                raise GlobalMenuHistoryError(
                    f"History row {row['history_id']} is already owned by another menu group"
                )
            conn.execute(
                """
                INSERT INTO global_menu_history (
                    history_id, menu_group_id, source_event_id, source_kind,
                    event_type, origin_restaurant_id, actor, attribution,
                    occurred_at, server_ingested_at, source, target,
                    mutation_id, is_undoable, detail, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(history_id) DO UPDATE SET
                    menu_group_id=excluded.menu_group_id,
                    source_event_id=excluded.source_event_id,
                    source_kind=excluded.source_kind,
                    event_type=excluded.event_type,
                    origin_restaurant_id=excluded.origin_restaurant_id,
                    actor=excluded.actor,
                    attribution=excluded.attribution,
                    occurred_at=excluded.occurred_at,
                    server_ingested_at=excluded.server_ingested_at,
                    source=excluded.source,
                    target=excluded.target,
                    mutation_id=excluded.mutation_id,
                    is_undoable=excluded.is_undoable,
                    detail=excluded.detail,
                    cached_at=CURRENT_TIMESTAMP
                """,
                (
                    row["history_id"],
                    page["menu_group_id"],
                    row["source_event_id"],
                    row["source_kind"],
                    row["event_type"],
                    row["origin_restaurant_id"],
                    row["actor"],
                    json.dumps(row["attribution"], sort_keys=True, separators=(",", ":"))
                    if row["attribution"] is not None
                    else None,
                    row["occurred_at"],
                    row["server_ingested_at"],
                    json.dumps(row["source"], sort_keys=True, separators=(",", ":"))
                    if row["source"] is not None
                    else None,
                    json.dumps(row["target"], sort_keys=True, separators=(",", ":"))
                    if row["target"] is not None
                    else None,
                    row["mutation_id"],
                    int(row["is_undoable"]),
                    json.dumps(row["detail"], sort_keys=True, separators=(",", ":")),
                ),
            )
        checkpoint = page["next_cursor"] if page["has_more"] else None
        update_global_menu_state(conn, history_cursor=checkpoint)
        conn.execute("RELEASE SAVEPOINT global_menu_history_page")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT global_menu_history_page")
        conn.execute("RELEASE SAVEPOINT global_menu_history_page")
        raise
    conn.commit()
    return {
        "status": "applied",
        "rows_applied": len(page["rows"]),
        "next_cursor": page["next_cursor"],
        "has_more": page["has_more"],
    }


def pull_global_menu_history(
    conn,
    *,
    auth: Optional[str] = None,
    page_limit: int = GLOBAL_MENU_HISTORY_PAGE_LIMIT,
    allow_profile_sync: bool = False,
) -> Dict[str, Any]:
    """Drain the opaque history pages; failures are returned for warning surfaces."""
    if page_limit < 1 or page_limit > 500:
        return {"status": "error", "error": "History page limit must be from 1 to 500"}
    capability = require_global_menu_capability(
        conn, allow_profile_sync=allow_profile_sync
    )
    endpoint = get_global_menu_history_endpoint(conn)
    if auth is None:
        _, auth = get_cloud_sync_config(conn)
    if not endpoint or not auth:
        return {"status": "error", "error": "Global menu history pull is not configured"}

    cursor = capability.history_cursor
    pages = rows_applied = 0
    for _page_number in range(GLOBAL_MENU_HISTORY_MAX_PAGES):
        page = _fetch_page(
            conn,
            endpoint,
            auth=auth,
            cursor=cursor,
            limit=page_limit,
        )
        if page.get("error"):
            return {"status": "error", "error": str(page["error"]), "pages": pages}
        try:
            result = apply_global_menu_history_page(
                conn,
                page,
                capability=capability,
                page_cursor=cursor,
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc), "pages": pages}
        pages += 1
        rows_applied += int(result["rows_applied"])
        cursor = result["next_cursor"]
        if result["has_more"]:
            continue
        return {
            "status": "applied",
            "pages": pages,
            "rows_applied": rows_applied,
        }
    return {
        "status": "error",
        "error": "Global menu history pull exceeded the page safety limit",
        "pages": pages,
    }


def _read_json_object(value: Any) -> Dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stable_merge_id(history_id: str) -> int:
    # The existing frontend envelope requires a JavaScript-safe integer even
    # though the revision-1.7 history key is opaque text.
    value = int.from_bytes(
        hashlib.sha256(history_id.encode("utf-8")).digest()[:6], "big"
    )
    return value or 1


def _current_undo_permission(
    *,
    capability: GlobalMenuCapabilityStatus,
    event_type: str,
    cached_is_undoable: bool,
    mutation_id: Optional[str],
) -> bool:
    if not cached_is_undoable or not mutation_id:
        return False
    if capability.mutation_ready:
        return True
    return capability.resolution_ready and event_type in _RESOLUTION_UNDO_EVENT_TYPES


def list_cached_global_menu_history(
    conn,
    *,
    capability: GlobalMenuCapabilityStatus,
    limit: int,
    offset: int,
) -> Dict[str, Any]:
    """Return the legacy frontend envelope from the unified history cache."""
    group_id = capability.menu_group_id
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM global_menu_history WHERE menu_group_id=?",
            (group_id,),
        ).fetchone()[0]
        or 0
    )
    rows = conn.execute(
        """
        SELECT history_id, source_event_id, source_kind, event_type,
               origin_restaurant_id, actor, attribution, occurred_at,
               server_ingested_at, source, target, mutation_id,
               is_undoable, detail
        FROM global_menu_history
        WHERE menu_group_id=?
        ORDER BY occurred_at DESC, source_kind ASC, source_event_id DESC
        LIMIT ? OFFSET ?
        """,
        (group_id, limit, offset),
    ).fetchall()
    entries: List[Dict[str, Any]] = []
    for row in rows:
        source = _read_json_object(row[9])
        target = _read_json_object(row[10])
        detail = _read_json_object(row[13])
        mutation_id = str(row[11]) if row[11] is not None else None
        can_undo = _current_undo_permission(
            capability=capability,
            event_type=str(row[3]),
            cached_is_undoable=bool(row[12]),
            mutation_id=mutation_id,
        )
        history_id = str(row[0])
        source_id = str(source.get("global_item_id") or f"{history_id}:source")
        target_id = str(target.get("global_item_id") or f"{history_id}:target")
        source_name = str(source.get("name") or row[3])
        target_name = str(target.get("name") or source_name)
        variant_assignments = detail.get("variant_reconciliation")
        entries.append(
            {
                "merge_id": _stable_merge_id(history_id),
                "history_id": history_id,
                "source_event_id": str(row[1]),
                "source_kind": str(row[2]),
                "event_type": str(row[3]),
                "origin_restaurant_id": row[4],
                "actor": row[5],
                "attribution": _read_json_object(row[6]) if row[6] is not None else None,
                "source_id": source_id,
                "target_id": target_id,
                "source_name": source_name,
                "target_name": target_name,
                "source": source or None,
                "target": target or None,
                "merged_at": str(row[7]),
                "server_ingested_at": str(row[8]),
                "detail": detail,
                "variant_assignments": (
                    variant_assignments if isinstance(variant_assignments, list) else []
                ),
                "is_undoable": can_undo,
                "global_mutation_id": mutation_id if can_undo else None,
                "global_menu_group_id": group_id,
            }
        )
    return {"entries": entries, "total": total, "limit": limit, "offset": offset}


__all__ = [
    "GlobalMenuHistoryError",
    "apply_global_menu_history_page",
    "get_global_menu_history_endpoint",
    "list_cached_global_menu_history",
    "pull_global_menu_history",
    "validate_global_menu_history_page",
]
