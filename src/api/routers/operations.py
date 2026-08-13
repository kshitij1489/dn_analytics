import queue
import threading
from typing import Any, Dict, Optional, Sequence, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from src.api.dependencies import (
    get_analytics_scope,
    get_authorized_restaurant_profile,
    get_db,
)
from src.core.analytics_scope import AnalyticsScope
from src.core.db.connection import get_profile_connection
from src.core.profiles import ALL_STORES_TOKEN
from src.core.services.sync_service import SyncStatus, sync_database
from src.core.services.cloud_pull_orchestrator import (
    collect_customer_pull_errors,
    collect_customer_pull_warnings,
    collect_forecast_pull_warnings,
    collect_global_menu_history_warnings,
    collect_menu_pull_errors,
    run_best_effort_cloud_pulls,
)
from src.core.client_learning_shipper import run_all as run_client_learning_shippers
from src.api.job_manager import JobManager
from src.api.models import JobResponse

router = APIRouter()


class SyncRunRequest(BaseModel):
    restaurant_id: str


class _SyncSlot:
    """One in-flight Sync DB job's claim on the restaurants it will write."""

    __slots__ = ("keys", "job_id", "released")

    def __init__(self, keys: Sequence[str]) -> None:
        self.keys = tuple(keys)
        self.job_id: Optional[str] = None
        self.released = False


_SYNC_SLOTS_LOCK = threading.Lock()
_ACTIVE_SYNC_SLOTS: Dict[str, _SyncSlot] = {}


def _slot_is_live(slot: _SyncSlot) -> bool:
    if slot.released:
        return False
    if slot.job_id is None:
        # Claimed, thread not started yet: the request that owns it is still
        # inside run_sync.
        return True
    job = JobManager.get_job(slot.job_id)
    # A job the manager never registered, or one that already finished or
    # failed, holds nothing — its slot is stale bookkeeping.
    return bool(job) and job.get("status") == "running"


def _claim_sync_slot(
    keys: Sequence[str],
) -> Tuple[Optional[_SyncSlot], Optional[Tuple[str, Optional[str]]]]:
    """Reserve every restaurant a job will write, or name the holder of the first clash."""
    with _SYNC_SLOTS_LOCK:
        for key in keys:
            held = _ACTIVE_SYNC_SLOTS.get(key)
            if held is None:
                continue
            if _slot_is_live(held):
                return None, (key, held.job_id)
            for stale_key in held.keys:
                if _ACTIVE_SYNC_SLOTS.get(stale_key) is held:
                    del _ACTIVE_SYNC_SLOTS[stale_key]
        slot = _SyncSlot(keys)
        for key in keys:
            _ACTIVE_SYNC_SLOTS[key] = slot
        return slot, None


def _release_sync_slot(slot: _SyncSlot) -> None:
    with _SYNC_SLOTS_LOCK:
        slot.released = True
        for key in slot.keys:
            if _ACTIVE_SYNC_SLOTS.get(key) is slot:
                del _ACTIVE_SYNC_SLOTS[key]


def _sync_already_running(conflict: Tuple[str, Optional[str]]) -> HTTPException:
    key, job_id = conflict
    scope = "All Stores" if key == ALL_STORES_TOKEN else key
    return HTTPException(
        status_code=409,
        detail={
            "error": f"Sync DB is already running for {scope}",
            "code": "sync_already_running",
            "job_id": job_id,
        },
    )


def _start_guarded_sync_job(factory, keys: Sequence[str]) -> str:
    """Start one Sync DB job, holding a claim on `keys` until it finishes."""
    slot, conflict = _claim_sync_slot(keys)
    if slot is None:
        raise _sync_already_running(conflict)

    def guarded():
        try:
            yield from factory()
        finally:
            _release_sync_slot(slot)

    try:
        job_id = JobManager.start_job(guarded)
    except BaseException:
        _release_sync_slot(slot)
        raise
    slot.job_id = job_id
    return job_id


def _format_menu_pull_failure(errors: list) -> str:
    if not errors:
        return "Menu pull failed"
    first = errors[0]
    stream = first.get("stream", "menu")
    message = first.get("error", "unknown error")
    if len(errors) == 1:
        return f"Menu pull failed ({stream}): {message}"
    return f"Menu pull failed ({len(errors)} streams); first: {stream}: {message}"


def _format_customer_pull_failure(errors: list) -> str:
    if not errors:
        return "Customer pull failed"
    first = errors[0]
    stream = first.get("stream", "customer_merges")
    message = first.get("error", "unknown error")
    if len(errors) == 1:
        return f"Customer pull failed ({stream}): {message}"
    return f"Customer pull failed ({len(errors)} streams); first: {stream}: {message}"


def _global_failure_code(messages) -> Optional[str]:
    for candidate in (
        "invalid_api_key",
        "invalid_token",
        "invalid_credentials",
        "restaurant_list_not_configured",
        "restaurant_selector_not_a_parameter",
        "retired_parameter",
        "retired_field",
    ):
        if any(
            message == candidate
            or message.startswith(f"{candidate}:")
            or f" {candidate}:" in message
            for message in messages
        ):
            return candidate
    return None


def _iter_cloud_pull(conn, options: Dict[str, Any], template: SyncStatus):
    """Yield one status per cloud pull step, then return the pull summary.

    The pull is a long chain of round trips to one host. Run it on a worker
    thread so the step it is on can be reported while it waits — a slow server
    reads as a named phase instead of a progress bar that stopped moving.
    """
    phases: "queue.Queue[Optional[str]]" = queue.Queue()
    outcome: Dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = run_best_effort_cloud_pulls(
                conn, on_phase=phases.put, **options
            )
        except BaseException as exc:  # re-raised on the consuming thread
            outcome["error"] = exc
        finally:
            phases.put(None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        while True:
            message = phases.get()
            if message is None:
                break
            yield SyncStatus(
                "info",
                message,
                progress=template.progress,
                current=template.current,
                total=template.total,
            )
    finally:
        # Even when the consumer abandons this generator, the pull has to be
        # done with the connection before the caller closes it.
        worker.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def _menu_items_empty(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()
    return bool(row and row[0] == 0)


def iter_sync_statuses(conn, *, already_locked: bool = False):
    """
    Stream POS sync progress, then best-effort cloud pulls, with a single terminal *done*.

    On an empty catalog with cloud bootstrap configured, menu bootstrap runs before
    order import. sync_database() yields a terminal *done* when the order stream
    finishes; we buffer that instead of forwarding it so JobManager / the UI do not
    mark the job completed while run_best_effort_cloud_pulls() is still running. We
    emit *info* during the cloud phase, then one final *done* that merges order stats
    with cloud_pull (when attempted).
    """
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.menu_bootstrap_sync import (
        fetch_and_apply_menu_bootstrap_snapshot,
        get_menu_bootstrap_apply_mode,
        get_menu_bootstrap_pull_endpoint,
    )

    menu_bootstrap_pulled = False
    pre_bootstrap_error = None
    global_mode_active = False
    global_capability = None
    from src.core.profiles import profile_rebuild_status_for_connection

    rebuild_in_progress = profile_rebuild_status_for_connection(conn) == "rebuilding"
    try:
        from src.core.global_menu_schema import resolve_global_menu_capability

        global_capability = resolve_global_menu_capability(
            conn, allow_profile_sync=True
        )
        global_mode_active = global_capability.active
    except Exception:
        global_mode_active = False

    if global_mode_active:
        from src.core.global_menu_sync import pull_global_menu_state

        yield SyncStatus(
            "info",
            "Pulling global menu rules before order sync...",
        )
        try:
            global_pull = pull_global_menu_state(conn, allow_profile_sync=True)
        except Exception as exc:
            global_pull = {"status": "error", "error": str(exc)}
        if global_pull.get("error") or global_pull.get("status") == "error":
            error_message = str(global_pull.get("error") or "unknown error")
            yield SyncStatus(
                "error",
                f"Global menu pull failed before order sync: {error_message}",
                code=_global_failure_code([error_message])
                or "global_menu_sync_failed",
            )
            return
        yield SyncStatus("info", "Global menu rules are current.")

    if not global_mode_active and _menu_items_empty(conn):
        endpoint = get_menu_bootstrap_pull_endpoint(conn)
        if endpoint:
            menu_bootstrap_pulled = True
            yield SyncStatus("info", "Menu catalog empty — pulling from cloud before order sync...")
            _, auth_key = get_cloud_sync_config(conn)
            try:
                result = fetch_and_apply_menu_bootstrap_snapshot(
                    conn,
                    endpoint,
                    auth=auth_key,
                    apply_mode=get_menu_bootstrap_apply_mode(conn),
                )
                if result.get("error"):
                    pre_bootstrap_error = {"stream": "menu_bootstrap", "error": result["error"]}
                    yield SyncStatus(
                        "info",
                        f"Menu catalog pull failed before order sync: {result['error']}",
                    )
                elif result.get("items_seeded", 0) > 0:
                    yield SyncStatus(
                        "info",
                        f"Menu catalog pulled from cloud ({result['items_seeded']} items).",
                    )
                else:
                    yield SyncStatus("info", "Menu bootstrap pull completed (no catalog rows applied).")
            except Exception as exc:
                pre_bootstrap_error = {"stream": "menu_bootstrap", "error": str(exc)}
                yield SyncStatus("info", f"Menu catalog pull failed before order sync: {exc}")

    final_status = None
    for status in sync_database(conn):
        if status.type == "error":
            yield status
            return
        if status.type == "done":
            final_status = status
            continue
        yield status

    if final_status is None:
        yield SyncStatus("error", "Sync did not produce a terminal status")
        return

    final_stats = dict(final_status.stats or {})
    cloud_phase_template = SyncStatus(
        "info",
        "Order sync complete. Pulling cloud data...",
        progress=final_status.progress or 1.0,
        current=final_status.current,
        total=final_status.total,
        stats=final_stats,
    )
    yield cloud_phase_template

    cloud_pull_options = {
        "skip_menu_bootstrap": menu_bootstrap_pulled,
        "already_locked": already_locked,
    }
    if global_mode_active:
        cloud_pull_options["skip_global_menu_state"] = True
    cloud = yield from _iter_cloud_pull(conn, cloud_pull_options, cloud_phase_template)
    final_message = final_status.message or "Sync complete"
    menu_pull_errors = list(collect_menu_pull_errors(cloud))
    customer_pull_errors = list(collect_customer_pull_errors(cloud))
    if pre_bootstrap_error:
        menu_pull_errors.insert(0, (pre_bootstrap_error["stream"], pre_bootstrap_error["error"]))
    if cloud.get("attempted"):
        final_stats["cloud_pull"] = cloud
        pull_failure_messages: list[str] = []
        if menu_pull_errors:
            final_stats["menu_pull_failed"] = True
            final_stats["menu_pull_errors"] = [
                {"stream": stream, "error": message} for stream, message in menu_pull_errors
            ]
            pull_failure_messages.append(_format_menu_pull_failure(final_stats["menu_pull_errors"]))
        if customer_pull_errors:
            final_stats["customer_pull_failed"] = True
            final_stats["customer_pull_errors"] = [
                {"stream": stream, "error": message} for stream, message in customer_pull_errors
            ]
            pull_failure_messages.append(
                _format_customer_pull_failure(final_stats["customer_pull_errors"])
            )
        customer_pull_warnings = list(collect_customer_pull_warnings(cloud))
        if customer_pull_warnings:
            final_stats["customer_pull_warnings"] = [
                {"stream": stream, "warning": message} for stream, message in customer_pull_warnings
            ]
        forecast_pull_warnings = list(collect_forecast_pull_warnings(cloud))
        if forecast_pull_warnings:
            final_stats["forecast_pull_warnings"] = [
                {"stream": stream, "warning": message}
                for stream, message in forecast_pull_warnings
            ]
        history_pull_warnings = list(collect_global_menu_history_warnings(cloud))
        if history_pull_warnings:
            final_stats["global_menu_history_warnings"] = [
                {"stream": stream, "warning": message}
                for stream, message in history_pull_warnings
            ]
        if pull_failure_messages:
            final_message = f"{final_message} · {' · '.join(pull_failure_messages)}"
        else:
            final_message = f"{final_message} · Cloud pull finished"
            pull_warnings = (
                customer_pull_warnings
                + forecast_pull_warnings
                + history_pull_warnings
            )
            for _stream, warning in pull_warnings:
                final_message = f"{final_message} · Warning: {warning}"

    rebuild_errors: list[str] = []
    if global_mode_active:
        try:
            from src.core.queries.global_menu_diagnostics import (
                fetch_global_menu_diagnostics,
            )

            diagnostics = fetch_global_menu_diagnostics(conn)
            final_stats["global_menu_diagnostics"] = diagnostics
            if rebuild_in_progress:
                if diagnostics["bootstrap_state"] != "complete":
                    rebuild_errors.append("global catalog bootstrap is incomplete")
                if diagnostics["quarantine_count"]:
                    rebuild_errors.append(
                        f"{diagnostics['quarantine_count']} global menu payload(s) remain quarantined"
                    )
        except Exception as exc:
            if rebuild_in_progress:
                rebuild_errors.append(f"diagnostics failed: {exc}")

    terminal_type = (
        "error"
        if (menu_pull_errors or customer_pull_errors or rebuild_errors)
        else "done"
    )
    failure_code = None
    if menu_pull_errors or customer_pull_errors:
        failure_messages = [message for _stream, message in (*menu_pull_errors, *customer_pull_errors)]
        failure_code = _global_failure_code(failure_messages)
        if failure_code:
            final_stats["failure_code"] = failure_code

    if rebuild_errors:
        failure_code = failure_code or "clean_rebuild_incomplete"
        final_stats["failure_code"] = failure_code
        final_stats["clean_rebuild_errors"] = rebuild_errors
        final_message = f"{final_message} · Clean rebuild incomplete: {'; '.join(rebuild_errors)}"
    elif rebuild_in_progress and terminal_type == "done":
        try:
            from src.core.profiles import complete_profile_rebuild_for_connection

            if not complete_profile_rebuild_for_connection(conn):
                raise RuntimeError("profile rebuild marker changed before finalization")
            final_stats["clean_rebuild_status"] = "complete"
        except Exception as exc:
            terminal_type = "error"
            failure_code = "clean_rebuild_finalize_failed"
            final_stats["failure_code"] = failure_code
            final_message = f"{final_message} · Clean rebuild finalization failed: {exc}"

    yield SyncStatus(
        terminal_type,
        final_message,
        progress=final_status.progress or 1.0,
        current=final_status.current,
        total=final_status.total,
        stats=final_stats,
        code=failure_code,
    )


@router.post("/run", response_model=JobResponse)
def run_sync(request: SyncRunRequest, scope: AnalyticsScope = Depends(get_analytics_scope)):
    """Start Sync DB for one restaurant, or for every store in All Stores mode.

    The restaurant list (or the single profile) is frozen here, before the job
    thread starts, so a later selection or allow-list change cannot redirect
    work that is already running.
    """
    if scope.is_all:
        if request.restaurant_id != ALL_STORES_TOKEN:
            raise HTTPException(
                status_code=409,
                detail={"error": "Sync scope changed before job capture", "code": "profile_scope_mismatch"},
            )
        captured = tuple(scope.profiles)

        def all_stores_wrapper():
            from src.core.services.all_stores_sync import iter_all_stores_sync

            yield from iter_all_stores_sync(captured)

        job_id = _start_guarded_sync_job(
            all_stores_wrapper,
            (ALL_STORES_TOKEN, *(profile.restaurant_id for profile in captured)),
        )
        return {
            "job_id": job_id,
            "status": "queued",
            "message": f"Sync started for {len(captured)} store(s)",
            "progress": 0.0,
        }

    profile = scope.profile
    if profile.authorization_state != "authorized":
        raise HTTPException(
            status_code=403,
            detail={
                "error": f"Restaurant profile is not authorized: {profile.restaurant_id}",
                "code": "restaurant_forbidden",
            },
        )
    if request.restaurant_id != profile.restaurant_id:
        raise HTTPException(
            status_code=409,
            detail={"error": "Sync scope changed before job capture", "code": "profile_scope_mismatch"},
        )

    def sync_wrapper():
        # The immutable profile was captured before this thread started.
        captured_profile = profile
        if captured_profile.clean_rebuild_status == "required":
            yield SyncStatus(
                "error",
                "This profile must be archived and reset before Sync DB can rebuild it",
                code="clean_profile_rebuild_required",
            )
            return
        if captured_profile.clean_rebuild_status == "rebuilding":
            yield SyncStatus(
                "info",
                "Refreshing restaurant registry and shared-menu capabilities...",
            )
            try:
                from pathlib import Path

                from src.core.profiles import (
                    get_profile,
                    refresh_allowed_restaurants_from_server,
                )

                refresh_allowed_restaurants_from_server()
                refreshed = get_profile(
                    captured_profile.restaurant_id, require_authorized=True
                )
                if Path(refreshed.database_path).resolve() != Path(
                    captured_profile.database_path
                ).resolve():
                    raise RuntimeError("Restaurant profile path changed during rebuild capture")
                captured_profile = refreshed
            except Exception as exc:
                yield SyncStatus(
                    "error",
                    f"Clean rebuild registry refresh failed: {exc}",
                    code=str(
                        getattr(exc, "code", "clean_rebuild_registry_refresh_failed")
                    ),
                )
                return

        conn, _ = get_profile_connection(captured_profile)

        try:
            yield from iter_sync_statuses(conn)
        finally:
            conn.close()

    job_id = _start_guarded_sync_job(sync_wrapper, (profile.restaurant_id,))
    return {
        "job_id": job_id,
        "status": "queued",
        "message": f"Sync started for {profile.display_name}",
        "progress": 0.0
    }

@router.get("/status/{job_id}", response_model=JobResponse)
def get_sync_status(job_id: str):
    job = JobManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}


@router.post("/client-learning")
def run_client_learning(
    conn=Depends(get_db),
    _profile=Depends(get_authorized_restaurant_profile),
):
    """
    Run all cloud push uploads: error logs, ai_logs + ai_feedback, menu bootstrap,
    customer merge events, menu merge events, and forecasts.
    Uses cloud_sync_url / cloud_sync_api_key from Configuration (same as the 5‑minute scheduler).
    """
    result = run_client_learning_shippers(conn)
    return {"status": "ok", "result": result}
