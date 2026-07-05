"""
Pull and apply menu mapping verification events from cloud.

Pull order (documented in cloud_pull_orchestrator): menu bootstrap → mapping verifications →
menu merges → customer merges. Mapping verifications fine-tune is_verified on menu_item_variants
before structural merge replay.

Conflict resolution (Phase 1): **last-write-wins** — the most recently pulled event
for a given order_item_id determines its is_verified value. No occurred_at comparison
is performed; cursor ordering from the server is assumed to be authoritative. If two
installs emit conflicting events (one verifies, one reopens), the last event ingested
by the server and subsequently pulled wins. A future phase may add timestamp-based or
vector-clock conflict resolution if needed.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.menu_assignment_apply import coerce_server_seq
from src.core.menu_assignment_schema import ensure_assignment_sync_schema
from src.core.menu_mapping_verification_sync_events import (
    EVENT_BULK_VERIFIED,
    EVENT_REOPENED,
    EVENT_VERIFIED,
)
from src.core.menu_sync_quarantine import (
    ensure_menu_sync_quarantine_table,
    fetch_unresolved_quarantined_events,
    mark_quarantined_event_resolved,
    quarantine_event,
    record_quarantine_retry_failure,
)
from src.core.sync_cursor_migration import ensure_sync_cursor_schema

logger = logging.getLogger(__name__)

MENU_MAPPING_VERIFICATION_PULL_CURSOR_KEY = "menu_mapping_verification_pull_cursor"
QUARANTINE_STREAM_MAPPING_VERIFICATION = "mapping_verification"
DEFAULT_PULL_LIMIT = 100


def get_menu_mapping_verification_pull_endpoint(conn) -> Optional[str]:
    from src.core.config.client_learning_config import CLIENT_LEARNING_MENU_MAPPING_VERIFICATION_PULL_URL

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url}/desktop-analytics-sync/menu-mapping-verifications"
    return CLIENT_LEARNING_MENU_MAPPING_VERIFICATION_PULL_URL or None


def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_mapping_verification_remote_events (
            remote_event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            remote_cursor TEXT,
            occurred_at TEXT,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_mapping_verification_deferred (
            remote_event_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    ensure_menu_sync_quarantine_table(conn)
    ensure_sync_cursor_schema(conn)
    ensure_assignment_sync_schema(conn)


def get_menu_mapping_verification_pull_cursor(conn) -> Optional[str]:
    _ensure_tables(conn)
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = ? LIMIT 1",
        (MENU_MAPPING_VERIFICATION_PULL_CURSOR_KEY,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def set_menu_mapping_verification_pull_cursor(conn, cursor: Optional[str]) -> None:
    _ensure_tables(conn)
    if cursor is None:
        conn.execute("DELETE FROM system_config WHERE key = ?", (MENU_MAPPING_VERIFICATION_PULL_CURSOR_KEY,))
        return
    conn.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (MENU_MAPPING_VERIFICATION_PULL_CURSOR_KEY, cursor),
    )


def _remote_event_exists(conn, remote_event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM menu_mapping_verification_remote_events WHERE remote_event_id = ? LIMIT 1",
        (remote_event_id,),
    ).fetchone()
    return row is not None


def _record_remote_event(
    conn,
    remote_event_id: str,
    event_type: str,
    payload: Dict[str, Any],
    remote_cursor: Optional[str],
    occurred_at: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT INTO menu_mapping_verification_remote_events (
            remote_event_id,
            event_type,
            payload,
            remote_cursor,
            occurred_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            remote_event_id,
            event_type,
            json.dumps(payload, sort_keys=True, default=str),
            remote_cursor,
            occurred_at,
        ),
    )


def _sync_menu_items_after_mapping_verified(conn, menu_item_ids: Set[str]) -> None:
    for menu_item_id in menu_item_ids:
        if not menu_item_id:
            continue
        row = conn.execute(
            "SELECT COUNT(*) FROM menu_item_variants WHERE menu_item_id = ? AND is_verified = 0",
            (menu_item_id,),
        ).fetchone()
        unresolved = int(row[0] or 0) if row else 0
        conn.execute(
            """
            UPDATE menu_items
            SET is_verified = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = ?
            """,
            (1 if unresolved == 0 else 0, menu_item_id),
        )


def _apply_verification_by_order_item_id(
    conn,
    order_item_id: str,
    is_verified: int,
    server_seq: Optional[int] = None,
) -> Optional[str]:
    """
    Set is_verified on the mapping row keyed by order_item_id (POS-canonical).
    Returns menu_item_id if the event was handled (row exists), else None.

    Last-write-wins in stream order, with the assignment seq guard (plan C3.5):
    if the row already carries an assignment_seq higher than this event's
    server_seq, the flag write is skipped as stale. Still flag-only by design —
    mapping corrections ride the merge stream. assignment_seq is not stamped
    here because verification events sequence on a different server table.
    """
    row = conn.execute(
        """
        SELECT menu_item_id, assignment_seq
        FROM menu_item_variants
        WHERE order_item_id = ?
        LIMIT 1
        """,
        (order_item_id,),
    ).fetchone()
    if row is None:
        return None

    menu_item_id = str(row["menu_item_id"]) if row["menu_item_id"] else None
    row_seq = coerce_server_seq(row["assignment_seq"])
    event_seq = coerce_server_seq(server_seq)
    if row_seq is not None and event_seq is not None and event_seq < row_seq:
        # Stale relative to a newer assignment; treat as handled without writing.
        return menu_item_id

    conn.execute(
        """
        UPDATE menu_item_variants
        SET is_verified = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE order_item_id = ?
        """,
        (is_verified, order_item_id),
    )
    return menu_item_id


def _defer_event(conn, remote_event_id: str, payload: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO menu_mapping_verification_deferred (remote_event_id, payload, created_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (remote_event_id, json.dumps(payload, sort_keys=True, default=str)),
    )


def flush_deferred_menu_mapping_verifications(conn) -> Dict[str, int]:
    """
    Retry deferred verification applies (e.g. order_item_id row created after POS ingest).
    """
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT remote_event_id, payload FROM menu_mapping_verification_deferred ORDER BY created_at ASC"
    ).fetchall()
    stats = {"attempted": 0, "cleared": 0}
    for row in rows:
        stats["attempted"] += 1
        remote_event_id = str(row["remote_event_id"])
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            conn.execute("DELETE FROM menu_mapping_verification_deferred WHERE remote_event_id = ?", (remote_event_id,))
            continue
        if not isinstance(payload, dict):
            conn.execute("DELETE FROM menu_mapping_verification_deferred WHERE remote_event_id = ?", (remote_event_id,))
            continue

        event_type = str(payload.get("event_type") or "")
        mids: Set[str] = set()
        ok = False
        all_resolved = True  # tracks if every row in a bulk event was applied
        try:
            if event_type == EVENT_VERIFIED:
                oid = str(payload.get("order_item_id") or "").strip()
                target = int(payload.get("is_verified", 1))
                if oid:
                    mid = _apply_verification_by_order_item_id(conn, oid, target, server_seq=payload.get("server_seq"))
                    if mid:
                        ok = True
                        mids.add(mid)
                    else:
                        all_resolved = False
            elif event_type == EVENT_BULK_VERIFIED:
                mappings = payload.get("mappings")
                if isinstance(mappings, list):
                    for m in mappings:
                        if not isinstance(m, dict):
                            continue
                        oid = str(m.get("order_item_id") or "").strip()
                        target = int(m.get("is_verified", 1))
                        if not oid:
                            continue
                        mid = _apply_verification_by_order_item_id(conn, oid, target, server_seq=payload.get("server_seq"))
                        if mid:
                            ok = True
                            mids.add(mid)
                        else:
                            all_resolved = False
            elif event_type == EVENT_REOPENED:
                oid = str(payload.get("order_item_id") or "").strip()
                target = int(payload.get("is_verified", 0))
                if oid:
                    mid = _apply_verification_by_order_item_id(conn, oid, target, server_seq=payload.get("server_seq"))
                    if mid:
                        ok = True
                        mids.add(mid)
                    else:
                        all_resolved = False
        except Exception:
            conn.rollback()
            raise

        if ok:
            _sync_menu_items_after_mapping_verified(conn, mids)
        # Only remove the deferred record when every row has been handled;
        # for bulk events some rows may still be missing their order_item_id.
        if ok and all_resolved:
            conn.execute(
                "DELETE FROM menu_mapping_verification_deferred WHERE remote_event_id = ?",
                (remote_event_id,),
            )
            stats["cleared"] += 1
        conn.commit()
    return stats


def _apply_remote_event(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    if not remote_event_id:
        raise ValueError("Mapping verification event is missing remote_event_id")
    if _remote_event_exists(conn, remote_event_id):
        return {"status": "duplicate"}

    event_type = str(event.get("event_type") or "").strip()
    occurred_at = str(event.get("occurred_at") or "")
    menu_item_ids: Set[str] = set()
    deferred = False

    if event_type == EVENT_VERIFIED:
        oid = str(event.get("order_item_id") or "").strip()
        target = int(event.get("is_verified", 1))
        if not oid:
            raise ValueError("mapping.verified requires order_item_id")
        row = conn.execute(
            "SELECT 1 FROM menu_item_variants WHERE order_item_id = ? LIMIT 1",
            (oid,),
        ).fetchone()
        if not row:
            deferred = True
            _defer_event(conn, remote_event_id, event)
        else:
            mid = _apply_verification_by_order_item_id(conn, oid, target, server_seq=event.get("server_seq"))
            if not mid:
                deferred = True
                _defer_event(conn, remote_event_id, event)
            else:
                menu_item_ids.add(mid)
    elif event_type == EVENT_BULK_VERIFIED:
        mappings = event.get("mappings")
        if not isinstance(mappings, list) or not mappings:
            raise ValueError("mapping.bulk_verified requires non-empty mappings")
        # Partial application: apply rows whose order_item_id exists now,
        # defer the whole event only if ANY row is missing (the deferred
        # copy will retry all rows; already-applied rows are idempotent UPDATEs).
        has_missing = False
        prepared: List[tuple] = []
        for m in mappings:
            if not isinstance(m, dict):
                continue
            oid = str(m.get("order_item_id") or "").strip()
            target = int(m.get("is_verified", 1))
            if not oid:
                continue
            row = conn.execute(
                "SELECT 1 FROM menu_item_variants WHERE order_item_id = ? LIMIT 1",
                (oid,),
            ).fetchone()
            if not row:
                has_missing = True
                continue  # skip this row for now, but keep processing others
            prepared.append((oid, target))
        for oid, target in prepared:
            mid = _apply_verification_by_order_item_id(conn, oid, target, server_seq=event.get("server_seq"))
            if mid:
                menu_item_ids.add(mid)
        if has_missing:
            deferred = True
            _defer_event(conn, remote_event_id, event)
    elif event_type == EVENT_REOPENED:
        oid = str(event.get("order_item_id") or "").strip()
        target = int(event.get("is_verified", 0))
        if not oid:
            raise ValueError("mapping.reopened requires order_item_id")
        row = conn.execute(
            "SELECT 1 FROM menu_item_variants WHERE order_item_id = ? LIMIT 1",
            (oid,),
        ).fetchone()
        if not row:
            deferred = True
            _defer_event(conn, remote_event_id, event)
        else:
            mid = _apply_verification_by_order_item_id(conn, oid, target, server_seq=event.get("server_seq"))
            if not mid:
                deferred = True
                _defer_event(conn, remote_event_id, event)
            else:
                menu_item_ids.add(mid)
    else:
        raise ValueError(f"Unsupported mapping verification event_type '{event_type}'")

    _record_remote_event(conn, remote_event_id, event_type, event, remote_cursor, occurred_at)
    _sync_menu_items_after_mapping_verified(conn, menu_item_ids)
    return {"status": "applied", "deferred": deferred}


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


def retry_quarantined_menu_mapping_verification_events(conn) -> Dict[str, int]:
    """
    Re-attempt quarantined mapping verification events at the start of each pull.

    Mirrors flush_deferred_menu_mapping_verifications, but for events that
    failed with an error rather than a missing order_item_id row.
    """
    stats = {"attempted": 0, "resolved": 0}
    for row in fetch_unresolved_quarantined_events(conn, QUARANTINE_STREAM_MAPPING_VERIFICATION):
        stats["attempted"] += 1
        remote_event_id = str(row["remote_event_id"])
        try:
            event = json.loads(row["payload"])
            if not isinstance(event, dict):
                raise ValueError("Quarantined payload is not a JSON object")
            _apply_remote_event(conn, event, None)
            mark_quarantined_event_resolved(conn, remote_event_id)
            conn.commit()
            stats["resolved"] += 1
        except Exception as exc:
            conn.rollback()
            record_quarantine_retry_failure(conn, remote_event_id, str(exc))
            conn.commit()
    return stats


def pull_and_apply_menu_mapping_verification_events(
    conn,
    endpoint: str,
    auth: Optional[str] = None,
    limit: int = DEFAULT_PULL_LIMIT,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_tables(conn)
    retry_stats = retry_quarantined_menu_mapping_verification_events(conn)

    cursor_before = cursor if cursor is not None else get_menu_mapping_verification_pull_cursor(conn)
    fetch_result = _fetch_remote_events(endpoint, auth=auth, cursor=cursor_before, limit=limit)
    if fetch_result.get("error"):
        return {
            "events_fetched": 0,
            "events_applied": 0,
            "events_skipped": 0,
            "quarantine_retried": retry_stats["attempted"],
            "quarantine_resolved": retry_stats["resolved"],
            "cursor_before": cursor_before,
            "cursor_after": cursor_before,
            "error": fetch_result["error"],
        }

    events = fetch_result["events"]
    next_cursor = fetch_result.get("next_cursor")
    stats: Dict[str, Any] = {
        "events_fetched": len(events),
        "events_applied": 0,
        "events_skipped": 0,
        "events_failed": 0,
        "events_quarantined": 0,
        "deferred": 0,
        "quarantine_retried": retry_stats["attempted"],
        "quarantine_resolved": retry_stats["resolved"],
        "cursor_before": cursor_before,
        "cursor_after": cursor_before,
        "error": None,
        "last_event_error": None,
    }

    for event in events:
        if not isinstance(event, dict):
            stats["events_failed"] += 1
            stats["last_event_error"] = "Invalid mapping verification event in response payload"
            logger.warning("Menu mapping verification pull skipping malformed event in response payload")
            continue

        try:
            result = _apply_remote_event(conn, event, next_cursor)
            if result["status"] == "applied":
                stats["events_applied"] += 1
                if result.get("deferred"):
                    stats["deferred"] += 1
            else:
                stats["events_skipped"] += 1
            conn.commit()
        except Exception as exc:
            # One bad event must not stall the stream forever (it previously
            # aborted the pull without advancing the cursor). Quarantine it and
            # keep going; the cursor advances past it and the retry pass / user
            # dismissal handles the quarantined copy.
            conn.rollback()
            stats["events_failed"] += 1
            stats["last_event_error"] = str(exc)
            remote_event_id = str(event.get("remote_event_id") or "").strip()
            if remote_event_id:
                try:
                    quarantine_event(
                        conn,
                        QUARANTINE_STREAM_MAPPING_VERIFICATION,
                        remote_event_id,
                        event,
                        str(exc),
                    )
                    conn.commit()
                    stats["events_quarantined"] += 1
                except Exception:
                    conn.rollback()
                    logger.exception(
                        "Failed to quarantine mapping verification event %s", remote_event_id
                    )
            logger.warning(
                "Menu mapping verification pull quarantined event %s: %s",
                event.get("remote_event_id"),
                exc,
            )
            continue

    cursor_after = cursor_before if next_cursor is None else str(next_cursor)
    try:
        set_menu_mapping_verification_pull_cursor(conn, cursor_after)
        conn.commit()
        stats["cursor_after"] = cursor_after
    except Exception as exc:
        conn.rollback()
        stats["error"] = str(exc)
        return stats

    flush_deferred_menu_mapping_verifications(conn)
    return stats
