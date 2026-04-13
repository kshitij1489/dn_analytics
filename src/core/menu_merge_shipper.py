"""
Upload append-only menu merge events to cloud.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.config.client_learning_config import CLIENT_LEARNING_MENU_MERGE_INGEST_URL
from src.core.menu_merge_sync_events import (
    SCHEMA_VERSION,
    backfill_menu_merge_sync_events,
    has_menu_merge_sync_table,
)


BATCH_LIMIT_EVENTS = 200


def _select_unsent_events(conn, limit: int = BATCH_LIMIT_EVENTS) -> List[Dict[str, Any]]:
    if not has_menu_merge_sync_table(conn):
        return []

    rows = conn.execute(
        """
        SELECT event_id, payload
        FROM menu_merge_sync_events
        WHERE uploaded_at IS NULL
        ORDER BY occurred_at ASC, created_at ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    events: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        payload.setdefault("remote_event_id", row["event_id"])
        events.append(payload)
    return events


def _mark_events(conn, event_ids: List[str], uploaded_at: Optional[str], error: Optional[str]) -> None:
    if not event_ids:
        return

    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in event_ids)
    conn.execute(
        f"""
        UPDATE menu_merge_sync_events
        SET upload_attempted_at = ?,
            uploaded_at = ?,
            last_error = ?
        WHERE event_id IN ({placeholders})
        """,
        [now, uploaded_at, error] + event_ids,
    )
    conn.commit()


def upload_pending(
    conn,
    endpoint: Optional[str] = None,
    auth: Optional[str] = None,
    uploaded_by: Optional[Dict[str, str]] = None,
    uploaded_from: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    url = (endpoint or CLIENT_LEARNING_MENU_MERGE_INGEST_URL).strip()
    if not url or conn is None:
        return {"events_sent": 0, "backfilled_applied": 0, "error": None}

    backfill_counts = backfill_menu_merge_sync_events(conn)
    events = _select_unsent_events(conn)
    if not events:
        return {
            "events_sent": 0,
            "backfilled_applied": backfill_counts["applied"],
            "error": None,
        }

    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    payload: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "events": events}
    if uploaded_by:
        payload["uploaded_by"] = uploaded_by
    if uploaded_from:
        payload["uploaded_from"] = uploaded_from

    event_ids = [str(event["remote_event_id"]) for event in events]
    try:
        import requests

        response = requests.post(url, json=payload, headers=headers, timeout=60)
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}"
            _mark_events(conn, event_ids, uploaded_at=None, error=error)
            return {
                "events_sent": 0,
                "backfilled_applied": backfill_counts["applied"],
                "error": error,
            }
    except Exception as exc:
        error = str(exc)
        _mark_events(conn, event_ids, uploaded_at=None, error=error)
        return {
            "events_sent": 0,
            "backfilled_applied": backfill_counts["applied"],
            "error": error,
        }

    uploaded_at = datetime.now(timezone.utc).isoformat()
    _mark_events(conn, event_ids, uploaded_at=uploaded_at, error=None)
    return {
        "events_sent": len(events),
        "backfilled_applied": backfill_counts["applied"],
        "error": None,
    }
