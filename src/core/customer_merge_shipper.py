"""
Customer merge shipper: upload append-only customer merge events to cloud.

Follows the same pattern as the existing cloud shippers:
  - read pending rows from a local sync table
  - POST them to a dedicated ingest endpoint
  - mark uploaded_at on success
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.config.client_learning_config import (
    CLIENT_LEARNING_CUSTOMER_MERGE_INGEST_URL,
)
from src.core.customer_merge_sync_events import (
    SCHEMA_VERSION,
    backfill_customer_merge_sync_events,
    has_customer_merge_sync_table,
)

BATCH_LIMIT_EVENTS = 200


def _select_unsent_events(conn, limit: int = BATCH_LIMIT_EVENTS) -> List[Dict[str, Any]]:
    if not has_customer_merge_sync_table(conn):
        return []

    rows = conn.execute(
        """
        SELECT event_id, payload
        FROM customer_merge_sync_events
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
        UPDATE customer_merge_sync_events
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
) -> Dict[str, Any]:
    """
    Upload unsent customer merge events and mark them as uploaded on success.

    Returns:
        {"events_sent": int, "backfilled_applied": int, "backfilled_undone": int, "error": str or None}
    """
    url = (endpoint or CLIENT_LEARNING_CUSTOMER_MERGE_INGEST_URL).strip()
    if not url or conn is None or not has_customer_merge_sync_table(conn):
        return {
            "events_sent": 0,
            "backfilled_applied": 0,
            "backfilled_undone": 0,
            "error": None,
        }

    backfill_counts = backfill_customer_merge_sync_events(conn)
    events = _select_unsent_events(conn)
    if not events:
        return {
            "events_sent": 0,
            "backfilled_applied": backfill_counts["applied"],
            "backfilled_undone": backfill_counts["undone"],
            "error": None,
        }

    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    payload: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "events": events}
    if uploaded_by:
        payload["uploaded_by"] = uploaded_by

    event_ids = [event["remote_event_id"] for event in events]

    try:
        import requests

        response = requests.post(url, json=payload, headers=headers, timeout=60)
        if response.status_code >= 400:
            error = f"HTTP {response.status_code}"
            _mark_events(conn, event_ids, uploaded_at=None, error=error)
            return {
                "events_sent": 0,
                "backfilled_applied": backfill_counts["applied"],
                "backfilled_undone": backfill_counts["undone"],
                "error": error,
            }
    except Exception as exc:
        error = str(exc)
        _mark_events(conn, event_ids, uploaded_at=None, error=error)
        return {
            "events_sent": 0,
            "backfilled_applied": backfill_counts["applied"],
            "backfilled_undone": backfill_counts["undone"],
            "error": error,
        }

    uploaded_at = datetime.now(timezone.utc).isoformat()
    _mark_events(conn, event_ids, uploaded_at=uploaded_at, error=None)
    return {
        "events_sent": len(events),
        "backfilled_applied": backfill_counts["applied"],
        "backfilled_undone": backfill_counts["undone"],
        "error": None,
    }
