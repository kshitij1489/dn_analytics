"""
Phase 6 rollout: drain legacy menu outboxes before enabling server strict mode.

Uploads all unsent menu-merge and mapping-verification events via the existing
batched shippers. Strict-mode clients stop writing to these outboxes; this module
is for clearing pre-rollout queues on installs that still use legacy push.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.menu_mapping_verification_shipper import upload_pending as upload_verification_events
from src.core.menu_mapping_verification_sync_events import has_menu_mapping_verification_sync_table
from src.core.menu_merge_shipper import upload_pending as upload_merge_events
from src.core.menu_merge_sync_events import has_menu_merge_sync_table
from src.core.menu_mutation_commit import strict_mode_active, strict_mode_ready
from src.core.sync_identity import get_menu_state_revision, get_menu_strict_mode_enabled

MAX_DRAIN_BATCHES = 100


def _count_unsent_merge_events(conn) -> int:
    if not has_menu_merge_sync_table(conn):
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM menu_merge_sync_events WHERE uploaded_at IS NULL"
    ).fetchone()
    return int(row[0] if row else 0)


def _count_unsent_verification_events(conn) -> int:
    if not has_menu_mapping_verification_sync_table(conn):
        return 0
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM menu_mapping_verification_sync_events
        WHERE uploaded_at IS NULL
        """
    ).fetchone()
    return int(row[0] if row else 0)


def _count_pending_local_rows(conn) -> int:
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM menu_item_variants WHERE pending_local = 1"
        ).fetchone()
        return int(row[0] if row else 0)
    except Exception:
        return 0


def get_menu_outbox_status(conn) -> Dict[str, Any]:
    """Return rollout-relevant outbox and strict-mode state for this install."""
    merge_unsent = _count_unsent_merge_events(conn)
    verification_unsent = _count_unsent_verification_events(conn)
    pending_local = _count_pending_local_rows(conn)
    return {
        "menu_merge_unsent": merge_unsent,
        "menu_mapping_verification_unsent": verification_unsent,
        "pending_local_rows": pending_local,
        "outbox_drained": merge_unsent == 0 and verification_unsent == 0,
        "menu_state_revision": get_menu_state_revision(conn),
        "menu_strict_mode_enabled": get_menu_strict_mode_enabled(conn),
        "strict_mode_ready": strict_mode_ready(conn),
        "strict_mode_active": strict_mode_active(conn),
    }


def _shipper_kwargs(conn) -> Dict[str, Any]:
    base_url, auth = get_cloud_sync_config(conn)
    kwargs: Dict[str, Any] = {}
    if not base_url:
        return kwargs
    base = base_url.rstrip("/")
    kwargs["endpoint_merge"] = f"{base}/desktop-analytics-sync/menu-merges/ingest"
    kwargs["endpoint_verification"] = (
        f"{base}/desktop-analytics-sync/menu-mapping-verifications/ingest"
    )
    if auth:
        kwargs["auth"] = auth
    from src.core.client_learning_shipper import get_uploaded_by, get_uploaded_from

    kwargs["uploaded_by"] = get_uploaded_by(conn)
    kwargs["uploaded_from"] = get_uploaded_from(conn)
    return kwargs


def drain_menu_outbox(
    conn,
    *,
    max_batches: int = MAX_DRAIN_BATCHES,
) -> Dict[str, Any]:
    """
    Upload all unsent legacy menu events. Stops on first shipper error or when
    both outboxes are empty.
    """
    kwargs = _shipper_kwargs(conn)
    if not kwargs.get("endpoint_merge"):
        return {
            "status": "error",
            "message": "Cloud sync URL is not configured.",
            "batches": 0,
            "menu_merges_sent": 0,
            "menu_mapping_verifications_sent": 0,
            "outbox_drained": False,
        }

    total_merge_sent = 0
    total_verification_sent = 0
    batches = 0

    for _ in range(max_batches):
        merge_unsent = _count_unsent_merge_events(conn)
        verification_unsent = _count_unsent_verification_events(conn)
        if merge_unsent == 0 and verification_unsent == 0:
            status = get_menu_outbox_status(conn)
            return {
                "status": "ok",
                "message": "Menu outbox drained.",
                "batches": batches,
                "menu_merges_sent": total_merge_sent,
                "menu_mapping_verifications_sent": total_verification_sent,
                "outbox_drained": True,
                **status,
            }

        batches += 1
        merge_result = upload_merge_events(
            conn,
            endpoint=kwargs["endpoint_merge"],
            auth=kwargs.get("auth"),
            uploaded_by=kwargs.get("uploaded_by"),
            uploaded_from=kwargs.get("uploaded_from"),
        )
        if merge_result.get("error"):
            status = get_menu_outbox_status(conn)
            return {
                "status": "error",
                "message": f"Menu merge upload failed: {merge_result['error']}",
                "batches": batches,
                "menu_merges_sent": total_merge_sent,
                "menu_mapping_verifications_sent": total_verification_sent,
                "last_merge_result": merge_result,
                "outbox_drained": False,
                **status,
            }
        total_merge_sent += int(merge_result.get("events_sent") or 0)

        verification_result = upload_verification_events(
            conn,
            endpoint=kwargs["endpoint_verification"],
            auth=kwargs.get("auth"),
            uploaded_by=kwargs.get("uploaded_by"),
            uploaded_from=kwargs.get("uploaded_from"),
        )
        if verification_result.get("error"):
            status = get_menu_outbox_status(conn)
            return {
                "status": "error",
                "message": (
                    f"Mapping verification upload failed: {verification_result['error']}"
                ),
                "batches": batches,
                "menu_merges_sent": total_merge_sent,
                "menu_mapping_verifications_sent": total_verification_sent,
                "last_verification_result": verification_result,
                "outbox_drained": False,
                **status,
            }
        total_verification_sent += int(verification_result.get("events_sent") or 0)

        if int(merge_result.get("events_sent") or 0) == 0 and int(
            verification_result.get("events_sent") or 0
        ) == 0:
            status = get_menu_outbox_status(conn)
            if status["outbox_drained"]:
                return {
                    "status": "ok",
                    "message": "Menu outbox drained.",
                    "batches": batches,
                    "menu_merges_sent": total_merge_sent,
                    "menu_mapping_verifications_sent": total_verification_sent,
                    "outbox_drained": True,
                    **status,
                }
            return {
                "status": "error",
                "message": (
                    "Outbox still has unsent events but shippers made no progress "
                    "(check quarantine / server rejections)."
                ),
                "batches": batches,
                "menu_merges_sent": total_merge_sent,
                "menu_mapping_verifications_sent": total_verification_sent,
                "outbox_drained": False,
                **status,
            }

    status = get_menu_outbox_status(conn)
    return {
        "status": "error",
        "message": f"Drain stopped after {max_batches} batches with events still pending.",
        "batches": batches,
        "menu_merges_sent": total_merge_sent,
        "menu_mapping_verifications_sent": total_verification_sent,
        "outbox_drained": status["outbox_drained"],
        **status,
    }
