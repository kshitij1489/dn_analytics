"""
Forecast Bootstrap — Fetch precomputed forecast cache from cloud and seed local SQLite.

Used by new .dmg installs to avoid expensive recomputation on first launch.
GET /desktop-analytics-sync/forecasts/bootstrap returns revenue_forecasts, item_forecasts,
revenue_backtest, item_backtest. We insert with uploaded_at='bootstrap' so seeded data
is skipped by the forecast shipper (which only uploads rows where uploaded_at IS NULL).
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def get_bootstrap_endpoint(conn) -> Optional[str]:
    """
    Resolve forecast bootstrap endpoint URL for manual pull.
    Uses system_config cloud_sync_url if set, else CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL.
    """
    from src.core.config.client_learning_config import CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL
    from src.core.config.cloud_sync_config import get_cloud_sync_config

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url}/desktop-analytics-sync/forecasts/bootstrap"
    return CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL if CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL else None


def fetch_and_seed_forecast_bootstrap(
    conn,
    endpoint: str,
    auth: Optional[str] = None,
    generated_on: Optional[str] = None,
    backtest_days: Optional[int] = None,
    scope: str = "all",
) -> Dict[str, Any]:
    """
    GET bootstrap data from cloud and insert into local forecast tables.

    Args:
        conn: SQLite connection.
        endpoint: Full URL for GET (e.g. https://server/desktop-analytics-sync/forecasts/bootstrap).
        auth: Bearer token (optional).
        generated_on: Optional query param to filter to specific generation date.
        backtest_days: Optional max days of backtest to request.
        scope: "revenue" | "items" | "all" — only seed the specified cache(s).

    Returns:
        {"revenue_inserted": int, "item_inserted": int, "revenue_backtest_inserted": int,
         "item_backtest_inserted": int, "error": str or None}
    """
    from src.core.learning.revenue_forecasting.forecast_cache import ensure_tables_exist

    ensure_tables_exist(conn)

    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    params: Dict[str, str] = {}
    if generated_on:
        params["generated_on"] = generated_on
    if backtest_days is not None:
        params["backtest_days"] = str(backtest_days)
    if scope != "all":
        params["scope"] = scope

    try:
        import requests
        r = requests.get(endpoint, headers=headers, params=params or None, timeout=60)
        if r.status_code >= 400:
            return {
                "revenue_inserted": 0,
                "item_inserted": 0,
                "revenue_backtest_inserted": 0,
                "item_backtest_inserted": 0,
                "error": f"HTTP {r.status_code}",
            }
        data = r.json()
    except Exception as e:
        return {
            "revenue_inserted": 0,
            "item_inserted": 0,
            "revenue_backtest_inserted": 0,
            "item_backtest_inserted": 0,
            "error": str(e),
        }

    revenue = data.get("revenue_forecasts", [])
    item = data.get("item_forecasts", [])
    revenue_bt = data.get("revenue_backtest", [])
    item_bt = data.get("item_backtest", [])

    stats = {"revenue_inserted": 0, "item_inserted": 0, "revenue_backtest_inserted": 0, "item_backtest_inserted": 0}
    do_revenue = scope in ("revenue", "all")
    do_items = scope in ("items", "all")

    try:
        # revenue_forecasts → forecast_cache
        if do_revenue:
            for row in revenue:
                conn.execute(
                    """INSERT OR REPLACE INTO forecast_cache
                       (forecast_date, model_name, generated_on,
                        revenue, orders, pred_std, lower_95, upper_95,
                        temp_max, rain_category, uploaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'bootstrap')""",
                    (
                        row.get("forecast_date"),
                        row.get("model_name"),
                        row.get("generated_on"),
                        row.get("revenue"),
                        row.get("orders", 0),
                        row.get("pred_std"),
                        row.get("lower_95"),
                        row.get("upper_95"),
                        row.get("temp_max"),
                        row.get("rain_category"),
                    ),
                )
                stats["revenue_inserted"] += 1

        # item_forecasts → item_forecast_cache
        if do_items:
            for row in item:
                conn.execute(
                    """INSERT OR REPLACE INTO item_forecast_cache
                       (forecast_date, item_id, generated_on,
                        p50, p90, probability, recommended_prep, uploaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'bootstrap')""",
                    (
                        row.get("forecast_date"),
                        row.get("item_id"),
                        row.get("generated_on"),
                        row.get("p50"),
                        row.get("p90"),
                        row.get("probability"),
                        row.get("recommended_prep"),
                    ),
                )
                stats["item_inserted"] += 1

        # revenue_backtest → revenue_backtest_cache
        if do_revenue:
            for row in revenue_bt:
                conn.execute(
                    """INSERT OR REPLACE INTO revenue_backtest_cache
                       (forecast_date, model_name, model_trained_through,
                        revenue, orders, pred_std, lower_95, upper_95, uploaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'bootstrap')""",
                    (
                        row.get("forecast_date"),
                        row.get("model_name"),
                        row.get("model_trained_through"),
                        row.get("revenue"),
                        row.get("orders", 0),
                        row.get("pred_std"),
                        row.get("lower_95"),
                        row.get("upper_95"),
                    ),
                )
                stats["revenue_backtest_inserted"] += 1

        # item_backtest → item_backtest_cache
        if do_items:
            for row in item_bt:
                conn.execute(
                    """INSERT OR REPLACE INTO item_backtest_cache
                       (forecast_date, item_id, model_trained_through, p50, p90, probability, uploaded_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'bootstrap')""",
                    (
                        row.get("forecast_date"),
                        row.get("item_id"),
                        row.get("model_trained_through"),
                        row.get("p50"),
                        row.get("p90"),
                        row.get("probability"),
                    ),
                )
                stats["item_backtest_inserted"] += 1

        conn.commit()
        if any(stats.values()):
            logger.info(f"Forecast bootstrap seeded: {stats}")
    except Exception as e:
        conn.rollback()
        return {**stats, "error": str(e)}

    return {**stats, "error": None}


def maybe_seed_forecast_bootstrap(conn) -> bool:
    """
    If bootstrap URL is configured and local forecast cache is empty,
    fetch and seed from cloud. Returns True if bootstrap was attempted (success or not).
    """
    from src.core.config.client_learning_config import (
        CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL,
    )
    from src.core.utils.business_date import get_current_business_date

    if not CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL:
        return False

    try:
        from src.core.learning.revenue_forecasting.forecast_cache import ensure_tables_exist

        ensure_tables_exist(conn)
        today = get_current_business_date()
        cur = conn.execute(
            "SELECT COUNT(*) FROM forecast_cache WHERE generated_on = ?",
            (today,),
        )
        if cur.fetchone()[0] > 0:
            return False  # Already have fresh data
        # Also check item cache
        cur = conn.execute(
            "SELECT COUNT(*) FROM item_forecast_cache WHERE generated_on = ?",
            (today,),
        )
        if cur.fetchone()[0] > 0:
            return False

        from src.core.config.cloud_sync_config import get_cloud_sync_config
        _, auth_key = get_cloud_sync_config(conn)

        result = fetch_and_seed_forecast_bootstrap(
            conn,
            CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL,
            auth=auth_key,
        )
        if result.get("error"):
            logger.warning(f"Forecast bootstrap failed: {result['error']}")
        return True
    except Exception as e:
        logger.warning(f"Forecast bootstrap check failed: {e}")
        return False
