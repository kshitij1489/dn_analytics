"""
Best-effort Dachnona cloud pulls (customer merges, menu bootstrap, menu mapping verifications, menu merges).

Pull order (keep in sync with docs/MENU_SYNC_ARCHITECTURE.md §4):
1. Menu bootstrap (broad catalog / id_maps + cluster_state; seed-only by default)
2. Menu assignments snapshot (one-time fresh-install seed, plan Phase C4 —
   after the catalog exists, before event tails)
3. Menu merge + mapping-verification event streams, drained to one stable revision
5. Customer merges

Used after POS sync when cloud endpoints are configured. Menu and customer ground-truth
pull failures are surfaced to Sync DB callers.
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MERGE_EVENTS_LIMIT = 100

MENU_GROUND_TRUTH_PULL_KEYS = (
    "menu_bootstrap",
    "menu_assignments_bootstrap",
    "menu_mapping_verifications",
    "menu_merges",
)

CUSTOMER_GROUND_TRUTH_PULL_KEYS = ("customer_merges",)

# One pull at a time per install (plan C5.1): the 5-minute scheduler and the
# button-triggered Sync DB job share this lock so their pulls never interleave.
CLOUD_PULL_LOCK = threading.Lock()


def _block_error_message(block: Any) -> Optional[str]:
    if not isinstance(block, dict):
        return None
    error = block.get("error")
    if isinstance(error, str) and error:
        return error
    if block.get("status") == "error" and isinstance(block.get("error"), str):
        return block["error"]
    return None


def collect_menu_pull_errors(summary: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return (stream_key, error_message) for each failed menu ground-truth pull."""
    failures: List[Tuple[str, str]] = []
    for key in MENU_GROUND_TRUTH_PULL_KEYS:
        message = _block_error_message(summary.get(key))
        if message:
            failures.append((key, message))
    return failures


def menu_pull_had_errors(summary: Dict[str, Any]) -> bool:
    return bool(collect_menu_pull_errors(summary))


def collect_customer_pull_errors(summary: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Return (stream_key, error_message) for each failed customer ground-truth pull."""
    failures: List[Tuple[str, str]] = []
    for key in CUSTOMER_GROUND_TRUTH_PULL_KEYS:
        message = _block_error_message(summary.get(key))
        if message:
            failures.append((key, message))
    return failures


def customer_pull_had_errors(summary: Dict[str, Any]) -> bool:
    return bool(collect_customer_pull_errors(summary))


def collect_customer_pull_warnings(summary: Dict[str, Any]) -> List[Tuple[str, str]]:
    """
    Return (stream_key, warning_message) for non-fatal customer pull conditions —
    currently quarantined unresolved merge events, which are retried on every pull
    and must not fail Sync DB.
    """
    warnings: List[Tuple[str, str]] = []
    for key in CUSTOMER_GROUND_TRUTH_PULL_KEYS:
        block = summary.get(key)
        if not isinstance(block, dict):
            continue
        pending = block.get("unresolved_pending")
        if isinstance(pending, int) and pending > 0:
            warnings.append(
                (
                    key,
                    f"{pending} customer merge event(s) could not be matched to local "
                    "customers and were quarantined; they will be retried on future syncs.",
                )
            )
    return warnings


def run_best_effort_cloud_pulls(
    conn,
    *,
    merge_events_limit: int = MERGE_EVENTS_LIMIT,
    blocking: bool = True,
    skip_menu_bootstrap: bool = False,
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
            conn, merge_events_limit=merge_events_limit, skip_menu_bootstrap=skip_menu_bootstrap
        )
    finally:
        CLOUD_PULL_LOCK.release()


def _run_best_effort_cloud_pulls_locked(
    conn,
    *,
    merge_events_limit: int = MERGE_EVENTS_LIMIT,
    skip_menu_bootstrap: bool = False,
) -> Dict[str, Any]:
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.customer_merge_sync import get_customer_merge_pull_endpoint
    from src.core.customer_mutation_commit import pull_latest_customer_state
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
    )
    from src.core.menu_merge_sync import get_menu_merge_pull_endpoint
    from src.core.menu_mutation_commit import pull_latest_menu_state

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
    if ep_boot and not skip_menu_bootstrap:
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
    ep_menu_merge = get_menu_merge_pull_endpoint(conn)
    if ep_mapping or ep_menu_merge:
        summary["attempted"] = True
        try:
            if not ep_mapping or not ep_menu_merge:
                raise RuntimeError(
                    "Both menu merge and mapping-verification pull endpoints "
                    "must be configured."
                )
            menu_pull = pull_latest_menu_state(conn, already_locked=True)
            summary["menu_mapping_verifications"] = menu_pull[
                "menu_mapping_verifications"
            ]
            summary["menu_merges"] = menu_pull["menu_merges"]
        except Exception as e:
            logger.exception("Menu ground-truth pull failed")
            summary["menu_mapping_verifications"] = {"error": str(e)}
            summary["menu_merges"] = {"error": str(e)}

    ep_cust = get_customer_merge_pull_endpoint(conn)
    if ep_cust:
        summary["attempted"] = True
        try:
            summary["customer_merges"] = pull_latest_customer_state(
                conn,
                already_locked=True,
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

    menu_errors = collect_menu_pull_errors(summary)
    if menu_errors:
        summary["menu_pull_errors"] = [{"stream": key, "error": message} for key, message in menu_errors]
        summary["menu_pull_failed"] = True

    customer_errors = collect_customer_pull_errors(summary)
    if customer_errors:
        summary["customer_pull_errors"] = [
            {"stream": key, "error": message} for key, message in customer_errors
        ]
        summary["customer_pull_failed"] = True

    customer_warnings = collect_customer_pull_warnings(summary)
    if customer_warnings:
        summary["customer_pull_warnings"] = [
            {"stream": key, "warning": message} for key, message in customer_warnings
        ]
        for key, message in customer_warnings:
            logger.warning("Cloud pull %s reported warning: %s", key, message)

    return summary
