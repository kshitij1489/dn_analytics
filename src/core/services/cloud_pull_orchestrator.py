"""
Best-effort Dachnona cloud pulls (customer merges, menu bootstrap, menu mapping verifications, menu merges).

Post-order pull order (keep in sync with the revision-1.7 rebuild runbook):
1. In global mode, assignment snapshot, unified audit history, then the settled
   §9 shared-POS observation. Catalog snapshot/event state is normally skipped
   here because Sync DB drains it before POS order replay. Audit-history and
   non-rebuild observation failures are warning-only.
2. Menu bootstrap (broad catalog / id_maps + cluster_state; seed-only by default)
3. Menu assignments snapshot (one-time fresh-install seed, plan Phase C4 —
   after the catalog exists, before event tails)
4. Menu merge + mapping-verification event streams, drained to one stable revision
5. Derived assignment flush (POS-backed machine assignments; best-effort)
6. Customer merges
7. Forecast deltas (central server-authored rows; best-effort, non-fatal on failure)

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
    for key in ("global_menu", "global_menu_assignments"):
        if key not in summary or summary.get(key) is None:
            continue
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


def collect_forecast_pull_warnings(summary: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Non-fatal forecast pull failures (OQ-3: must not fail Sync DB)."""
    warnings: List[Tuple[str, str]] = []
    block = summary.get("forecasts")
    if not isinstance(block, dict):
        return warnings
    message = _block_error_message(block)
    if message:
        warnings.append(("forecasts", message))
    elif block.get("status") == "error" and isinstance(block.get("error"), str):
        warnings.append(("forecasts", block["error"]))
    return warnings


def collect_global_menu_history_warnings(
    summary: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """History is a read-only audit view; its failure must not fail catalog sync."""
    block = summary.get("global_menu_history")
    message = _block_error_message(block)
    if not message:
        return []
    return [("global_menu_history", message)]


def collect_shared_pos_observation_warnings(
    summary: Dict[str, Any],
) -> List[Tuple[str, str]]:
    """The settled-state §9 observation is warning-only outside a clean rebuild."""
    block = summary.get("shared_pos_observation")
    message = _block_error_message(block)
    if not message:
        return []
    return [("shared_pos_observation", message)]


def run_best_effort_cloud_pulls(
    conn,
    *,
    merge_events_limit: int = MERGE_EVENTS_LIMIT,
    blocking: bool = True,
    skip_menu_bootstrap: bool = False,
    skip_global_menu_state: bool = False,
    send_shared_pos_observation: bool = False,
    already_locked: bool = False,
) -> Dict[str, Any]:
    """
    Run cloud pull steps when their pull URLs are configured.
    Returns a summary dict; sets attempted=True if any pull was invoked.

    blocking=False (the scheduler) skips the cycle when another pull holds the
    lock instead of queueing behind it.

    already_locked=True is for the All Stores coordinator, which holds the
    process-wide lock across its whole store loop so a scheduler cycle cannot
    interleave between two stores. The lock is not reentrant.
    """
    from src.core.central_api import restaurant_id_from_connection

    restaurant_id = restaurant_id_from_connection(conn)
    if already_locked:
        result = _run_best_effort_cloud_pulls_locked(
            conn,
            merge_events_limit=merge_events_limit,
            skip_menu_bootstrap=skip_menu_bootstrap,
            skip_global_menu_state=skip_global_menu_state,
            send_shared_pos_observation=send_shared_pos_observation,
        )
        result["restaurant_id"] = restaurant_id
        return result
    if not CLOUD_PULL_LOCK.acquire(blocking=blocking):
        return {
            "attempted": False,
            "skipped": True,
            "reason": f"another cloud pull is in progress (requested {restaurant_id})",
            "restaurant_id": restaurant_id,
        }
    try:
        result = _run_best_effort_cloud_pulls_locked(
            conn,
            merge_events_limit=merge_events_limit,
            skip_menu_bootstrap=skip_menu_bootstrap,
            skip_global_menu_state=skip_global_menu_state,
            send_shared_pos_observation=send_shared_pos_observation,
        )
        result["restaurant_id"] = restaurant_id
        return result
    finally:
        CLOUD_PULL_LOCK.release()


def _run_best_effort_cloud_pulls_locked(
    conn,
    *,
    merge_events_limit: int = MERGE_EVENTS_LIMIT,
    skip_menu_bootstrap: bool = False,
    skip_global_menu_state: bool = False,
    send_shared_pos_observation: bool = False,
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
        "derived_assignment_flush": None,
        "menu_assignments_bootstrap": None,
        "menu_bootstrap": None,
        "menu_mapping_verifications": None,
        "menu_merges": None,
        "global_menu": None,
        "global_menu_assignments": None,
        "global_menu_history": None,
        "shared_pos_observation": None,
        "forecasts": None,
    }

    _, auth_key = get_cloud_sync_config(conn)

    try:
        from src.core.global_menu_schema import resolve_global_menu_capability

        global_capability = resolve_global_menu_capability(
            conn, allow_profile_sync=True
        )
    except Exception:
        global_capability = None

    if global_capability is not None and global_capability.active:
        from src.core.global_menu_sync import (
            pull_global_assignment_snapshot,
            pull_global_menu_state,
        )

        summary["attempted"] = True
        if skip_global_menu_state:
            summary["global_menu"] = {
                "status": "already_current",
                "reason": "catalog snapshot and event tail drained before order replay",
            }
        else:
            try:
                summary["global_menu"] = pull_global_menu_state(
                    conn, auth=auth_key, allow_profile_sync=True
                )
            except Exception as e:
                logger.exception("Global menu pull failed")
                summary["global_menu"] = {"status": "error", "error": str(e)}
        if not _block_error_message(summary["global_menu"]):
            try:
                summary["global_menu_assignments"] = pull_global_assignment_snapshot(
                    conn, auth=auth_key, allow_profile_sync=True
                )
            except Exception as e:
                logger.exception("Global menu assignment pull failed")
                summary["global_menu_assignments"] = {
                    "status": "error",
                    "error": str(e),
                }
        try:
            from src.core.global_menu_history import pull_global_menu_history

            summary["global_menu_history"] = pull_global_menu_history(
                conn, auth=auth_key, allow_profile_sync=True
            )
        except Exception as e:
            logger.exception("Global menu history pull failed")
            summary["global_menu_history"] = {
                "status": "error",
                "error": str(e),
            }
        if (
            send_shared_pos_observation
            and global_capability.shared_pos_catalog_advertised
            and not _block_error_message(summary["global_menu"])
            and not _block_error_message(summary["global_menu_assignments"])
        ):
            try:
                from src.core.client_learning_shipper import run_scoped_uploads

                summary["shared_pos_observation"] = run_scoped_uploads(
                    conn, auth=auth_key
                )
            except Exception as e:
                logger.exception("Shared POS observation upload failed")
                summary["shared_pos_observation"] = {
                    "status": "error",
                    "error": str(e),
                }

    ep_boot = get_menu_bootstrap_pull_endpoint(conn)
    if not (global_capability is not None and global_capability.active) and ep_boot and not skip_menu_bootstrap:
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
    if not (global_capability is not None and global_capability.active) and ep_assignments:
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
    if not (global_capability is not None and global_capability.active) and (ep_mapping or ep_menu_merge):
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

    if (
        isinstance(summary.get("menu_mapping_verifications"), dict)
        and isinstance(summary.get("menu_merges"), dict)
        and not _block_error_message(summary["menu_mapping_verifications"])
        and not _block_error_message(summary["menu_merges"])
    ):
        try:
            from src.core.derived_assignment_flush import flush_pending_derived_assignments

            summary["derived_assignment_flush"] = flush_pending_derived_assignments(conn)
        except Exception as e:
            logger.exception("Derived assignment flush failed")
            summary["derived_assignment_flush"] = {"error": str(e)}

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

    from src.core.forecast_sync import get_forecast_delta_endpoint, pull_and_apply_forecast_deltas

    ep_forecast = get_forecast_delta_endpoint(conn)
    if ep_forecast:
        summary["attempted"] = True
        try:
            summary["forecasts"] = pull_and_apply_forecast_deltas(conn)
        except Exception as e:
            logger.exception("Best-effort forecast pull failed")
            summary["forecasts"] = {"status": "error", "error": str(e)}

    if not summary["attempted"]:
        summary["skipped"] = True
        summary["reason"] = "no cloud pull endpoints configured"
        return summary

    for key in (
        "global_menu",
        "global_menu_assignments",
        "global_menu_history",
        "shared_pos_observation",
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

    forecast_warnings = collect_forecast_pull_warnings(summary)
    if forecast_warnings:
        summary["forecast_pull_warnings"] = [
            {"stream": key, "warning": message} for key, message in forecast_warnings
        ]
        for key, message in forecast_warnings:
            logger.warning("Cloud pull %s reported warning: %s", key, message)

    history_warnings = collect_global_menu_history_warnings(summary)
    if history_warnings:
        summary["global_menu_history_warnings"] = [
            {"stream": key, "warning": message} for key, message in history_warnings
        ]
        for key, message in history_warnings:
            logger.warning("Cloud pull %s reported warning: %s", key, message)

    observation_warnings = collect_shared_pos_observation_warnings(summary)
    if observation_warnings:
        summary["shared_pos_observation_warnings"] = [
            {"stream": key, "warning": message}
            for key, message in observation_warnings
        ]
        for key, message in observation_warnings:
            logger.warning("Cloud pull %s reported warning: %s", key, message)

    return summary
