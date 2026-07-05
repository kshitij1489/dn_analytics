"""
Shared handling of per-event ingest results from cloud push endpoints.

Phase S1 servers respond to merge-event ingest with
{"accepted": [remote_event_id, ...], "rejected": [{"remote_event_id", "error"}, ...]}
so one malformed event no longer 400s the whole batch. Shippers route rejected
events out of the push queue and into the quarantine table (plan: Phase C2).
Old servers omit both keys; callers detect that (None return) and keep the
legacy all-or-nothing behavior.
"""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.core.menu_sync_quarantine import quarantine_event


def apply_per_event_ingest_results(
    conn,
    events: List[Dict[str, Any]],
    response_data: Any,
    mark_events: Callable[..., None],
    quarantine_stream: str,
) -> Optional[Dict[str, int]]:
    """
    Mark pushed events per the server's accepted/rejected verdicts.

    Accepted events are marked uploaded. Rejected events are marked uploaded
    with last_error set (so they stop blocking the queue) and copied into the
    quarantine table for surfacing. Events the server mentioned in neither
    list are left unsent and retry on the next push cycle.

    Returns {"accepted": n, "rejected": n}, or None when the response has no
    per-event results (old server) so the caller keeps its legacy behavior.
    """
    if not isinstance(response_data, dict):
        return None
    if "accepted" not in response_data and "rejected" not in response_data:
        return None

    events_by_id = {
        str(event.get("remote_event_id") or ""): event for event in events
    }
    uploaded_at = datetime.now(timezone.utc).isoformat()

    raw_accepted = response_data.get("accepted") or []
    accepted_ids = [
        str(event_id)
        for event_id in raw_accepted
        if str(event_id) in events_by_id
    ] if isinstance(raw_accepted, list) else []
    if accepted_ids:
        mark_events(conn, accepted_ids, uploaded_at=uploaded_at, error=None)

    rejected_count = 0
    raw_rejected = response_data.get("rejected") or []
    if isinstance(raw_rejected, list):
        for entry in raw_rejected:
            if not isinstance(entry, dict):
                continue
            event_id = str(entry.get("remote_event_id") or "").strip()
            if not event_id or event_id not in events_by_id:
                continue
            error = str(entry.get("error") or "Rejected by server")
            mark_events(conn, [event_id], uploaded_at=uploaded_at, error=error)
            quarantine_event(
                conn,
                quarantine_stream,
                event_id,
                events_by_id[event_id],
                error,
            )
            conn.commit()
            rejected_count += 1

    return {"accepted": len(accepted_ids), "rejected": rejected_count}
