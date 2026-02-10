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

from src.api.routers.config import get_db_connection
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
    ITEM_DEMAND_AVAILABLE = True
except (ImportError, OSError) as e:
    logging.getLogger(__name__).warning(f"Item Demand ML module not available: {e}")
    ITEM_DEMAND_AVAILABLE = False
except Exception as e:
    logging.getLogger(__name__).warning(f"Unexpected error importing Item Demand ML module: {e}")
    ITEM_DEMAND_AVAILABLE = False

logger = logging.getLogger(__name__)
router = APIRouter()

# Lock to prevent concurrent item demand training runs.
# NOTE: Only effective for single-worker deployments (same caveat as GP model).
_item_training_lock = threading.Lock()


def get_db():
    conn, _ = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


def _get_item_historical_data(conn, item_id: Optional[str] = None, days: int = 90) -> pd.DataFrame:
    """Fetch item-level sales history with weather data."""
    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
    start_dt = today_date - timedelta(days=days)

    item_filter = "AND mi.menu_item_id = ?" if item_id else ""
    params = [start_dt.isoformat(), today_str]
    if item_id:
        params.append(item_id)

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


# ---------------------------------------------------------------------------
# Background retraining (mirrors GP model pattern in forecast.py)
# ---------------------------------------------------------------------------

def _train_item_demand_task():
    """
    Background task: retrain item demand models on latest data.

    Uses a threading lock to prevent concurrent runs.  Opens its own DB
    connection (independent of any request lifecycle).
    """
    if not _item_training_lock.acquire(blocking=False):
        logger.warning("Item demand training already in progress, skipping.")
        return

    conn = None
    try:
        from src.api.routers.config import get_db_connection
        conn, _ = get_db_connection()
        logger.info("Starting background item demand training...")

        # Fetch 120 days of history (extra buffer for lag features)
        df = _get_item_historical_data(conn, days=120)
        if df.empty:
            logger.warning("No historical data available — skipping item demand training.")
            return

        logger.info(f"Training data: {len(df)} rows, "
                     f"{df['item_id'].nunique()} items, "
                     f"{df['date'].min().date()} to {df['date'].max().date()}")

        # Retrain — skip held-out evaluation for speed (this is a routine refresh)
        save_dir = _resolve_model_dir()
        train_pipeline(df=df, save_path=save_dir, evaluate=False)

        # Clear cached models so the next API request loads the fresh ones
        clear_model_cache()

        logger.info("Background item demand training completed successfully.")

    except Exception as e:
        logger.error(f"Background item demand training failed: {e}", exc_info=True)
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
    try:
        if not ITEM_DEMAND_AVAILABLE:
            raise HTTPException(
                status_code=503,
                detail="Item Demand ML module not available. Train models first."
            )

        # ---- Check if models exist; auto-train if missing but data available ----
        models_loaded = True
        try:
            get_models()
        except FileNotFoundError:
            models_loaded = False

        if not models_loaded:
            # No trained models on disk — can we auto-train?
            training_running = _item_training_lock.locked()

            if not training_running:
                # Quick check: is there enough sales data to train?
                df_check = _get_item_historical_data(conn, days=90)
                if not df_check.empty and len(df_check) >= 30:
                    logger.info(
                        "No item demand models found but sales data exists "
                        f"({len(df_check)} rows) — triggering initial training."
                    )
                    threading.Thread(
                        target=_train_item_demand_task, daemon=True
                    ).start()
                    training_running = True
                else:
                    logger.info(
                        "No item demand models and insufficient sales data "
                        f"({len(df_check) if not df_check.empty else 0} rows) "
                        "— cannot auto-train yet."
                    )

            return {
                "items": [],
                "history": [],
                "forecast": [],
                "model_stale": True,
                "training_in_progress": training_running,
                "message": (
                    "Item demand models are being trained — refresh in ~30 seconds."
                    if training_running
                    else "Not enough sales data to train models yet."
                ),
            }

        # ---- Staleness check: trigger background retrain if needed ----
        model_stale = False
        try:
            model_stale = is_model_stale()
            if model_stale:
                logger.info(
                    "Item demand model is stale — triggering background retrain."
                )
                threading.Thread(
                    target=_train_item_demand_task, daemon=True
                ).start()
        except Exception as e:
            logger.debug(f"Staleness check failed (non-fatal): {e}")

        # Fetch historical data
        df_history = _get_item_historical_data(conn, item_id=item_id, days=90)
        if df_history.empty:
            return {
                "items": [], "history": [], "forecast": [],
                "model_stale": model_stale,
                "training_in_progress": _item_training_lock.locked(),
            }

        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

        # Build future date grid
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

        # Build response
        # History: daily qty per item (last 30 days)
        hist_start = pd.Timestamp(today_date - timedelta(days=30))
        df_hist_recent = df_history[df_history['date'] >= hist_start]

        history_rows = [
            {
                "date": row['date'].strftime('%Y-%m-%d'),
                "item_id": row['item_id'],
                "qty": int(row['quantity_sold']),
            }
            for _, row in df_hist_recent.iterrows()
        ]

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

        items_list = [
            {"item_id": row['item_id'], "item_name": row['item_name']}
            for _, row in items_info.iterrows()
        ]

        return {
            "items": items_list,
            "history": history_rows,
            "forecast": forecast_rows,
            "model_stale": model_stale,
            "training_in_progress": _item_training_lock.locked(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Item forecast error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
