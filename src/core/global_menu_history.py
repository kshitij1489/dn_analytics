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


GLOBAL_MENU_HISTORY_PAGE_LIMIT = 500
GLOBAL_MENU_HISTORY_MAX_PAGES = 1000
# Every column the cached row carries except the key and the local cache stamp.
# Order matches the INSERT below so a fetched row compares as one tuple.
_HISTORY_CONTENT_COLUMNS = (
    "menu_group_id",
    "source_event_id",
    "source_kind",
    "event_type",
    "origin_restaurant_id",
    "actor",
    "attribution",
    "occurred_at",
    "server_ingested_at",
    "source",
    "target",
    "mutation_id",
    "is_undoable",
    "detail",
)
_SOURCE_KINDS = frozenset({"legacy_restaurant_event", "global_menu_event"})
_RESOLUTION_UNDO_EVENT_TYPES = frozenset(
    {"global_item.create", "global_variant.create", "global_locator.map"}
)
GLOBAL_MENU_HISTORY_FILTERS = frozenset(
    {"all", "global", "legacy", "system", "undoable"}
)
_SYSTEM_EVENT_TYPES = frozenset(
    {
        "global_catalog.verification_backfill",
        "global_menu.genesis",
    }
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
    if isinstance(value, str) and not value.strip():
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


def _dump_json(value: Optional[Dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


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
    """Atomically upsert one valid page and its paging checkpoint.

    A row whose cached copy already matches the served one byte for byte is
    left alone, so re-reading an unchanged page writes nothing. ``rows_written``
    reports the rows that actually changed, which is what tells the drain in
    ``pull_global_menu_history`` that it has caught up.
    """
    page = validate_global_menu_history_page(
        payload, capability=capability, page_cursor=page_cursor
    )
    rows_written = 0
    conn.execute("SAVEPOINT global_menu_history_page")
    try:
        for row in page["rows"]:
            values = (
                page["menu_group_id"],
                row["source_event_id"],
                row["source_kind"],
                row["event_type"],
                row["origin_restaurant_id"],
                row["actor"],
                _dump_json(row["attribution"]),
                row["occurred_at"],
                row["server_ingested_at"],
                _dump_json(row["source"]),
                _dump_json(row["target"]),
                row["mutation_id"],
                int(row["is_undoable"]),
                json.dumps(row["detail"], sort_keys=True, separators=(",", ":")),
            )
            existing = conn.execute(
                f"SELECT {', '.join(_HISTORY_CONTENT_COLUMNS)} "
                "FROM global_menu_history WHERE history_id=?",
                (row["history_id"],),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != page["menu_group_id"]:
                    raise GlobalMenuHistoryError(
                        f"History row {row['history_id']} is already owned by another menu group"
                    )
                if tuple(existing) == values:
                    continue
            rows_written += 1
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
                (row["history_id"], *values),
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
        "rows_written": rows_written,
        "history_ids": [row["history_id"] for row in page["rows"]],
        "next_cursor": page["next_cursor"],
        "has_more": page["has_more"],
    }


_HISTORY_SEEN_TABLE = "global_menu_history_seen"


def _reset_history_seen(conn) -> None:
    # A temp table rather than a parameter list: the drain has to name every
    # served row and the feed is not bounded by SQLite's variable limit.
    conn.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {_HISTORY_SEEN_TABLE} "
        "(history_id TEXT PRIMARY KEY)"
    )
    conn.execute(f"DELETE FROM {_HISTORY_SEEN_TABLE}")


def _record_history_seen(conn, history_ids: List[str]) -> None:
    if not history_ids:
        return
    conn.executemany(
        f"INSERT OR IGNORE INTO {_HISTORY_SEEN_TABLE} (history_id) VALUES (?)",
        [(history_id,) for history_id in history_ids],
    )


def _prune_unserved_history(conn, *, menu_group_id: str) -> int:
    """Drop cached rows the completed drain did not see.

    Only ever called after a drain that reached the end of the feed. The page
    is projected across the group's *current* members, so a restaurant leaving
    the group retires its legacy rows; without this they would sit in the cache
    and keep showing in Resolution History forever. An interrupted drain must
    never reach here — its unread tail is indistinguishable from retired rows.
    """
    cursor = conn.execute(
        f"""
        DELETE FROM global_menu_history
        WHERE menu_group_id=?
          AND history_id NOT IN (SELECT history_id FROM {_HISTORY_SEEN_TABLE})
        """,
        (menu_group_id,),
    )
    pruned = int(cursor.rowcount or 0)
    conn.commit()
    return pruned


def pull_global_menu_history(
    conn,
    *,
    auth: Optional[str] = None,
    page_limit: int = GLOBAL_MENU_HISTORY_PAGE_LIMIT,
    allow_profile_sync: bool = False,
) -> Dict[str, Any]:
    """Drain the whole history feed from its head; failures surface as warnings.

    Two properties of the served feed decide the shape of this loop:

    * It is ordered **newest-first** and `after` pages *backwards* into older
      rows, so an end-of-feed cursor can never be resumed from — persisting one
      pins the reader below every entry written later. Every drain therefore
      starts at the head, and the checkpoint the applier writes is only a
      within-drain resume point.
    * A row's rendering is not fixed once served. `is_undoable` reflects
      whether the undo preview *currently* permits it, and the page is
      projected across all **current** group members, so admitting a member
      interleaves its legacy rows deep in the feed (contract §25.10). Changes
      therefore do not always appear at the head, and no page can be taken as
      proof that the pages below it are unchanged.

    So the drain always runs to the end of the feed. What it does not do is
    write: :func:`apply_global_menu_history_page` skips rows whose cached copy
    already matches, which is where the per-sync cost actually went — a
    caught-up install re-reads the feed and writes nothing.
    """
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

    cursor = None
    pages = rows_applied = rows_written = 0
    _reset_history_seen(conn)
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
        rows_written += int(result["rows_written"])
        _record_history_seen(conn, result["history_ids"])
        cursor = result["next_cursor"]
        if result["has_more"]:
            continue
        # Complete drain only: every cached row for this group has now either
        # been re-served or is gone from the projection. A feed that served no
        # rows at all is the one case left alone — a genuinely empty group and
        # a server that answered wrongly look identical from here, and blanking
        # the audit view is the worse of the two outcomes.
        rows_pruned = (
            _prune_unserved_history(conn, menu_group_id=capability.menu_group_id)
            if rows_applied
            else 0
        )
        return {
            "status": "applied",
            "pages": pages,
            "rows_applied": rows_applied,
            "rows_written": rows_written,
            "rows_pruned": rows_pruned,
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
    if event_type == "global_menu.genesis":
        return False
    if not cached_is_undoable or not mutation_id:
        return False
    if capability.mutation_ready:
        return True
    return capability.resolution_ready and event_type in _RESOLUTION_UNDO_EVENT_TYPES


def _is_system_history_entry(
    *,
    source_kind: str,
    event_type: str,
    actor: Optional[str],
    source: Dict[str, Any],
    target: Dict[str, Any],
) -> bool:
    """Classify non-human audit noise without changing the stored projection."""
    if event_type in _SYSTEM_EVENT_TYPES or event_type.endswith(".backfill"):
        return True
    if actor in {"backfill"} or (actor and actor.startswith("system:")):
        return True
    # Older derived-assignment rows were projected without their merge kind.
    # Both snapshots are blank, which is also why the old UI rendered
    # ``menu_merge.applied -> menu_merge.applied``. Keep them reachable through
    # Legacy History, but make System Activity able to isolate that noise until
    # the next complete server history pull prunes it.
    return (
        source_kind == "legacy_restaurant_event"
        and not str(source.get("name") or "").strip()
        and not str(target.get("name") or "").strip()
    )


def _history_entry_matches(
    entry: Dict[str, Any],
    *,
    history_filter: str,
    restaurant_id: Optional[str],
) -> bool:
    if restaurant_id and entry.get("origin_restaurant_id") != restaurant_id:
        return False
    if history_filter == "all":
        return True
    if history_filter == "undoable":
        return bool(entry.get("is_undoable"))
    if history_filter == "legacy":
        return entry.get("source_kind") == "legacy_restaurant_event"
    if history_filter == "system":
        return bool(entry.get("is_system_event"))
    return (
        entry.get("source_kind") == "global_menu_event"
        and not entry.get("is_system_event")
    )


def list_cached_global_menu_history(
    conn,
    *,
    capability: GlobalMenuCapabilityStatus,
    limit: int,
    offset: int,
    history_filter: str = "all",
    restaurant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a filtered, paginated view of the unified history cache."""
    if history_filter not in GLOBAL_MENU_HISTORY_FILTERS:
        raise ValueError(f"Unsupported group history filter: {history_filter}")
    group_id = capability.menu_group_id
    unfiltered_total = int(
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
        """,
        (group_id,),
    ).fetchall()
    entries: List[Dict[str, Any]] = []
    available_restaurants: Dict[str, Optional[str]] = {}
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
        source_name = str(source.get("name") or "")
        target_name = str(target.get("name") or "")
        variant_assignments = detail.get("variant_reconciliation")
        entry = {
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
        entry["is_system_event"] = _is_system_history_entry(
            source_kind=entry["source_kind"],
            event_type=entry["event_type"],
            actor=entry["actor"],
            source=source,
            target=target,
        )
        origin_restaurant_id = str(entry.get("origin_restaurant_id") or "").strip()
        if origin_restaurant_id:
            attribution = entry.get("attribution") or {}
            restaurant_name = str(attribution.get("restaurant_name") or "").strip()
            if origin_restaurant_id not in available_restaurants or restaurant_name:
                available_restaurants[origin_restaurant_id] = restaurant_name or None
        if _history_entry_matches(
            entry,
            history_filter=history_filter,
            restaurant_id=restaurant_id,
        ):
            entries.append(entry)
    total = len(entries)
    return {
        "entries": entries[offset : offset + limit],
        "total": total,
        "unfiltered_total": unfiltered_total,
        "limit": limit,
        "offset": offset,
        "filter": history_filter,
        "restaurant_id": restaurant_id,
        "restaurants": [
            {
                "restaurant_id": origin_restaurant_id,
                "restaurant_name": available_restaurants[origin_restaurant_id],
            }
            for origin_restaurant_id in sorted(available_restaurants)
        ],
    }


__all__ = [
    "GlobalMenuHistoryError",
    "GLOBAL_MENU_HISTORY_FILTERS",
    "apply_global_menu_history_page",
    "get_global_menu_history_endpoint",
    "list_cached_global_menu_history",
    "pull_global_menu_history",
    "validate_global_menu_history_page",
]
