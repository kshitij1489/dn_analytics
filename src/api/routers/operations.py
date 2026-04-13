from fastapi import APIRouter, Depends, HTTPException
from src.api.dependencies import get_db
from src.core.db.connection import get_db_connection
from src.core.services.sync_service import SyncStatus, sync_database
from src.core.services.cloud_pull_orchestrator import run_best_effort_cloud_pulls
from src.core.client_learning_shipper import run_all as run_client_learning_shippers
from src.api.job_manager import JobManager
from src.api.models import JobResponse

router = APIRouter()

def iter_sync_statuses(conn):
    """
    Stream POS sync progress, then best-effort cloud pulls, with a single terminal *done*.

    sync_database() yields a terminal *done* when the order stream finishes; we buffer that
    instead of forwarding it so JobManager / the UI do not mark the job completed while
    run_best_effort_cloud_pulls() is still running. We emit *info* during the cloud phase, then
    one final *done* that merges order stats with cloud_pull (when attempted).
    """
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

    cloud = run_best_effort_cloud_pulls(conn)
    final_message = final_status.message or "Sync complete"
    if cloud.get("attempted"):
        final_stats["cloud_pull"] = cloud
        final_message = f"{final_message} · Cloud pull (best-effort) finished"

    yield SyncStatus(
        "done",
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


@router.post("/client-learning")
def run_client_learning(conn=Depends(get_db)):
    """
    Run all cloud push uploads: error logs, ai_logs + ai_feedback, menu bootstrap,
    customer merge events, menu merge events, and forecasts.
    Uses placeholder URLs by default; set CLIENT_LEARNING_* env vars for real cloud.
    """
    result = run_client_learning_shippers(conn)
    return {"status": "ok", "result": result}
