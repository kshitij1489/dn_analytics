"""
Forecast Shipper — Upload cached revenue and item forecasts + backtest caches to cloud.

Follows the same pattern as learning_shipper.py:
  - Queries rows where uploaded_at IS NULL
  - POSTs to /desktop-analytics-sync/forecasts/ingest
  - On success, sets uploaded_at = NOW()

Integrated via client_learning_shipper.run_all().
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Max rows per batch to avoid huge payloads
BATCH_LIMIT_REVENUE = 500
BATCH_LIMIT_ITEMS = 1000
BATCH_LIMIT_REVENUE_BACKTEST = 500
BATCH_LIMIT_ITEM_BACKTEST = 1000


def _select_unsent_revenue_forecasts(conn, limit: int = BATCH_LIMIT_REVENUE) -> List[Dict[str, Any]]:
    """Select forecast_cache rows where uploaded_at IS NULL."""
    try:
        cursor = conn.execute("""
            SELECT id, forecast_date, model_name, generated_on,
                   revenue, orders, pred_std, lower_95, upper_95,
                   temp_max, rain_category
            FROM forecast_cache
            WHERE uploaded_at IS NULL
            ORDER BY generated_on, forecast_date
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.debug(f"Could not read forecast_cache: {e}")
        return []


def _select_unsent_item_forecasts(conn, limit: int = BATCH_LIMIT_ITEMS) -> List[Dict[str, Any]]:
    """Select item_forecast_cache rows where uploaded_at IS NULL."""
    try:
        cursor = conn.execute("""
            SELECT id, forecast_date, item_id, generated_on,
                   p50, p90, probability, recommended_prep
            FROM item_forecast_cache
            WHERE uploaded_at IS NULL
            ORDER BY generated_on, forecast_date
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.debug(f"Could not read item_forecast_cache: {e}")
        return []


def _select_unsent_revenue_backtest(conn, limit: int = BATCH_LIMIT_REVENUE_BACKTEST) -> List[Dict[str, Any]]:
    """Select revenue_backtest_cache rows where uploaded_at IS NULL."""
    try:
        cursor = conn.execute("""
            SELECT id, forecast_date, model_name, model_trained_through,
                   revenue, orders, pred_std, lower_95, upper_95
            FROM revenue_backtest_cache
            WHERE uploaded_at IS NULL
            ORDER BY model_trained_through, forecast_date
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.debug(f"Could not read revenue_backtest_cache: {e}")
        return []


def _select_unsent_item_backtest(conn, limit: int = BATCH_LIMIT_ITEM_BACKTEST) -> List[Dict[str, Any]]:
    """Select item_backtest_cache rows where uploaded_at IS NULL."""
    try:
        cursor = conn.execute("""
            SELECT id, forecast_date, item_id, model_trained_through,
                   p50, p90, probability
            FROM item_backtest_cache
            WHERE uploaded_at IS NULL
            ORDER BY model_trained_through, forecast_date
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    except Exception as e:
        logger.debug(f"Could not read item_backtest_cache: {e}")
        return []


def upload_pending(
    conn,
    endpoint: Optional[str] = None,
    auth: Optional[str] = None,
    uploaded_by: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Upload unsent forecast cache rows (revenue, item, backtest) to cloud.

    Args:
        conn: SQLite connection.
        endpoint: Full URL for POST (e.g. https://server/desktop-analytics-sync/forecasts/ingest).
        auth: Bearer token (optional).
        uploaded_by: {"employee_id": "...", "name": "..."} for attribution.

    Returns:
        {"revenue_sent": int, "items_sent": int, "revenue_backtest_sent": int,
         "item_backtest_sent": int, "error": str or None}
    """
    from src.core.config.client_learning_config import (
        CLIENT_LEARNING_FORECAST_INGEST_URL,
    )

    url = (endpoint or CLIENT_LEARNING_FORECAST_INGEST_URL).strip()
    if not url:
        return {"revenue_sent": 0, "items_sent": 0, "revenue_backtest_sent": 0, "item_backtest_sent": 0, "error": None}

    token = auth

    revenue_rows = _select_unsent_revenue_forecasts(conn)
    item_rows = _select_unsent_item_forecasts(conn)
    revenue_backtest_rows = _select_unsent_revenue_backtest(conn)
    item_backtest_rows = _select_unsent_item_backtest(conn)

    if not revenue_rows and not item_rows and not revenue_backtest_rows and not item_backtest_rows:
        return {
            "revenue_sent": 0,
            "items_sent": 0,
            "revenue_backtest_sent": 0,
            "item_backtest_sent": 0,
            "error": None,
        }

    def strip_id(rows: List[Dict]) -> List[Dict]:
        return [{k: v for k, v in r.items() if k != "id"} for r in rows]

    payload: Dict[str, Any] = {
        "revenue_forecasts": strip_id(revenue_rows),
        "item_forecasts": strip_id(item_rows),
        "revenue_backtest": strip_id(revenue_backtest_rows),
        "item_backtest": strip_id(item_backtest_rows),
    }
    if uploaded_by:
        payload["uploaded_by"] = uploaded_by

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        import requests
        r = requests.post(url, json=payload, headers=headers, timeout=60)
        if r.status_code >= 400:
            return {
                "revenue_sent": 0,
                "items_sent": 0,
                "revenue_backtest_sent": 0,
                "item_backtest_sent": 0,
                "error": f"HTTP {r.status_code}",
            }
    except Exception as e:
        return {
            "revenue_sent": 0,
            "items_sent": 0,
            "revenue_backtest_sent": 0,
            "item_backtest_sent": 0,
            "error": str(e),
        }

    # Mark as uploaded
    now = datetime.now(timezone.utc).isoformat()

    for rows, table in [
        (revenue_rows, "forecast_cache"),
        (item_rows, "item_forecast_cache"),
        (revenue_backtest_rows, "revenue_backtest_cache"),
        (item_backtest_rows, "item_backtest_cache"),
    ]:
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            try:
                conn.execute(
                    f"UPDATE {table} SET uploaded_at = ? WHERE id IN ({placeholders})",
                    [now] + ids,
                )
            except Exception as e:
                logger.warning(f"Failed to mark {table} as uploaded: {e}")

    conn.commit()

    return {
        "revenue_sent": len(revenue_rows),
        "items_sent": len(item_rows),
        "revenue_backtest_sent": len(revenue_backtest_rows),
        "item_backtest_sent": len(item_backtest_rows),
        "error": None,
    }
