"""
Orchestrator for client-learning uploads: errors, learning, menu bootstrap.

Forecast upload removed — central server is the sole forecast publisher (Phase 5).
Menu-merge / customer-merge / menu-mapping-verification legacy batch uploads removed —
the server is strict-only and those events now go through the mutation-commit path
(src/core/menu_mutation_commit.py, src/core/customer_mutation_commit.py).

The three uploads have different scopes, so they are separate entry points:

* ``run_global_uploads`` — error-log files live in one app-wide log directory.
  They are uploaded **once per cycle**, not once per restaurant, so a multi-profile
  loop must not re-send them for every store.
* ``run_profile_telemetry_uploads`` — ai_logs / ai_feedback rows are read from one
  profile database but stay **unscoped on the wire** (``learning/ingest`` takes no
  restaurant selector), so they run per profile without a restaurant header.
* ``run_scoped_uploads`` — the menu bootstrap seed snapshot is restaurant-scoped and
  carries the profile's ``X-Restaurant-ID``; it runs once per profile.

``run_all`` composes all three for the single-profile Phase 1 path.
"""

from typing import Any, Dict, Optional

from src.core.config.cloud_sync_config import get_cloud_sync_config

from src.core.error_shipper import upload_pending as upload_errors
from src.core.learning_shipper import upload_pending as upload_learning
from src.core.menu_bootstrap_shipper import upload_pending as upload_menu_bootstrap
from src.core.sync_identity import get_active_user_identity, get_device_identity


def get_uploaded_by(conn) -> Optional[Dict[str, str]]:
    return get_active_user_identity(conn)


def get_uploaded_from(conn) -> Optional[Dict[str, str]]:
    if conn is None:
        return None
    try:
        return get_device_identity(conn)
    except Exception:
        return None


def _resolve_cloud_config(conn, base_url: Optional[str], auth: Optional[str]):
    if conn and (not base_url or not auth):
        db_url, db_auth = get_cloud_sync_config(conn)
        base_url = base_url or db_url
        auth = auth or db_auth
    return base_url, auth


def _endpoint(base_url: Optional[str], path: str) -> Optional[str]:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/desktop-analytics-sync/{path}"


def run_global_uploads(
    conn=None,
    log_dir: Optional[str] = None,
    base_url: Optional[str] = None,
    auth: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload app-global error-log files. Run once per cycle, never per store."""
    base_url, auth = _resolve_cloud_config(conn, base_url, auth)
    kwargs: Dict[str, Any] = {
        "uploaded_by": get_uploaded_by(conn) if conn else None,
        "log_dir": log_dir,
    }
    endpoint = _endpoint(base_url, "errors/ingest")
    if endpoint:
        kwargs["endpoint"] = endpoint
    if auth:
        kwargs["auth"] = auth
    return upload_errors(**kwargs)


def run_profile_telemetry_uploads(
    conn,
    base_url: Optional[str] = None,
    auth: Optional[str] = None,
) -> Dict[str, Any]:
    """Drain one profile's ai_logs/ai_feedback. Unscoped on the wire by design."""
    if conn is None:
        return {
            "ai_logs_sent": 0,
            "ai_feedback_sent": 0,
            "tier3_included": False,
            "error": "No connection",
        }
    base_url, auth = _resolve_cloud_config(conn, base_url, auth)
    kwargs: Dict[str, Any] = {"uploaded_by": get_uploaded_by(conn)}
    endpoint = _endpoint(base_url, "learning/ingest")
    if endpoint:
        kwargs["endpoint"] = endpoint
    if auth:
        kwargs["auth"] = auth
    return upload_learning(conn, **kwargs)


def run_scoped_uploads(
    conn,
    base_url: Optional[str] = None,
    auth: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload one profile's menu bootstrap seed with that profile's restaurant header."""
    if conn is None:
        return {"sent": False, "error": "No connection"}
    base_url, auth = _resolve_cloud_config(conn, base_url, auth)
    kwargs: Dict[str, Any] = {
        "uploaded_by": get_uploaded_by(conn),
        "uploaded_from": get_uploaded_from(conn),
    }
    endpoint = _endpoint(base_url, "menu-bootstrap/ingest")
    if endpoint:
        kwargs["endpoint"] = endpoint
    if auth:
        kwargs["auth"] = auth
    return upload_menu_bootstrap(conn, **kwargs)


def run_all(
    conn,
    log_dir: Optional[str] = None,
    base_url: Optional[str] = None,
    auth: Optional[str] = None,
    *,
    include_global_uploads: bool = True,
) -> Dict[str, Any]:
    """Run every upload for one profile.

    Phase 2 fan-out passes ``include_global_uploads=False`` for every store after
    the first so app-global error files are shipped exactly once per cycle.
    """
    base_url, auth = _resolve_cloud_config(conn, base_url, auth)
    return {
        "errors": (
            run_global_uploads(conn, log_dir=log_dir, base_url=base_url, auth=auth)
            if include_global_uploads
            else {"skipped": "already uploaded this cycle"}
        ),
        "learning": run_profile_telemetry_uploads(conn, base_url=base_url, auth=auth),
        "menu_bootstrap": run_scoped_uploads(conn, base_url=base_url, auth=auth),
    }
