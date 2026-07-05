"""
Fire-and-forget push nudge after local menu merge/resolution commits
(plan C5.3): ship the freshly-emitted merge event now instead of waiting up to
5 minutes for the scheduler. This is a latency optimization layered on the
deterministic assignment model — never a correctness mechanism.
"""

import logging
import threading


logger = logging.getLogger(__name__)


def nudge_menu_merge_push_async(conn) -> bool:
    """
    Kick off a background upload of pending menu merge events.

    The cloud config is read on the caller's connection first: when no cloud
    URL is configured (dev setups, unit tests) no thread is spawned. The
    worker opens its own connection; all failures are swallowed — the
    5-minute scheduler remains the reliable path.
    """
    try:
        from src.core.config.cloud_sync_config import get_cloud_sync_config

        base_url, auth = get_cloud_sync_config(conn)
    except Exception:
        return False
    if not base_url:
        return False

    endpoint = f"{base_url}/desktop-analytics-sync/menu-merges/ingest"
    thread = threading.Thread(
        target=_run_nudge,
        args=(endpoint, auth),
        name="menu-merge-push-nudge",
        daemon=True,
    )
    thread.start()
    return True


def _run_nudge(endpoint: str, auth) -> None:
    conn = None
    try:
        from src.core.db.connection import get_db_connection
        from src.core.menu_merge_shipper import upload_pending

        conn, _ = get_db_connection()
        if conn is None:
            return
        upload_pending(conn, endpoint=endpoint, auth=auth)
    except Exception:
        logger.debug("Menu merge push nudge failed", exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
