"""All Stores Sync DB coordinator (plan §8.2).

Runs the ordinary single-profile sync once per authorized restaurant, in a
frozen order, each with its own database connection and its own central request
context. There is no "all" branch inside the low-level pull functions and no
mixed database: this is a loop over complete single-store syncs.

A store-local failure does not stop the remaining stores; a process-wide
credential or configuration failure does.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Sequence

from src.core.db.connection import get_profile_connection
from src.core.profiles import RestaurantProfile
from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK
from src.core.services.sync_service import SyncStatus

logger = logging.getLogger(__name__)

# Failures that are not about one restaurant: retrying the next store would
# repeat the same rejection and hide the real cause.
GLOBAL_FAILURE_CODES = {
    "invalid_api_key",
    "invalid_token",
    "invalid_credentials",
    "restaurant_list_not_configured",
    "restaurant_selector_not_a_parameter",
    "retired_parameter",
    "retired_field",
}


def _failure_code(error: BaseException) -> str:
    return str(getattr(error, "code", "") or "")


def _is_global_failure(error: BaseException) -> bool:
    return _failure_code(error) in GLOBAL_FAILURE_CODES


def _terminal_failure_code(status: SyncStatus) -> str:
    """Keep the code carried by a yielded terminal error.

    The ordinary sync API reports failures by yielding ``SyncStatus("error")``;
    most errors do not escape the generator as exceptions. Older statuses may
    put the code in stats or only prefix the message, so retain those narrowly
    scoped compatibility fallbacks while the typed ``code`` field rolls out.
    """
    code = str(getattr(status, "code", "") or "")
    if code:
        return code
    stats = status.stats if isinstance(status.stats, dict) else {}
    code = str(stats.get("failure_code") or stats.get("code") or "")
    if code:
        return code
    message = str(status.message or "")
    for candidate in GLOBAL_FAILURE_CODES:
        if message == candidate or message.startswith(f"{candidate}:"):
            return candidate
        if f" {candidate}:" in message:
            return candidate
    return "store_sync_failed"


def _store_progress(index: int, total: int, profile: RestaurantProfile, phase: str) -> str:
    return f"Store {index} of {total} — {profile.display_name} — {phase}"


def iter_all_stores_sync(
    profiles: Sequence[RestaurantProfile],
    *,
    iter_single_store=None,
) -> Iterator[SyncStatus]:
    """Yield progress for a sequential, profile-isolated sync of every store.

    `profiles` is the snapshot frozen when Sync DB was pressed. A later
    selection or allow-list change cannot alter this iteration set.
    """
    if iter_single_store is None:
        from src.api.routers.operations import iter_sync_statuses

        iter_single_store = iter_sync_statuses

    total = len(profiles)
    if total == 0:
        yield SyncStatus("error", "No authorized restaurant has an initialized database")
        return

    store_results: List[Dict[str, Any]] = []
    aborted_reason: Dict[str, Any] | None = None

    # First release: the whole All Stores job is serialized with the existing
    # process-wide cloud-pull lock, so a scheduler cycle cannot interleave
    # between two stores. The lock owner is named in the logs.
    logger.info("All Stores sync acquiring cloud pull lock for %d store(s)", total)
    with CLOUD_PULL_LOCK:
        for index, profile in enumerate(profiles, start=1):
            if aborted_reason is not None:
                store_results.append(
                    {
                        "restaurant_id": profile.restaurant_id,
                        "restaurant_name": profile.display_name,
                        "status": "not_attempted",
                        "error": aborted_reason["error"],
                        "code": aborted_reason["code"],
                    }
                )
                continue

            yield SyncStatus("info", _store_progress(index, total, profile, "starting"))
            conn = None
            try:
                conn, _ = get_profile_connection(profile)
                terminal = None
                for status in iter_single_store(conn, already_locked=True):
                    if status.type in ("done", "error"):
                        terminal = status
                        continue
                    yield SyncStatus(
                        "progress" if status.type == "progress" else "info",
                        _store_progress(
                            index, total, profile, status.message or status.type
                        ),
                        progress=(index - 1 + (status.progress or 0.0)) / total,
                        current=index,
                        total=total,
                    )
                if terminal is None:
                    raise RuntimeError("Sync did not produce a terminal status")
                if terminal.type == "error":
                    code = _terminal_failure_code(terminal)
                    error = terminal.message or "Sync failed"
                    store_results.append(
                        {
                            "restaurant_id": profile.restaurant_id,
                            "restaurant_name": profile.display_name,
                            "status": "failed",
                            "error": error,
                            "code": code,
                            "stats": terminal.stats or {},
                        }
                    )
                    if code in GLOBAL_FAILURE_CODES:
                        aborted_reason = {"error": error, "code": code}
                        yield SyncStatus(
                            "info",
                            f"Stopping All Stores sync: {error}",
                        )
                    else:
                        yield SyncStatus(
                            "info",
                            _store_progress(index, total, profile, f"failed: {error}"),
                            progress=index / total,
                            current=index,
                            total=total,
                        )
                    continue
                store_results.append(
                    {
                        "restaurant_id": profile.restaurant_id,
                        "restaurant_name": profile.display_name,
                        "status": "completed",
                        "message": terminal.message,
                        "stats": terminal.stats or {},
                    }
                )
                yield SyncStatus(
                    "info",
                    _store_progress(index, total, profile, terminal.message or "complete"),
                    progress=index / total,
                    current=index,
                    total=total,
                )
            except Exception as exc:  # noqa: BLE001 - recorded per store
                logger.warning("All Stores sync failed for %s: %s", profile.restaurant_id, exc)
                code = _failure_code(exc) or "store_sync_failed"
                store_results.append(
                    {
                        "restaurant_id": profile.restaurant_id,
                        "restaurant_name": profile.display_name,
                        "status": "failed",
                        "error": str(exc),
                        "code": code,
                    }
                )
                if _is_global_failure(exc):
                    aborted_reason = {"error": str(exc), "code": code}
                    yield SyncStatus(
                        "info",
                        f"Stopping All Stores sync: {exc}",
                    )
                else:
                    yield SyncStatus(
                        "info",
                        _store_progress(index, total, profile, f"failed: {exc}"),
                        progress=index / total,
                        current=index,
                        total=total,
                    )
            finally:
                if conn is not None:
                    conn.close()

    completed = [result for result in store_results if result["status"] == "completed"]
    failed = [result for result in store_results if result["status"] != "completed"]
    if completed and not failed:
        outcome = "completed"
    elif completed:
        outcome = "partial"
    else:
        outcome = "failed"

    stats = {
        "scope": "all",
        "outcome": outcome,
        "stores_requested": total,
        "stores_completed": len(completed),
        "stores": store_results,
        # Order totals across the whole loop, so the existing Sync DB summary
        # keeps working without per-store guessing.
        "orders": sum(int((result.get("stats") or {}).get("orders") or 0) for result in completed),
        "fetched": sum(int((result.get("stats") or {}).get("fetched") or 0) for result in completed),
    }
    if aborted_reason:
        stats["aborted"] = aborted_reason

    message = {
        "completed": f"All Stores sync complete · {len(completed)} of {total} stores",
        "partial": (
            f"All Stores sync partial · {len(completed)} of {total} stores completed · "
            + "; ".join(
                f"{result['restaurant_name']}: {result.get('error') or result['status']}"
                for result in failed
            )
        ),
        "failed": (
            "All Stores sync failed · "
            + "; ".join(
                f"{result['restaurant_name']}: {result.get('error') or result['status']}"
                for result in failed
            )
        ),
    }[outcome]

    yield SyncStatus(
        "done" if outcome != "failed" else "error",
        message,
        progress=1.0,
        current=total,
        total=total,
        stats=stats,
    )
