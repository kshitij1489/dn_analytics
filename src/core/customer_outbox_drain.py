"""
Phase 6 rollout: drain legacy customer merge outboxes before enabling server strict mode.

Uploads all unsent customer merge events via the existing batched shipper. Strict-mode
clients stop writing to this outbox; this module clears pre-rollout queues on installs
that still use legacy push.
"""

from __future__ import annotations

from typing import Any, Dict

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.customer_merge_shipper import upload_pending as upload_customer_merge_events
from src.core.customer_merge_sync_events import has_customer_merge_sync_table
from src.core.customer_mutation_commit import strict_mode_active, strict_mode_ready
from src.core.sync_identity import get_customer_state_revision, get_customer_strict_mode_enabled

MAX_DRAIN_BATCHES = 100


def _count_unsent_customer_events(conn) -> int:
    if not has_customer_merge_sync_table(conn):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM customer_merge_sync_events WHERE uploaded_at IS NULL"
    ).fetchone()
    return int(row[0] if row else 0)


def get_customer_outbox_status(conn) -> Dict[str, Any]:
    """Return rollout-relevant outbox and strict-mode state for this install."""
    from src.core.customer_merge_sync import count_unresolved_customer_merge_events

    unsent = _count_unsent_customer_events(conn)
    return {
        "customer_merge_unsent": unsent,
        "outbox_drained": unsent == 0,
        "customer_merge_unresolved": count_unresolved_customer_merge_events(conn),
        "customer_state_revision": get_customer_state_revision(conn),
        "customer_strict_mode_enabled": get_customer_strict_mode_enabled(conn),
        "strict_mode_ready": strict_mode_ready(conn),
        "strict_mode_active": strict_mode_active(conn),
    }


def _shipper_kwargs(conn) -> Dict[str, Any]:
    base_url, auth = get_cloud_sync_config(conn)
    kwargs: Dict[str, Any] = {}
    if not base_url:
        return kwargs
    base = base_url.rstrip("/")
    kwargs["endpoint"] = f"{base}/desktop-analytics-sync/customer-merges/ingest"
    if auth:
        kwargs["auth"] = auth
    from src.core.client_learning_shipper import get_uploaded_by, get_uploaded_from

    kwargs["uploaded_by"] = get_uploaded_by(conn)
    kwargs["uploaded_from"] = get_uploaded_from(conn)
    return kwargs


def drain_customer_outbox(
    conn,
    *,
    max_batches: int = MAX_DRAIN_BATCHES,
) -> Dict[str, Any]:
    """
    Upload all unsent legacy customer merge events. Stops on first shipper error or when
    the outbox is empty.
    """
    kwargs = _shipper_kwargs(conn)
    if not kwargs.get("endpoint"):
        return {
            "status": "error",
            "message": "Cloud sync URL is not configured.",
            "batches": 0,
            "customer_merges_sent": 0,
            "outbox_drained": False,
        }

    total_sent = 0
    batches = 0

    for _ in range(max_batches):
        unsent = _count_unsent_customer_events(conn)
        if unsent == 0:
            status = get_customer_outbox_status(conn)
            return {
                "status": "ok",
                "message": "Customer outbox drained.",
                "batches": batches,
                "customer_merges_sent": total_sent,
                "outbox_drained": True,
                **status,
            }

        batches += 1
        result = upload_customer_merge_events(conn, **kwargs)
        if result.get("error"):
            status = get_customer_outbox_status(conn)
            return {
                "status": "error",
                "message": f"Customer merge upload failed: {result['error']}",
                "batches": batches,
                "customer_merges_sent": total_sent,
                "last_upload_result": result,
                "outbox_drained": False,
                **status,
            }
        total_sent += int(result.get("events_sent") or 0)

        if int(result.get("events_sent") or 0) == 0:
            status = get_customer_outbox_status(conn)
            if status["outbox_drained"]:
                return {
                    "status": "ok",
                    "message": "Customer outbox drained.",
                    "batches": batches,
                    "customer_merges_sent": total_sent,
                    "outbox_drained": True,
                    **status,
                }
            return {
                "status": "error",
                "message": (
                    "Outbox still has unsent events but shipper made no progress "
                    "(check quarantine / server rejections)."
                ),
                "batches": batches,
                "customer_merges_sent": total_sent,
                "outbox_drained": False,
                **status,
            }

    status = get_customer_outbox_status(conn)
    return {
        "status": "error",
        "message": f"Drain stopped after {max_batches} batches with events still pending.",
        "batches": batches,
        "customer_merges_sent": total_sent,
        "outbox_drained": status["outbox_drained"],
        **status,
    }
