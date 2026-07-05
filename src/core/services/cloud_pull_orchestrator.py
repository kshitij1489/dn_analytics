"""
Best-effort Dachnona cloud pulls (customer merges, menu bootstrap, menu mapping verifications, menu merges).

Pull order (keep in sync with MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md):
1. Menu bootstrap (broad catalog / id_maps + cluster_state; seed-only by default)
2. Menu assignments snapshot (one-time fresh-install seed, plan Phase C4 —
   after the catalog exists, before event tails)
3. Menu mapping verification events (per-line is_verified on menu_item_variants)
4. Menu merge events (structural merges / resolution history)
5. Customer merges

Used after POS sync when cloud endpoints are configured; failures are logged, not raised.
"""

import logging
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

MERGE_EVENTS_LIMIT = 100

# One pull at a time per install (plan C5.1): the 5-minute scheduler and the
# button-triggered Sync DB job share this lock so their pulls never interleave.
CLOUD_PULL_LOCK = threading.Lock()


def run_best_effort_cloud_pulls(
    conn,
    *,
    merge_events_limit: int = MERGE_EVENTS_LIMIT,
    blocking: bool = True,
) -> Dict[str, Any]:
    """
    Run cloud pull steps when their pull URLs are configured.
    Returns a summary dict; sets attempted=True if any pull was invoked.

    blocking=False (the scheduler) skips the cycle when another pull holds the
    lock instead of queueing behind it.
    """
    if not CLOUD_PULL_LOCK.acquire(blocking=blocking):
        return {
            "attempted": False,
            "skipped": True,
            "reason": "another cloud pull is in progress",
        }
    try:
        return _run_best_effort_cloud_pulls_locked(
            conn, merge_events_limit=merge_events_limit
        )
    finally:
        CLOUD_PULL_LOCK.release()


def _run_best_effort_cloud_pulls_locked(
    conn,
    *,
    merge_events_limit: int = MERGE_EVENTS_LIMIT,
) -> Dict[str, Any]:
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.customer_merge_sync import (
        get_customer_merge_pull_endpoint,
        pull_and_apply_customer_merge_events,
    )
    from src.core.menu_assignment_bootstrap import (
        bootstrap_menu_assignments_if_needed,
        get_menu_assignments_snapshot_endpoint,
    )
    from src.core.menu_bootstrap_sync import (
        fetch_and_apply_menu_bootstrap_snapshot,
        get_menu_bootstrap_apply_mode,
        get_menu_bootstrap_pull_endpoint,
    )
    from src.core.menu_mapping_verification_sync import (
        get_menu_mapping_verification_pull_endpoint,
        pull_and_apply_menu_mapping_verification_events,
    )
    from src.core.menu_merge_sync import (
        get_menu_merge_pull_endpoint,
        pull_and_apply_menu_merge_events,
    )

    summary: Dict[str, Any] = {
        "attempted": False,
        "customer_merges": None,
        "menu_assignments_bootstrap": None,
        "menu_bootstrap": None,
        "menu_mapping_verifications": None,
        "menu_merges": None,
    }

    _, auth_key = get_cloud_sync_config(conn)

    ep_boot = get_menu_bootstrap_pull_endpoint(conn)
    if ep_boot:
        summary["attempted"] = True
        try:
            summary["menu_bootstrap"] = fetch_and_apply_menu_bootstrap_snapshot(
                conn,
                ep_boot,
                auth=auth_key,
                apply_mode=get_menu_bootstrap_apply_mode(conn),
            )
        except Exception as e:
            logger.exception("Best-effort menu bootstrap pull failed")
            summary["menu_bootstrap"] = {"error": str(e)}

    ep_assignments = get_menu_assignments_snapshot_endpoint(conn)
    if ep_assignments:
        summary["attempted"] = True
        try:
            summary["menu_assignments_bootstrap"] = bootstrap_menu_assignments_if_needed(
                conn,
                ep_assignments,
                auth=auth_key,
            )
        except Exception as e:
            logger.exception("Best-effort menu assignments bootstrap failed")
            summary["menu_assignments_bootstrap"] = {"error": str(e)}

    ep_mapping = get_menu_mapping_verification_pull_endpoint(conn)
    if ep_mapping:
        summary["attempted"] = True
        try:
            summary["menu_mapping_verifications"] = pull_and_apply_menu_mapping_verification_events(
                conn,
                ep_mapping,
                auth=auth_key,
                limit=merge_events_limit,
            )
        except Exception as e:
            logger.exception("Best-effort menu mapping verification pull failed")
            summary["menu_mapping_verifications"] = {"error": str(e)}

    ep_menu_merge = get_menu_merge_pull_endpoint(conn)
    if ep_menu_merge:
        summary["attempted"] = True
        try:
            summary["menu_merges"] = pull_and_apply_menu_merge_events(
                conn,
                ep_menu_merge,
                auth=auth_key,
                limit=merge_events_limit,
            )
        except Exception as e:
            logger.exception("Best-effort menu merge pull failed")
            summary["menu_merges"] = {"error": str(e)}

    ep_cust = get_customer_merge_pull_endpoint(conn)
    if ep_cust:
        summary["attempted"] = True
        try:
            summary["customer_merges"] = pull_and_apply_customer_merge_events(
                conn,
                ep_cust,
                auth=auth_key,
                limit=merge_events_limit,
            )
        except Exception as e:
            logger.exception("Best-effort customer merge pull failed")
            summary["customer_merges"] = {"error": str(e)}

    if not summary["attempted"]:
        summary["skipped"] = True
        summary["reason"] = "no cloud pull endpoints configured"
        return summary

    for key in (
        "menu_assignments_bootstrap",
        "menu_bootstrap",
        "menu_mapping_verifications",
        "menu_merges",
        "customer_merges",
    ):
        block = summary.get(key)
        if isinstance(block, dict) and block.get("error"):
            logger.warning("Cloud pull %s reported error: %s", key, block["error"])

    return summary
