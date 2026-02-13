"""
Item-Level Demand Forecast API Router

Provides endpoints for item-level demand forecasting using global ML models.
Separated from main forecast router per file size standards.

Includes automatic staleness detection: if the model was trained before the
current business date, a background retrain is triggered so that yesterday's
sales are incorporated.  The current (slightly stale) forecast is still
returned immediately — users never see an empty response.
"""
import logging
import threading
from datetime import timedelta, datetime
from typing import Optional

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from src.api.routers import forecast_training_status

from src.api.dependencies import get_db
from src.core.utils.business_date import BUSINESS_DATE_SQL, get_current_business_date

# Safe Import for Item Demand ML
# Catch OSError too — native libs (LightGBM/XGBoost) can fail with OSError if
# shared libraries (e.g. libomp) are missing, which is distinct from ImportError.
try:
    from src.core.learning.revenue_forecasting.item_demand_ml.predict import forecast_items
    from src.core.learning.revenue_forecasting.item_demand_ml.model_io import (
        get_models, clear_model_cache, is_model_stale, _resolve_model_dir,
    )
    from src.core.learning.revenue_forecasting.item_demand_ml.train import train_pipeline
    from src.core.learning.revenue_forecasting.item_demand_ml.backtest_point_in_time import (
        train_and_predict_for_date,
    )
    ITEM_DEMAND_AVAILABLE = True
except (ImportError, OSError) as e:
    logging.getLogger(__name__).warning(f"Item Demand ML module not available: {e}")
    ITEM_DEMAND_AVAILABLE = False
except Exception as e:
    logging.getLogger(__name__).warning(f"Unexpected error importing Item Demand ML module: {e}")
    ITEM_DEMAND_AVAILABLE = False

from src.core.learning.revenue_forecasting.forecast_cache import (
    is_item_cache_fresh,
    get_latest_item_cache_generated_on,
    load_item_forecasts,
    save_item_forecasts,
    get_missing_backtest_dates,
    load_backtest_forecasts,
    save_backtest_forecasts,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Lock to prevent concurrent item demand training runs.
# NOTE: Only effective for single-worker deployments (same caveat as GP model).
_item_training_lock = threading.Lock()


def _fill_item_backtest(
    conn,
    items_info: pd.DataFrame,
    active_items: set,
    today_date,
    n_days: int = 30,
) -> None:
    """
    CPU-heavy: fill item backtest cache for missing dates.
    Called ONLY during training. Checks is_shutting_down() per date.
    """
    backtest_end = today_date - timedelta(days=1)
    backtest_start = backtest_end - timedelta(days=n_days - 1)
    forecast_dates = [
        (backtest_start + timedelta(days=i)).isoformat()
        for i in range(n_days)
    ]
    item_ids = list(active_items)
    missing_dates = get_missing_backtest_dates(conn, forecast_dates, item_ids) if item_ids else []

    for fd in missing_dates:
        if forecast_training_status.is_shutting_down():
            forecast_training_status.log("Shutdown requested — stopping item backtest fill.")
            return
        if not ITEM_DEMAND_AVAILABLE:
            break
        try:
            d = datetime.strptime(fd, "%Y-%m-%d").date()
            start_str = (d - timedelta(days=120)).isoformat()
            end_str = (d - timedelta(days=1)).isoformat()
            df_train = _get_item_historical_data_range(
                conn, start_str, end_str, item_id=None
            )
            if df_train.empty or len(df_train) < 30:
                continue
            result = train_and_predict_for_date(
                df_history=df_train,
                items_info=items_info,
                forecast_date=fd,
            )
            if result.empty:
                continue
            model_through = (d - timedelta(days=1)).isoformat()
            to_save = [
                {
                    "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else str(row["date"])[:10],
                    "item_id": row["item_id"],
                    "p50": float(row["predicted_p50"]),
                    "p90": float(row["predicted_p90"]),
                    "probability": float(row["probability_of_sale"]),
                }
                for _, row in result.iterrows()
            ]
            save_backtest_forecasts(conn, to_save, model_through)
            forecast_training_status.log(f"  Item backtest filled for {fd}")
        except Exception as e:
            logger.warning(f"Point-in-time backtest failed for {fd}: {e}")


def _load_item_backtest(
    conn,
    items_info: pd.DataFrame,
    active_items: set,
    today_date,
    n_days: int = 30,
    item_id: Optional[str] = None,
) -> list:
    """
    Read-only: load item backtest from cache. No fill — fast path for GET handler.
    """
    backtest_end = today_date - timedelta(days=1)
    backtest_start = backtest_end - timedelta(days=n_days - 1)
    forecast_dates = [
        (backtest_start + timedelta(days=i)).isoformat()
        for i in range(n_days)
    ]
    cached = load_backtest_forecasts(conn, forecast_dates)
    name_by_id = dict(zip(items_info["item_id"], items_info["item_name"]))
    backtest_rows = []
    for r in cached:
        if item_id and r["item_id"] != item_id:
            continue
        if r["item_id"] not in active_items:
            continue
        backtest_rows.append({
            "date": r["date"],
            "item_id": r["item_id"],
            "item_name": name_by_id.get(r["item_id"], r["item_id"]),
            "p50": round(float(r["p50"] or 0), 2),
            "p90": round(float(r["p90"] or 0), 2),
            "probability": round(float(r["probability"] or 0), 4),
        })
    return backtest_rows




def _get_item_historical_data_range(
    conn,
    start_date_str: str,
    end_date_str: str,
    item_id: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch item-level sales history for an explicit date range (YYYY-MM-DD)."""
    item_filter = "AND mi.menu_item_id = ?" if item_id else ""
    params = [start_date_str, end_date_str]
    if item_id:
        params.append(item_id)
    return _get_item_historical_data_query(conn, params, item_filter)


def _get_item_historical_data_query(conn, params: list, item_filter: str) -> pd.DataFrame:
    """Shared query logic for item historical data."""
    query = f"""
        SELECT 
            {BUSINESS_DATE_SQL} as date,
            mi.menu_item_id as item_id,
            mi.name as item_name,
            mi.type as category,
            oi.unit_price as price,
            SUM(oi.quantity) as quantity_sold,
            w.temp_max as temperature,
            COALESCE(w.rain_sum, 0) as rain
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        LEFT JOIN weather_daily w ON {BUSINESS_DATE_SQL} = w.date AND w.city = 'Gurugram'
        WHERE o.order_status = 'Success'
          AND {BUSINESS_DATE_SQL} >= ?
          AND {BUSINESS_DATE_SQL} <= ?
          {item_filter}
        GROUP BY {BUSINESS_DATE_SQL}, mi.menu_item_id
        ORDER BY date, item_id
    """
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df['quantity_sold'] = pd.to_numeric(df['quantity_sold'], errors='coerce').fillna(0).astype(int)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0)
    df['temperature'] = pd.to_numeric(df['temperature'], errors='coerce').ffill().fillna(25.0)
    df['rain'] = pd.to_numeric(df['rain'], errors='coerce').fillna(0)
    return df


def _get_item_historical_data(conn, item_id: Optional[str] = None, days: int = 90) -> pd.DataFrame:
    """Fetch item-level sales history with weather data."""
    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
    start_dt = today_date - timedelta(days=days)

    item_filter = "AND mi.menu_item_id = ?" if item_id else ""
    params = [start_dt.isoformat(), today_str]
    if item_id:
        params.append(item_id)
    return _get_item_historical_data_query(conn, params, item_filter)


# ---------------------------------------------------------------------------
# Background retraining (mirrors GP model pattern in forecast.py)
# ---------------------------------------------------------------------------

def _train_item_demand_task():
    """
    Background task: retrain item demand models on latest data,
    then generate predictions for all active items and save to cache.
    """
    if not _item_training_lock.acquire(blocking=False):
        logger.warning("Item demand training already in progress, skipping.")
        return

    conn = None
    try:
        from src.api.routers.config import get_db_connection
        conn, _ = get_db_connection()
        logger.info("Starting background item demand training...")
        forecast_training_status.log("Fetching item history (120 days)…")

        # Fetch 120 days of history (extra buffer for lag features)
        df = _get_item_historical_data(conn, days=120)
        if df.empty:
            logger.warning("No historical data available — skipping item demand training.")
            forecast_training_status.log("No item history data — skipped.")
            return

        logger.info(f"Training data: {len(df)} rows, "
                     f"{df['item_id'].nunique()} items, "
                     f"{df['date'].min().date()} to {df['date'].max().date()}")

        # Retrain — skip held-out evaluation for speed (this is a routine refresh)
        forecast_training_status.log(f"Training item demand model ({df['item_id'].nunique()} items)…")
        save_dir = _resolve_model_dir()
        train_pipeline(df=df, save_path=save_dir, evaluate=False)

        # Clear cached models so we load the fresh ones
        clear_model_cache()

        forecast_training_status.log("Item model trained. Generating predictions…")

        # ── Populate item_forecast_cache ─────────────────────────
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

        # Filter history to complete days only
        df_history = df[df['date'] < pd.Timestamp(today_date)].copy()

        # Active items: sold in last 14 days
        cutoff = today_date - timedelta(days=14)
        active_items = set(
            df_history[df_history['date'] >= pd.Timestamp(cutoff)]['item_id'].unique()
        )
        df_active = df_history[df_history['item_id'].isin(active_items)]

        items_info = df_active.groupby('item_id').agg({
            'item_name': 'first',
            'category': 'first',
            'price': 'first',
        }).reset_index()

        # Cross product: items × future dates (14 days)
        future_dates = pd.date_range(today_date, periods=14, freq='D')
        df_future = items_info.assign(key=1).merge(
            pd.DataFrame({'date': future_dates, 'key': 1}), on='key'
        ).drop('key', axis=1)

        # Weather fallback: recent average
        recent_temp = df_history.groupby('date')['temperature'].first().tail(7).mean()
        recent_rain = df_history.groupby('date')['rain'].first().tail(7).mean()
        df_future['temperature'] = round(recent_temp if pd.notna(recent_temp) else 25.0, 1)
        df_future['rain'] = round(recent_rain if pd.notna(recent_rain) else 0.0, 1)

        forecast_df = forecast_items(
            df_future_dates=df_future,
            df_history=df_history,
        )

        forecast_rows = [
            {
                "date": row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10],
                "item_id": row['item_id'],
                "item_name": row['item_name'],
                "p50": round(float(row['predicted_p50']), 2),
                "p90": round(float(row['predicted_p90']), 2),
                "probability": round(float(row['probability_of_sale']), 4),
                "recommended_prep": int(row['recommended_prep']) if 'recommended_prep' in row.index else 0,
            }
            for _, row in forecast_df.iterrows()
        ]

        save_item_forecasts(conn, forecast_rows, today_str)
        forecast_training_status.log(f"Cached {len(forecast_rows)} item forecast rows.")

        # ── Fill item backtest cache ─────────────────────────────
        forecast_training_status.log("Filling item backtest cache (30 days)…")
        _fill_item_backtest(conn, items_info, active_items, today_date, n_days=30)

        logger.info("Background item demand training + cache population completed.")

    except Exception as e:
        logger.error(f"Background item demand training failed: {e}", exc_info=True)
        forecast_training_status.log(f"ERROR in item demand training: {e}")
    finally:
        if conn:
            conn.close()
        _item_training_lock.release()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/train-items")
def trigger_item_demand_training(background_tasks: BackgroundTasks):
    """
    Manually trigger item demand model retraining in the background.

    Returns immediately; training runs asynchronously.
    """
    if not ITEM_DEMAND_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail="Item Demand ML module not available.",
        )
    background_tasks.add_task(_train_item_demand_task)
    return {"message": "Item demand training started in background"}


@router.get("/items")
def get_item_forecast(item_id: Optional[str] = None, days: int = 14, conn=Depends(get_db)):
    """
    Item-level demand forecast endpoint.

    Query params:
        item_id: Optional specific item to forecast. If omitted, returns all items.
        days: Number of future days to forecast (default 14).

    Returns:
        {
            items: [{item_id, item_name}],
            history: [{date, item_id, qty}],
            forecast: [{date, item_id, item_name, p50, p90, probability, recommended_prep}],
            model_stale: bool,
            training_in_progress: bool,
            message: str (only when no models exist yet)
        }

    Behaviour:
        - No models on disk + data in DB → auto-triggers initial training, returns
          empty forecast with training_in_progress=True.
        - No models + no data → returns empty forecast with helpful message.
        - Stale model → triggers background retrain, returns current (stale) forecast.
        - Fresh model → returns forecast normally.
    """
    # 503 during training — frontend shows overlay instead of fetching data
    if forecast_training_status.is_training():
        return JSONResponse(
            status_code=503,
            content={"detail": "Training in progress", "training_status": forecast_training_status.get_status()}
        )

    try:
        if not ITEM_DEMAND_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail="Item Demand ML module not available. Train models first."
            )

        # ---- Models check (needed only for compute; cache can serve without models) ----
        models_loaded = True
        try:
            get_models()
        except FileNotFoundError:
            models_loaded = False

        # ---- Staleness check — NO auto-trigger; manual Full Retrain only ----
        model_stale = False
        try:
            model_stale = is_model_stale()
            if model_stale:
                logger.info("Item demand model is stale. Use Full Retrain in Configuration to refresh.")
        except Exception as e:
            logger.debug(f"Staleness check failed (non-fatal): {e}")

        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

        # Resolve cache generated_on: today if fresh, else latest (handles Pull from Cloud with different date)
        cache_generated_on = today_str if is_item_cache_fresh(conn, today_str) else get_latest_item_cache_generated_on(conn)

        # Fetch historical data (strictly BEFORE today to avoid partial day overlap)
        df_history_all = _get_item_historical_data(conn, item_id=item_id, days=90)
        df_history = df_history_all[df_history_all['date'] < pd.Timestamp(today_date)].copy()

        # If we have cache (from Pull from Cloud), serve it even without models
        if cache_generated_on and not df_history.empty:
            # Build items_info from history and serve from cache
            cutoff = today_date - timedelta(days=14)
            active_items = set(
                df_history[df_history['date'] >= pd.Timestamp(cutoff)]['item_id'].unique()
            )
            df_active = df_history[df_history['item_id'].isin(active_items)]
            items_info = df_active.groupby('item_id').agg({
                'item_name': 'first',
                'category': 'first',
                'price': 'first',
            }).reset_index()
            items_list = [{"item_id": row['item_id'], "item_name": row['item_name']} for _, row in items_info.iterrows()]
            hist_start = pd.Timestamp(today_date - timedelta(days=30))
            df_hist_recent = df_history[df_history['date'] >= hist_start]
            hist_grid_dates = pd.date_range(hist_start, today_date - timedelta(days=1), freq='D')
            if not hist_grid_dates.empty and not items_info.empty:
                hist_bloat = items_info[['item_id', 'item_name']].assign(key=1).merge(
                    pd.DataFrame({'date': hist_grid_dates, 'key': 1}), on='key'
                ).drop('key', axis=1)
                df_hist_final = hist_bloat.merge(
                    df_hist_recent[['date', 'item_id', 'quantity_sold']],
                    on=['date', 'item_id'],
                    how='left'
                )
                df_hist_final['quantity_sold'] = df_hist_final['quantity_sold'].fillna(0).astype(int)
            else:
                df_hist_final = pd.DataFrame(columns=['date', 'item_id', 'item_name', 'quantity_sold'])
            history_rows = [{"date": row['date'].strftime('%Y-%m-%d'), "item_id": row['item_id'], "qty": int(row['quantity_sold'])} for _, row in df_hist_final.iterrows()]
            backtest_rows = _load_item_backtest(conn, items_info, active_items, today_date, n_days=30, item_id=item_id)
            cached = load_item_forecasts(conn, cache_generated_on)
            name_by_id = dict(zip(items_info['item_id'], items_info['item_name']))
            filtered = [r for r in cached if r['item_id'] in active_items and (item_id is None or r['item_id'] == item_id) and r['date'] >= today_str]
            forecast_rows = [
                {"date": r['date'], "item_id": r['item_id'], "item_name": name_by_id.get(r['item_id'], r['item_id']),
                 "p50": round(float(r['p50'] or 0), 2), "p90": round(float(r['p90'] or 0), 2),
                 "probability": round(float(r['probability'] or 0), 4), "recommended_prep": int(r['recommended_prep'] or 0)}
                for r in filtered
            ]
            forecast_rows.sort(key=lambda x: x['date'])
            distinct_dates = sorted({r['date'] for r in forecast_rows})
            limit_dates = set(distinct_dates[:days])
            forecast_rows = [r for r in forecast_rows if r['date'] in limit_dates]
            return {
                "items": items_list,
                "history": history_rows,
                "forecast": forecast_rows,
                "backtest": backtest_rows,
                "model_stale": model_stale,
                "training_in_progress": _item_training_lock.locked(),
            }

        # No cache and no history: return empty (or awaiting_action if no models)
        if df_history.empty:
            if not models_loaded:
                from src.core.forecast_bootstrap import get_bootstrap_endpoint
                cloud_configured = bool(get_bootstrap_endpoint(conn))
                message = "Forecast cache is empty. Use Pull from Cloud or Full Retrain to populate forecasts."
                if not cloud_configured:
                    message = "Forecast cache is empty. Configure Cloud Server URL in Configuration to use Pull from Cloud, or use Full Retrain."
                return {
                    "items": [], "history": [], "forecast": [], "backtest": [],
                    "model_stale": True, "training_in_progress": False,
                    "awaiting_action": True, "cloud_not_configured": not cloud_configured,
                    "message": message,
                }
            return {
                "items": [], "history": [], "forecast": [], "backtest": [],
                "model_stale": model_stale,
                "training_in_progress": _item_training_lock.locked(),
            }

        # Have history but no cache — need models to compute
        if not models_loaded:
            from src.core.forecast_bootstrap import get_bootstrap_endpoint
            cloud_configured = bool(get_bootstrap_endpoint(conn))
            message = "Forecast cache is empty. Use Pull from Cloud or Full Retrain to populate forecasts."
            if not cloud_configured:
                message = "Forecast cache is empty. Configure Cloud Server URL in Configuration to use Pull from Cloud, or use Full Retrain."
            logger.info("No item demand models and no cache. Use Full Retrain or Pull from Cloud.")
            return {
                "items": [],
                "history": [],
                "forecast": [],
                "backtest": [],
                "model_stale": True,
                "training_in_progress": False,
                "awaiting_action": True,
                "cloud_not_configured": not cloud_configured,
                "message": message,
            }

        # Build future date grid STARTING TODAY
        future_dates = pd.date_range(today_date, periods=days, freq='D')

        # Get unique items from history — only items active in last 14 days
        cutoff = today_date - timedelta(days=14)
        active_items = set(
            df_history[df_history['date'] >= pd.Timestamp(cutoff)]['item_id'].unique()
        )
        df_active = df_history[df_history['item_id'].isin(active_items)]

        items_info = df_active.groupby('item_id').agg({
            'item_name': 'first',
            'category': 'first',
            'price': 'first',
        }).reset_index()

        items_list = [
            {"item_id": row['item_id'], "item_name": row['item_name']}
            for _, row in items_info.iterrows()
        ]

        # History: daily qty per item (last 30 days)
        # CRITICAL: Densify history to include zero-sales days for the chart
        hist_start = pd.Timestamp(today_date - timedelta(days=30))
        df_hist_recent = df_history[df_history['date'] >= hist_start]
        
        # unique_hist_dates = pd.date_range(hist_start, today_date - timedelta(days=1), freq='D')
        # We need a cross product of (all active items) x (last 30 days)
        # to ensure zeros are present.
        
        # 1. Create grid of (date, item_id)
        hist_grid_dates = pd.date_range(hist_start, today_date - timedelta(days=1), freq='D')
        if not hist_grid_dates.empty and not items_info.empty:
            hist_bloat = items_info[['item_id', 'item_name']].assign(key=1).merge(
                pd.DataFrame({'date': hist_grid_dates, 'key': 1}), on='key'
            ).drop('key', axis=1)
            
            # 2. Merge with actuals
            df_hist_final = hist_bloat.merge(
                df_hist_recent[['date', 'item_id', 'quantity_sold']],
                on=['date', 'item_id'],
                how='left'
            )
            df_hist_final['quantity_sold'] = df_hist_final['quantity_sold'].fillna(0).astype(int)
        else:
            df_hist_final = pd.DataFrame(columns=['date', 'item_id', 'item_name', 'quantity_sold'])

        history_rows = [
            {
                "date": row['date'].strftime('%Y-%m-%d'),
                "item_id": row['item_id'],
                "qty": int(row['quantity_sold']),
            }
            for _, row in df_hist_final.iterrows()
        ]

        # ---- Backtest: point-in-time T→T+1 (always; fills cache for missing dates) ----
        backtest_rows = _load_item_backtest(
            conn, items_info, active_items, today_date, n_days=30, item_id=item_id
        )

        # ---- Cache-first: load forecast from DB when fresh ----
        if is_item_cache_fresh(conn, today_str):
            try:
                cached = load_item_forecasts(conn, today_str)
                name_by_id = dict(zip(items_info['item_id'], items_info['item_name']))
                filtered = [
                    r for r in cached
                    if r['item_id'] in active_items
                    and (item_id is None or r['item_id'] == item_id)
                    and r['date'] >= today_str
                ]
                forecast_rows = [
                    {
                        "date": r['date'],
                        "item_id": r['item_id'],
                        "item_name": name_by_id.get(r['item_id'], r['item_id']),
                        "p50": round(float(r['p50'] or 0), 2),
                        "p90": round(float(r['p90'] or 0), 2),
                        "probability": round(float(r['probability'] or 0), 4),
                        "recommended_prep": int(r['recommended_prep'] or 0),
                    }
                    for r in filtered
                ]
                forecast_rows.sort(key=lambda x: x['date'])
                distinct_dates = sorted({r['date'] for r in forecast_rows})
                limit_dates = set(distinct_dates[:days])
                forecast_rows = [r for r in forecast_rows if r['date'] in limit_dates]
                logger.debug(f"Item forecast served from cache ({len(forecast_rows)} forecast, {len(backtest_rows)} backtest)")
                return {
                    "items": items_list,
                    "history": history_rows,
                    "forecast": forecast_rows,
                    "backtest": backtest_rows,
                    "model_stale": model_stale,
                    "training_in_progress": _item_training_lock.locked(),
                }
            except Exception as e:
                logger.warning(f"Item cache load failed, falling back to compute: {e}")

        # ---- Cache miss: compute forecast + backtest, then save ----
        # Cross product: items × future dates
        df_future = items_info.assign(key=1).merge(
            pd.DataFrame({'date': future_dates, 'key': 1}), on='key'
        ).drop('key', axis=1)

        # Weather fallback: use recent average
        recent_temp = df_history.groupby('date')['temperature'].first().tail(7).mean()
        recent_rain = df_history.groupby('date')['rain'].first().tail(7).mean()
        df_future['temperature'] = round(recent_temp if pd.notna(recent_temp) else 25.0, 1)
        df_future['rain'] = round(recent_rain if pd.notna(recent_rain) else 0.0, 1)

        # Generate forecast — use full 90-day history for lag features
        forecast_df = forecast_items(
            df_future_dates=df_future,
            df_history=df_history,
        )

        forecast_rows = [
            {
                "date": row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10],
                "item_id": row['item_id'],
                "item_name": row['item_name'],
                "p50": round(float(row['predicted_p50']), 2),
                "p90": round(float(row['predicted_p90']), 2),
                "probability": round(float(row['probability_of_sale']), 4),
                "recommended_prep": int(row['recommended_prep']) if 'recommended_prep' in row.index else 0,
            }
            for _, row in forecast_df.iterrows()
        ]

        # ---- Save forecast to item cache (backtest is in item_backtest_cache) ----
        try:
            save_item_forecasts(conn, forecast_rows, today_str)
        except Exception as e:
            logger.warning(f"Failed to save item forecasts to cache: {e}")

        return {
            "items": items_list,
            "history": history_rows,
            "forecast": forecast_rows,
            "backtest": backtest_rows,
            "model_stale": model_stale,
            "training_in_progress": _item_training_lock.locked(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Item forecast error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
