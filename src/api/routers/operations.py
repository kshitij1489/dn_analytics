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
    try:
        from src.core.global_menu_schema import resolve_global_menu_capability

        global_mode_active = resolve_global_menu_capability(
            conn, allow_profile_sync=True
        ).active
    except Exception:
        global_mode_active = False

    if global_mode_active:
        from src.core.global_menu_sync import pull_global_menu_state

        yield SyncStatus(
            "info",
            "Pulling global menu rules before order sync...",
        )
        global_pull = pull_global_menu_state(conn, allow_profile_sync=True)
        if global_pull.get("error") or global_pull.get("status") == "error":
            yield SyncStatus(
                "error",
                f"Global menu pull failed before order sync: {global_pull.get('error') or 'unknown error'}",
                code="global_menu_sync_failed",
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
    yield SyncStatus(
        "info",
        "Order sync complete. Pulling cloud data...",
        progress=final_status.progress or 1.0,
        current=final_status.current,
        total=final_status.total,
        stats=final_stats,
    )

    cloud = run_best_effort_cloud_pulls(
        conn, skip_menu_bootstrap=menu_bootstrap_pulled, already_locked=already_locked
    )
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

    terminal_type = "error" if (menu_pull_errors or customer_pull_errors) else "done"
    failure_code = None
    if menu_pull_errors or customer_pull_errors:
        failure_messages = [message for _stream, message in (*menu_pull_errors, *customer_pull_errors)]
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
                for message in failure_messages
            ):
                failure_code = candidate
                break
        if failure_code:
            final_stats["failure_code"] = failure_code

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

        job_id = JobManager.start_job(all_stores_wrapper)
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
        conn, _ = get_profile_connection(profile)

        try:
            yield from iter_sync_statuses(conn)
        finally:
            conn.close()

    job_id = JobManager.start_job(sync_wrapper)
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
