"""
Best-effort Dachnona cloud pulls (customer merges, menu bootstrap, menu merges).
Used after POS sync when cloud endpoints are configured; failures are logged, not raised.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

MERGE_EVENTS_LIMIT = 100


def run_best_effort_cloud_pulls(
    conn,
    *,
    merge_events_limit: int = MERGE_EVENTS_LIMIT,
) -> Dict[str, Any]:
    """
    Run cloud pull steps when their pull URLs are configured.
    Returns a summary dict; sets attempted=True if any pull was invoked.
    """
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.customer_merge_sync import (
        get_customer_merge_pull_endpoint,
        pull_and_apply_customer_merge_events,
    )
    from src.core.menu_bootstrap_sync import (
        DEFAULT_MENU_BOOTSTRAP_APPLY_MODE,
        fetch_and_apply_menu_bootstrap_snapshot,
        get_menu_bootstrap_pull_endpoint,
    )
    from src.core.menu_merge_sync import (
        get_menu_merge_pull_endpoint,
        pull_and_apply_menu_merge_events,
    )

    summary: Dict[str, Any] = {
        "attempted": False,
        "customer_merges": None,
        "menu_bootstrap": None,
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
                apply_mode=DEFAULT_MENU_BOOTSTRAP_APPLY_MODE,
            )
        except Exception as e:
            logger.exception("Best-effort menu bootstrap pull failed")
            summary["menu_bootstrap"] = {"error": str(e)}

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

    for key in ("menu_bootstrap", "menu_merges", "customer_merges"):
        block = summary.get(key)
        if isinstance(block, dict) and block.get("error"):
            logger.warning("Cloud pull %s reported error: %s", key, block["error"])

    return summary
