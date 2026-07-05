"""
Quarantine (dead-letter) store for sync events that could not be applied or pushed.

Pull loops park un-appliable events here instead of silently dropping them, so
the cursor can keep advancing without losing the event (plan: Phase C1).
Shippers park server-rejected push events here so they stop blocking the push
queue but stay visible (plan: Phase C2). Rows are surfaced through
GET /api/menu/sync-conflicts and cleared by a successful retry or an explicit
user dismiss.

Streams currently in use:
  - 'menu_merge' / 'mapping_verification'        (pull failures)
  - 'menu_merge_push' / 'mapping_verification_push' / 'customer_merge_push'
                                                  (server-rejected push events)
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def ensure_menu_sync_quarantine_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_sync_event_quarantine (
            remote_event_id TEXT PRIMARY KEY,
            stream TEXT NOT NULL,
            payload TEXT NOT NULL,
            error TEXT,
            fail_count INTEGER DEFAULT 1,
            first_failed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_failed_at TEXT,
            resolved_at TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_menu_sync_event_quarantine_stream
        ON menu_sync_event_quarantine(stream, resolved_at);
        """
    )


def quarantine_event(
    conn,
    stream: str,
    remote_event_id: str,
    payload: Any,
    error: Optional[str],
) -> None:
    """Insert or re-fail a quarantined event, bumping fail_count on repeats."""
    remote_event_id = str(remote_event_id or "").strip()
    if not remote_event_id:
        return

    ensure_menu_sync_quarantine_table(conn)
    if not isinstance(payload, str):
        payload = json.dumps(payload, sort_keys=True, default=str)
    conn.execute(
        """
        INSERT INTO menu_sync_event_quarantine (
            remote_event_id, stream, payload, error, fail_count, last_failed_at
        )
        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(remote_event_id) DO UPDATE SET
            stream = excluded.stream,
            payload = excluded.payload,
            error = excluded.error,
            fail_count = fail_count + 1,
            last_failed_at = CURRENT_TIMESTAMP,
            resolved_at = NULL
        """,
        (remote_event_id, stream, payload, error),
    )


def fetch_unresolved_quarantined_events(conn, stream: str) -> List[Dict[str, Any]]:
    ensure_menu_sync_quarantine_table(conn)
    rows = conn.execute(
        """
        SELECT remote_event_id, stream, payload, error, fail_count
        FROM menu_sync_event_quarantine
        WHERE stream = ? AND resolved_at IS NULL
        ORDER BY first_failed_at ASC
        """,
        (stream,),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_quarantined_event_resolved(conn, remote_event_id: str) -> bool:
    ensure_menu_sync_quarantine_table(conn)
    cur = conn.execute(
        """
        UPDATE menu_sync_event_quarantine
        SET resolved_at = CURRENT_TIMESTAMP
        WHERE remote_event_id = ? AND resolved_at IS NULL
        """,
        (remote_event_id,),
    )
    return bool(cur.rowcount)


def record_quarantine_retry_failure(conn, remote_event_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE menu_sync_event_quarantine
        SET error = ?,
            fail_count = fail_count + 1,
            last_failed_at = CURRENT_TIMESTAMP
        WHERE remote_event_id = ?
        """,
        (error, remote_event_id),
    )


def _event_summary_from_payload(raw_payload: Any) -> Dict[str, Any]:
    """Best-effort human-readable context from the quarantined event snapshot."""
    try:
        payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    except (TypeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return {}

    summary: Dict[str, Any] = {}
    if payload.get("event_type"):
        summary["event_type"] = str(payload["event_type"])
    source_item = payload.get("source_item")
    target_item = payload.get("target_item")
    if isinstance(source_item, dict) and source_item.get("name"):
        summary["source_name"] = str(source_item["name"])
    if isinstance(target_item, dict) and target_item.get("name"):
        summary["target_name"] = str(target_item["name"])
    if payload.get("order_item_id"):
        summary["order_item_id"] = str(payload["order_item_id"])
    if payload.get("occurred_at"):
        summary["occurred_at"] = str(payload["occurred_at"])
    return summary


def list_sync_conflicts(conn, include_resolved: bool = False) -> List[Dict[str, Any]]:
    """Quarantined events for /api/menu/sync-conflicts, newest failures first."""
    ensure_menu_sync_quarantine_table(conn)
    where = "" if include_resolved else "WHERE resolved_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT remote_event_id, stream, payload, error, fail_count,
               first_failed_at, last_failed_at, resolved_at
        FROM menu_sync_event_quarantine
        {where}
        ORDER BY COALESCE(last_failed_at, first_failed_at) DESC
        """
    ).fetchall()

    conflicts = []
    for row in rows:
        entry = dict(row)
        entry["summary"] = _event_summary_from_payload(entry.pop("payload", None))
        conflicts.append(entry)
    return conflicts


def dismiss_sync_conflict(conn, remote_event_id: str) -> bool:
    """User-acknowledged dismissal: stops retries, keeps the row for audit."""
    return mark_quarantined_event_resolved(conn, remote_event_id)
