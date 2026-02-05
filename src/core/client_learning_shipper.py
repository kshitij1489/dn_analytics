"""
Orchestrator for all client-learning uploads: errors, learning (ai_logs + ai_feedback), menu bootstrap.

Call run_all(conn) periodically (e.g. from a background task or POST /api/sync/client-learning).
Uses placeholder URLs by default; set env vars for plug-and-play when cloud is ready.
Appends uploaded_by (employee_id, name from app_users) to every payload so cloud knows which employee the upload is from.
"""

from typing import Any, Dict, Optional

from src.core.error_shipper import upload_pending as upload_errors
from src.core.learning_shipper import upload_pending as upload_learning
from src.core.menu_bootstrap_shipper import upload_pending as upload_menu_bootstrap


def get_uploaded_by(conn) -> Optional[Dict[str, str]]:
    """
    Return the current app user for cloud payload attribution.
    Reads from app_users (singleton). Returns {"employee_id": "...", "name": "..."} or None if no user.
    """
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT employee_id, name FROM app_users WHERE is_active = 1 LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {"employee_id": row[0], "name": row[1]}
    except Exception:
        return None


def run_all(conn, log_dir: Optional[str] = None, base_url: Optional[str] = None, auth: Optional[str] = None) -> Dict[str, Any]:
    """
    Run all client-learning shippers: error logs, ai_logs + ai_feedback, menu bootstrap.
    Appends uploaded_by (from app_users) to every payload so cloud knows which employee the upload is from.
    conn: database connection (used for learning shipper and to read app_users).
    log_dir: optional override for error log directory.
    Returns combined result: { "errors": {...}, "learning": {...}, "menu_bootstrap": {...} }.
    """
    result: Dict[str, Any] = {"errors": {}, "learning": {}, "menu_bootstrap": {}}
    uploaded_by = get_uploaded_by(conn) if conn else None

    # Pass configured endpoints/auth to sub-shippers if base_url is provided.
    # Each sub-shipper (error, learning, menu) accepts (endpoint=..., auth=...).
    
    error_kwargs = {"uploaded_by": uploaded_by, "log_dir": log_dir}
    learning_kwargs = {"uploaded_by": uploaded_by}
    menu_kwargs = {"uploaded_by": uploaded_by}
    
    if base_url:
         # Construct specific endpoints
         base = base_url.rstrip("/")
         error_kwargs["endpoint"] = f"{base}/desktop-analytics-sync/errors/ingest"
         learning_kwargs["endpoint"] = f"{base}/desktop-analytics-sync/learning/ingest"
         menu_kwargs["endpoint"] = f"{base}/desktop-analytics-sync/menu-bootstrap/ingest"

    if auth:
         error_kwargs["auth"] = auth
         learning_kwargs["auth"] = auth
         menu_kwargs["auth"] = auth

    result["errors"] = upload_errors(**error_kwargs)
    result["learning"] = (
        upload_learning(conn, **learning_kwargs)
        if conn
        else {"ai_logs_sent": 0, "ai_feedback_sent": 0, "tier3_included": False, "error": "No connection"}
    )
    result["menu_bootstrap"] = upload_menu_bootstrap(**menu_kwargs)

    return result
