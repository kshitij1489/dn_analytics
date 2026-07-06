from fastapi import APIRouter, Depends, HTTPException
from src.api.dependencies import get_db
from src.core.db.connection import get_db_connection
from src.core.services.sync_service import SyncStatus, sync_database
from src.core.services.cloud_pull_orchestrator import (
    collect_customer_pull_errors,
    collect_customer_pull_warnings,
    collect_menu_pull_errors,
    run_best_effort_cloud_pulls,
)
from src.core.client_learning_shipper import run_all as run_client_learning_shippers
from src.core.customer_outbox_drain import drain_customer_outbox, get_customer_outbox_status
from src.core.menu_outbox_drain import drain_menu_outbox, get_menu_outbox_status
from src.api.job_manager import JobManager
from src.api.models import JobResponse

router = APIRouter()


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


def iter_sync_statuses(conn):
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
    if _menu_items_empty(conn):
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

    cloud = run_best_effort_cloud_pulls(conn, skip_menu_bootstrap=menu_bootstrap_pulled)
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
        if pull_failure_messages:
            final_message = f"{final_message} · {' · '.join(pull_failure_messages)}"
        else:
            final_message = f"{final_message} · Cloud pull finished"
            if customer_pull_warnings:
                final_message = f"{final_message} · Warning: {customer_pull_warnings[0][1]}"

    terminal_type = "error" if (menu_pull_errors or customer_pull_errors) else "done"
    yield SyncStatus(
        terminal_type,
        final_message,
        progress=final_status.progress or 1.0,
        current=final_status.current,
        total=final_status.total,
        stats=final_stats,
    )


@router.post("/run", response_model=JobResponse)
def run_sync():
    def sync_wrapper():
        # Open connection inside the thread
        conn, err = get_db_connection()
        if conn is None:
            raise Exception(f"DB Connection failed: {err}")

        try:
            yield from iter_sync_statuses(conn)
        finally:
            conn.close()

    job_id = JobManager.start_job(sync_wrapper)
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Sync started",
        "progress": 0.0
    }

@router.get("/status/{job_id}", response_model=JobResponse)
def get_sync_status(job_id: str):
    job = JobManager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}


@router.get("/menu-rollout-status")
def menu_rollout_status(conn=Depends(get_db)):
    """
    Phase 6 rollout: unsent legacy menu outbox counts and strict-mode mirror state.
    Drain outboxes on every install before the server flips strict_mode_enabled.
    """
    return {"status": "ok", "result": get_menu_outbox_status(conn)}


@router.post("/drain-menu-outbox")
def drain_menu_outbox_endpoint(conn=Depends(get_db)):
    """
    Upload all unsent legacy menu-merge and mapping-verification events.
    Run on each install before enabling server strict mode.
    """
    result = drain_menu_outbox(conn)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result)
    return {"status": "ok", "result": result}


@router.get("/customer-rollout-status")
def customer_rollout_status(conn=Depends(get_db)):
    """
    Phase 6 rollout: unsent legacy customer outbox counts and strict-mode mirror state.
    Drain outboxes on every install before the server flips strict_mode_enabled.
    """
    return {"status": "ok", "result": get_customer_outbox_status(conn)}


@router.post("/drain-customer-outbox")
def drain_customer_outbox_endpoint(conn=Depends(get_db)):
    """
    Upload all unsent legacy customer merge events.
    Run on each install before enabling server strict mode.
    """
    result = drain_customer_outbox(conn)
    if result.get("status") == "error":
        raise HTTPException(status_code=502, detail=result)
    return {"status": "ok", "result": result}


@router.post("/client-learning")
def run_client_learning(conn=Depends(get_db)):
    """
    Run all cloud push uploads: error logs, ai_logs + ai_feedback, menu bootstrap,
    customer merge events, menu merge events, and forecasts.
    Uses cloud_sync_url / cloud_sync_api_key from Configuration (same as the 5‑minute scheduler).
    """
    result = run_client_learning_shippers(conn)
    return {"status": "ok", "result": result}
