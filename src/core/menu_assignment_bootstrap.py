"""
Fresh-install fast path (plan Phase C4.1): seed per-order-item assignments from
the server's materialized snapshot instead of replaying the whole merge event
log, then set the menu-merge pull cursor to the snapshot watermark so the tail
picks up with seq > watermark — same fixed point as full replay, without I4.
"""

import logging
from typing import Any, Dict, Optional

from src.core.menu_assignment_apply import apply_assignments, coerce_server_seq
from src.core.menu_merge_sync import (
    _ensure_pull_tables,
    get_menu_merge_pull_cursor,
    set_menu_merge_pull_cursor,
)


logger = logging.getLogger(__name__)

MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY = "menu_assignments_bootstrapped"
SNAPSHOT_PAGE_LIMIT = 500


def get_menu_assignments_snapshot_endpoint(conn) -> Optional[str]:
    from src.core.config.cloud_sync_config import get_cloud_sync_config

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url}/desktop-analytics-sync/menu-assignments/snapshot"
    return None


def _get_config_value(conn, key: str) -> Optional[str]:
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = ? LIMIT 1", (key,)
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _set_config_value(conn, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def _fetch_snapshot_page(
    endpoint: str,
    auth: Optional[str],
    after: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    params: Dict[str, str] = {"limit": str(limit)}
    if after:
        params["after"] = after

    try:
        import requests

        response = requests.get(endpoint, headers=headers, params=params, timeout=60)
        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}"}
        data = response.json()
    except Exception as exc:
        return {"error": str(exc)}

    if not isinstance(data, dict) or not isinstance(data.get("assignments"), list):
        return {"error": "Invalid snapshot response payload"}
    return {"error": None, **data}


def bootstrap_menu_assignments_if_needed(
    conn,
    endpoint: str,
    auth: Optional[str] = None,
    page_limit: int = SNAPSHOT_PAGE_LIMIT,
) -> Dict[str, Any]:
    """
    One-time snapshot seed for installs that have never pulled menu merges.

    Installs that already have a pull cursor are not fresh: they are marked
    bootstrapped without touching their state (their history came from
    replay). Failures leave the flag unset so the next pull retries.
    """
    _ensure_pull_tables(conn)

    if _get_config_value(conn, MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY):
        return {"status": "already_bootstrapped", "rows_applied": 0}

    if get_menu_merge_pull_cursor(conn):
        _set_config_value(conn, MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY, "existing-install")
        conn.commit()
        return {"status": "existing_install", "rows_applied": 0}

    rows_applied = 0
    rows_missing = 0
    watermark_seq: Optional[int] = None
    watermark_cursor: Optional[str] = None
    after: Optional[str] = None

    while True:
        page = _fetch_snapshot_page(endpoint, auth, after, page_limit)
        if page.get("error"):
            conn.rollback()
            return {"status": "error", "error": page["error"], "rows_applied": rows_applied}

        # The first page's watermark is the tail cut-off: later pages may
        # already contain newer rows, but replaying seq > watermark over them
        # is idempotent under the seq guard.
        if watermark_seq is None:
            watermark_seq = coerce_server_seq(page.get("watermark_seq"))
            watermark_cursor = page.get("watermark_cursor") or None

        assignments = []
        for row in page["assignments"]:
            if not isinstance(row, dict):
                continue
            assignment = {
                "order_item_id": str(row.get("order_item_id") or "").strip(),
                "menu_item_id": str(row.get("menu_item_id") or "").strip(),
                "variant_id": row.get("variant_id"),
                "is_verified": row.get("is_verified", 1),
            }
            if assignment["order_item_id"] and assignment["menu_item_id"]:
                assignments.append(assignment)

        if assignments:
            result = apply_assignments(
                conn,
                assignments,
                watermark_seq,
                event={},
                detect_supersede=False,
            )
            rows_applied += result["rows_applied"]
            rows_missing += result["rows_missing"]

        after = page.get("next_page") or None
        if not after:
            break

    if watermark_cursor:
        set_menu_merge_pull_cursor(conn, watermark_cursor)
    _set_config_value(conn, MENU_ASSIGNMENTS_BOOTSTRAPPED_KEY, "snapshot")
    conn.commit()

    logger.info(
        "Menu assignments bootstrapped from snapshot: %d rows applied, %d without local order items, watermark_seq=%s",
        rows_applied,
        rows_missing,
        watermark_seq,
    )
    return {
        "status": "bootstrapped",
        "rows_applied": rows_applied,
        "rows_missing": rows_missing,
        "watermark_seq": watermark_seq,
        "cursor_set": bool(watermark_cursor),
    }
