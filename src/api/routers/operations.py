from fastapi import APIRouter, Depends, HTTPException
from src.api.dependencies import get_db
from src.core.db.connection import get_db_connection
from src.core.services.sync_service import sync_database
from src.core.client_learning_shipper import run_all as run_client_learning_shippers
from src.api.job_manager import JobManager
from src.api.models import JobResponse

router = APIRouter()

@router.post("/run", response_model=JobResponse)
def run_sync():
    def sync_wrapper():
        # Open connection inside the thread
        conn, err = get_db_connection()
        if conn is None:
             raise Exception(f"DB Connection failed: {err}")

        try:
           yield from sync_database(conn)
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
    Run all client-learning uploads: error logs, ai_logs + ai_feedback, menu bootstrap.
    Uses placeholder URLs by default; set CLIENT_LEARNING_* env vars for real cloud.
    """
    result = run_client_learning_shippers(conn)
    return {"status": "ok", "result": result}
