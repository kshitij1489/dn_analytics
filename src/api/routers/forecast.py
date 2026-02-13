"""
Sales Forecast API Router
Supports multiple forecasting algorithms: Weekday Average, Holt-Winters, Prophet, Gaussian Process.
"""
import logging
import threading
import json

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from datetime import timedelta, datetime
from typing import List, Dict, Optional

from src.api.routers import forecast_training_status

import pandas as pd

from src.api.dependencies import get_db
from src.core.services.weather_service import WeatherService
from src.core.utils.business_date import BUSINESS_DATE_SQL, get_current_business_date, get_last_complete_business_date
from src.core.utils.weather_helpers import get_rain_cat
from src.core.learning.revenue_forecasting.weekday import forecast_weekday_avg
from src.core.learning.revenue_forecasting.holtwinters import forecast_holt_winters
from src.core.learning.revenue_forecasting.prophet_model import forecast_prophet
from src.core.learning.revenue_forecasting.forecast_cache import (
    is_revenue_cache_fresh,
    load_revenue_forecasts,
    save_revenue_forecasts,
    get_missing_revenue_backtest_dates,
    load_revenue_backtest_forecasts,
    save_revenue_backtest_forecasts,
)

logger = logging.getLogger(__name__)

# Safe Import for Gaussian Process
try:
    from src.core.learning.revenue_forecasting.gaussianprocess import RollingGPForecaster
    GP_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import Gaussian Process module: {e}")
    GP_AVAILABLE = False
except Exception as e:
    logger.error(f"Unexpected error importing Gaussian Process module: {e}")
    GP_AVAILABLE = False

router = APIRouter()

# Lock to prevent concurrent training runs.
# NOTE: Only effective for single-worker deployments. For multi-worker (uvicorn --workers > 1),
# use a file lock (e.g., filelock) or external coordinator to prevent model file corruption.
_training_lock = threading.Lock()


@router.get("/training-status")
def get_training_status():
    """Returns current training status for the frontend overlay."""
    return forecast_training_status.get_status()


def _load_and_check_stale(gp: RollingGPForecaster) -> bool:
    """
    Load the GP model and check if it needs retraining.
    
    Uses the 5 AM business-day boundary:
    - Before 5 AM IST: never stale (business day hasn't ended yet)
    - After 5 AM: stale if model's training window doesn't include yesterday
    
    Side effect: Loads the model into `gp` if a persisted model exists.
    
    Returns True if:
    - No model file exists
    - Current time >= 5 AM and model's window_end < last complete business date
    """
    if not gp.load():
        return True  # No model exists

    # Guard: don't trigger retraining before business day starts.
    # Before 5 AM the previous day is still in progress.
    try:
        from src.core.utils.business_date import BUSINESS_DAY_START_HOUR, IST
        now_ist = datetime.now(IST)
        if now_ist.hour < BUSINESS_DAY_START_HOUR:
            return False  # Too early — previous business day not finalized
    except Exception:
        pass  # If timezone import fails, fall through to normal check
    
    expected_end_str = get_last_complete_business_date()
    expected_end = pd.Timestamp(expected_end_str)
    
    if gp.window_end is None or gp.window_end < expected_end:
        logger.info(f"Model stale: window_end={gp.window_end}, expected={expected_end}")
        return True
    
    return False



def sync_weather_task(city: str = "Gurugram"):
    try:
        service = WeatherService()
        service.sync_weather_data(city)
    except Exception as e:
        logger.warning(f"Background weather sync failed: {e}")

def get_historical_data(conn, days: int = 90, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """Fetch historical sales and weather data as a DataFrame."""
    # Use business date
    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
    
    if start_date and end_date:
        # dayfirst=False silences warning for ISO YYYY-MM-DD; flexible for other formats
        start_dt = pd.to_datetime(start_date, dayfirst=False).date()
        end_dt = pd.to_datetime(end_date, dayfirst=False).date()
    else:
        end_dt = today_date + timedelta(days=1)
        start_dt = end_dt - timedelta(days=days + 1)
    
    # Left join orders with weather_daily
    # We aggregate orders first, then join weather
    query = f"""
        SELECT 
            d.day as ds,
            COALESCE(sales.revenue, 0) as y,
            COALESCE(sales.orders_count, 0) as orders,
            w.temp_max,
            w.rain_sum,
            w.weather_code,
            w.forecast_snapshot
        FROM (
            -- Calendar Helper (naive recursive CTE for sqlite to ensure all dates)
            WITH RECURSIVE dates(day) AS (
                VALUES(DATE(?))
                UNION ALL
                SELECT DATE(day, '+1 day')
                FROM dates
                WHERE day < DATE(?)
            )
            SELECT day FROM dates
        ) d
        LEFT JOIN (
            SELECT 
                {BUSINESS_DATE_SQL} as sale_date,
                SUM(total) as revenue,
                COUNT(*) as orders_count
            FROM orders
            WHERE order_status = 'Success'
            GROUP BY 1
        ) sales ON d.day = sales.sale_date
        LEFT JOIN weather_daily w ON d.day = w.date AND w.city = 'Gurugram'
        ORDER BY d.day ASC
    """
    cursor = conn.execute(query, (start_dt.isoformat(), end_dt.isoformat()))
    rows = [dict(row) for row in cursor.fetchall()]
    
    if not rows:
        return pd.DataFrame(columns=['ds', 'y', 'orders', 'temp_max', 'rain_sum', 'weather_code'])
    
    df = pd.DataFrame(rows)
    df['ds'] = pd.to_datetime(df['ds'])
    df['y'] = pd.to_numeric(df['y'], errors='coerce').fillna(0)
    df['orders'] = pd.to_numeric(df['orders'], errors='coerce').fillna(0)
    
    # Fill missing weather with 0 or mean (for prophet regressors we need non-null)
    df['temp_max'] = pd.to_numeric(df['temp_max'], errors='coerce').ffill().fillna(25.0) 
    df['rain_sum'] = pd.to_numeric(df['rain_sum'], errors='coerce').fillna(0)
    
    return df


@router.post("/train-gp")
def trigger_gp_training(background_tasks: BackgroundTasks, conn=Depends(get_db)):
    """Manually trigger daily rolling training for GP model."""
    background_tasks.add_task(train_gp_task)
    return {"message": "GP training started in background"}


@router.post("/pull-from-cloud")
def pull_from_cloud(scope: str = "all", conn=Depends(get_db)):
    """
    Manually pull forecast cache from cloud bootstrap endpoint.
    scope: "revenue" | "items" | "volume" | "all" — only pull and seed the specified cache(s).
    """
    from src.core.forecast_bootstrap import get_bootstrap_endpoint, fetch_and_seed_forecast_bootstrap
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    if scope not in ("revenue", "items", "volume", "all"):
        raise HTTPException(status_code=400, detail="scope must be 'revenue', 'items', 'volume', or 'all'")
    endpoint = get_bootstrap_endpoint(conn)
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail="Cloud sync URL not configured. Set cloud_sync_url in Configuration.",
        )
    _, auth_key = get_cloud_sync_config(conn)
    result = fetch_and_seed_forecast_bootstrap(conn, endpoint, auth=auth_key, scope=scope)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"Bootstrap failed: {result['error']}")
    return {
        "message": "Forecast data pulled from cloud",
        "revenue_inserted": result.get("revenue_inserted", 0),
        "item_inserted": result.get("item_inserted", 0),
        "volume_inserted": result.get("volume_inserted", 0),
        "revenue_backtest_inserted": result.get("revenue_backtest_inserted", 0),
        "item_backtest_inserted": result.get("item_backtest_inserted", 0),
        "volume_backtest_inserted": result.get("volume_backtest_inserted", 0),
    }


@router.post("/full-retrain")
def full_retrain(background_tasks: BackgroundTasks, scope: str = "all"):
    """
    Manually trigger retrain. scope: "revenue" | "items" | "volume" | "all".
    Runs in background **sequentially** (GP → Items → Volume).
    Returns 409 if training is already in progress.
    """
    if scope not in ("revenue", "items", "volume", "all"):
        raise HTTPException(status_code=400, detail="scope must be 'revenue', 'items', 'volume', or 'all'")

    # Reject duplicate retrain requests
    if forecast_training_status.is_training():
        raise HTTPException(status_code=409, detail="Training already in progress")

    background_tasks.add_task(_full_retrain_task, scope)
    return {"message": f"Full retrain started (scope={scope}). Monitor progress via the training overlay."}


def _full_retrain_task(scope: str):
    """
    Sequential wrapper: runs applicable training tasks one at a time,
    updating forecast_training_status at each phase.
    """
    try:
        from src.api.routers.forecast_items import _train_item_demand_task
    except ImportError:
        _train_item_demand_task = None
    try:
        from src.api.routers.forecast_volume import _train_volume_task
    except ImportError:
        _train_volume_task = None

    forecast_training_status.start("revenue", "Starting sales model training…")
    try:
        if scope in ("revenue", "all"):
            forecast_training_status.update(5, "Training revenue models (GP + Prophet + HW + WeekdayAvg)…")
            train_gp_task()

        if forecast_training_status.is_shutting_down():
            forecast_training_status.log("Shutdown requested — aborting retrain.")
            return

        if scope in ("items", "all") and _train_item_demand_task:
            forecast_training_status.update(33, "Training item demand model…")
            _train_item_demand_task()

        if forecast_training_status.is_shutting_down():
            forecast_training_status.log("Shutdown requested — aborting retrain.")
            return

        if scope in ("volume", "all") and _train_volume_task:
            forecast_training_status.update(66, "Training volume model…")
            _train_volume_task()

        forecast_training_status.update(100, "All training complete.")
    except Exception as e:
        logger.error(f"Full retrain task failed: {e}", exc_info=True)
        forecast_training_status.log(f"ERROR: {e}")
    finally:
        forecast_training_status.finish()

def train_gp_task():
    """
    Background task: trains GP model, then runs all 4 revenue forecast models
    and saves results to forecast_cache so the GET handler serves from cache.
    """
    # Idempotency: prevent concurrent training runs
    if not _training_lock.acquire(blocking=False):
        logger.warning("GP training already in progress, skipping duplicate request.")
        return

    conn = None
    try:
        from src.api.routers.config import get_db_connection
        conn, _ = get_db_connection()
        logger.info("Starting Daily GP Training...")
        forecast_training_status.log("Fetching historical data (120 days)…")

        # Fetch enough data for lag features + 90 days window
        df = get_historical_data(conn, days=120)

        # CRITICAL: Filter to only include complete business days
        last_complete_str = get_last_complete_business_date()
        last_complete_date = pd.Timestamp(last_complete_str)
        df = df[df['ds'] <= last_complete_date].copy()

        logger.info(f"Training data: {len(df)} rows, ending at {df['ds'].max()}")

        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

        # Build future weather (shared by GP + prophet)
        future_dates = [today_date + timedelta(days=i) for i in range(7)]
        future_weather = pd.DataFrame({'ds': pd.to_datetime(future_dates)})
        weather_temps = _fetch_forecast_weather(conn, today_str, future_dates)
        future_weather['temp_max'] = weather_temps

        # ── 1. Train & predict GP ───────────────────────────────
        gp_results = []
        if GP_AVAILABLE:
            forecast_training_status.log("Training Gaussian Process model…")
            gp = RollingGPForecaster()
            gp.update_and_fit(df)

            forecast_training_status.log("Generating GP predictions…")
            gp_results = forecast_gp(future_weather)
        else:
            logger.warning("GP module unavailable — skipping GP forecast.")
            forecast_training_status.log("GP module unavailable — skipped.")

        # ── 2. Weekday Average ──────────────────────────────────
        forecast_training_status.log("Computing Weekday Average forecast…")
        wa_results = forecast_weekday_avg(df, periods=7)

        # ── 3. Holt-Winters ────────────────────────────────────
        forecast_training_status.log("Computing Holt-Winters forecast…")
        hw_out = forecast_holt_winters(df, periods=7)
        hw_results = hw_out.get("data", [])
        if hw_out.get("error"):
            forecast_training_status.log(f"Holt-Winters warning: {hw_out['error']}")

        # ── 4. Prophet ─────────────────────────────────────────
        forecast_training_status.log("Computing Prophet forecast…")
        pr_out = forecast_prophet(df, periods=7)
        pr_results = pr_out.get("data", [])
        if pr_out.get("error"):
            forecast_training_status.log(f"Prophet warning: {pr_out['error']}")

        # ── Save all 4 models to forecast_cache ────────────────
        forecast_training_status.log("Saving forecasts to cache…")
        for model_name, rows in [
            ("weekday_avg", wa_results),
            ("holt_winters", hw_results),
            ("prophet", pr_results),
            ("gp", gp_results),
        ]:
            if rows:
                save_revenue_forecasts(conn, model_name, rows, today_str)
                forecast_training_status.log(f"  Cached {len(rows)} {model_name} rows.")

        # ── Fill revenue backtest cache (CPU-heavy, done during training) ──
        forecast_training_status.log("Filling revenue backtest cache (30 days)…")
        _fill_revenue_backtest(conn, today_date, MODEL_NAMES)

        logger.info("Revenue model training & cache population complete.")
        forecast_training_status.log("Revenue training complete.")

    except Exception as e:
        logger.error(f"GP Training failed: {e}", exc_info=True)
        forecast_training_status.log(f"ERROR in revenue training: {e}")
    finally:
        if conn:
            conn.close()
        _training_lock.release()


MODEL_NAMES = ["weekday_avg", "holt_winters", "prophet", "gp"]


def _merge_backtest_and_forecast(
    backtest: Dict[str, List[dict]],
    forecast: Dict[str, List[dict]],
    today_str: str,
) -> Dict[str, List[dict]]:
    """
    Merge point-in-time backtest (dates < today) with forward forecast (dates >= today).
    Backtest rows get temp_max=0, rain_category='none' if missing for UI compatibility.
    """
    merged = {}
    for m in MODEL_NAMES:
        bt = backtest.get(m, [])
        fc = forecast.get(m, [])
        # Normalize backtest rows (add temp_max, rain_category if missing)
        for r in bt:
            r.setdefault("temp_max", 0)
            r.setdefault("rain_category", "none")
        bt_past = [r for r in bt if r["date"] < today_str]
        fc_future = [r for r in fc if r["date"] >= today_str]
        combined = bt_past + fc_future
        combined.sort(key=lambda x: x["date"])
        merged[m] = combined
    return merged


def _fill_revenue_backtest(conn, today_date, model_names: List[str]) -> None:
    """
    CPU-heavy: fill revenue backtest cache for missing dates.
    Called ONLY during training. Checks is_shutting_down() per date.
    """
    backtest_end = today_date - timedelta(days=1)
    backtest_start = backtest_end - timedelta(days=29)
    forecast_dates = [(backtest_start + timedelta(days=i)).isoformat() for i in range(30)]
    missing = get_missing_revenue_backtest_dates(conn, forecast_dates, model_names)

    for fd in missing:
        if forecast_training_status.is_shutting_down():
            forecast_training_status.log("Shutdown requested — stopping revenue backtest fill.")
            return
        try:
            d = datetime.strptime(fd, "%Y-%m-%d").date()
            start_str = (d - timedelta(days=120)).isoformat()
            end_str = (d - timedelta(days=1)).isoformat()
            df = get_historical_data(conn, start_date=start_str, end_date=end_str)
            if df.empty or len(df) < 14:
                continue
            from src.core.learning.revenue_forecasting.backtest_point_in_time import run_backtest_for_date
            results = run_backtest_for_date(df, fd, model_names, conn=conn)
            model_through = (d - timedelta(days=1)).isoformat()
            for m, rows in results.items():
                if rows:
                    save_revenue_backtest_forecasts(conn, m, rows, model_through)
            forecast_training_status.log(f"  Backtest filled for {fd}")
        except Exception as e:
            logger.warning(f"Revenue backtest failed for {fd}: {e}")


def _load_revenue_backtest(conn, today_date, model_names: List[str]) -> Dict[str, List[dict]]:
    """
    Read-only: load revenue backtest from cache. No fill — fast path for GET handler.
    """
    backtest_end = today_date - timedelta(days=1)
    backtest_start = backtest_end - timedelta(days=29)
    forecast_dates = [(backtest_start + timedelta(days=i)).isoformat() for i in range(30)]
    cached = load_revenue_backtest_forecasts(conn, forecast_dates, model_names)
    out = {m: cached.get(m, []) for m in model_names}
    return out


def _fetch_forecast_weather(conn, today_str: str, future_dates: list) -> list:
    """Fetch forecast temperatures from weather_daily.forecast_snapshot or API fallback."""
    temps = [25.0] * len(future_dates)  # Default fallback
    
    try:
        # Query today's forecast snapshot from weather_daily
        cursor = conn.execute("""
            SELECT forecast_snapshot FROM weather_daily 
            WHERE date = ? AND city = 'Gurugram'
        """, (today_str,))
        row = cursor.fetchone()
        
        if row and row[0]:
            snapshot = json.loads(row[0])
            times = snapshot.get('time', [])
            snapshot_temps = snapshot.get('temperature_2m_max', [])
            
            # Build date->temp lookup
            temp_lookup = {t: snapshot_temps[i] for i, t in enumerate(times) if i < len(snapshot_temps)}
            
            loaded_count = 0
            for i, dt in enumerate(future_dates):
                date_key = dt.strftime('%Y-%m-%d')
                if date_key in temp_lookup:
                    temps[i] = temp_lookup[date_key]
                    loaded_count += 1
                    
            logger.info(f"Loaded {loaded_count} forecast temps from DB snapshot.")
        else:
            logger.warning(f"No weather snapshot found for {today_str}, using default temps.")
            
    except Exception as e:
        logger.warning(f"Failed to fetch forecast weather: {e}, using defaults.")
        
    return temps

@router.get("/replay")
def get_forecast_replay(run_date: str, conn=Depends(get_db)):
    """
    Returns the cached forecasts generated on 'run_date' (from forecast_cache)
    AND the actual revenue that occurred on those target dates.
    """
    # Validate run_date format
    try:
        datetime.strptime(run_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: '{run_date}'. Expected YYYY-MM-DD.")
    
    try:
        # 1. Fetch from forecast_cache (GP model, keyed by generated_on = run_date)
        cursor = conn.execute("""
            SELECT forecast_date, revenue, pred_std, lower_95, upper_95
            FROM forecast_cache
            WHERE generated_on = ? AND model_name = 'gp'
            ORDER BY forecast_date ASC
        """, (run_date,))
        
        forecast_rows = []
        target_dates = []
        
        for row in cursor.fetchall():
            target_dates.append(row[0])  # string 'YYYY-MM-DD'
            forecast_rows.append({
                "date": row[0],
                "pred_mean": row[1],
                "pred_std": row[2],
                "lower_95": row[3],
                "upper_95": row[4]
            })
            
        # 2. Fetch Actuals for those dates
        actuals_map = {}
        if target_dates:
            placeholders = ','.join(['?'] * len(target_dates))
            q_actuals = f"""
                SELECT 
                    {BUSINESS_DATE_SQL} as sale_date,
                    SUM(total) as revenue
                FROM orders
                WHERE order_status = 'Success'
                AND {BUSINESS_DATE_SQL} IN ({placeholders})
                GROUP BY 1
            """
            cur_act = conn.execute(q_actuals, target_dates)
            for row in cur_act.fetchall():
                actuals_map[row[0]] = float(row[1])
                
        # 3. Combine
        results = []
        for item in forecast_rows:
            dt = item['date']
            results.append({
                "date": dt,
                "pred_mean": item['pred_mean'],
                "pred_std": item['pred_std'],
                "lower_95": item['lower_95'],
                "upper_95": item['upper_95'],
                "actual_revenue": actuals_map.get(dt, None)
            })
            
        return {
            "data": results,
            "model_window": None  # no longer tracked per-snapshot
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Replay endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def forecast_gp(future_weather: pd.DataFrame) -> List[Dict]:
    """
    Generates GP forecast using the persistent trained model.
    
    Returns BOTH historical fitted values (last 30 days) AND future predictions,
    matching the pattern of weekday_avg / holt_winters / prophet.
    
    If model is stale, triggers background training and returns empty result.
    GP data will be available on next page load/refresh.
    """
    try:
        if not GP_AVAILABLE:
            logger.warning("Gaussian Process module not available. Skipping forecast.")
            return []

        gp = RollingGPForecaster()
        
        # Check if model is stale or missing — NO auto-trigger; manual Full Retrain only
        if _load_and_check_stale(gp):
            logger.info("GP model is stale or missing. Use Full Retrain or Pull from Cloud in Configuration.")
            return []  # GP unavailable until user triggers manual action
        
        results = []
        
        # 1. Historical fitted values (last 30 days) — in-sample predictions
        hist_df = gp.predict_historical(n_days=30)
        if not hist_df.empty:
            for _, row in hist_df.iterrows():
                results.append({
                    "date": row['ds'].strftime('%Y-%m-%d') if hasattr(row['ds'], 'strftime') else str(row['ds'])[:10],
                    "revenue": max(0, float(row['pred_mean'])),
                    "orders": 0,
                    "gp_lower": max(0, float(row['lower'])),
                    "gp_upper": max(0, float(row['upper']))
                })
        
        # 2. Future predictions (7 days ahead)
        forecast_df = gp.predict_next_days(future_weather)
        for _, row in forecast_df.iterrows():
            results.append({
                "date": row['ds'].strftime('%Y-%m-%d'),
                "revenue": max(0, float(row['pred_mean'])),
                "orders": 0,
                "gp_lower": max(0, float(row['lower'])),
                "gp_upper": max(0, float(row['upper']))
            })
            
        return results
    except Exception as e:
        logger.warning(f"GP Forecast error: {e}")
        return []

@router.get("/")
def get_sales_forecast(background_tasks: BackgroundTasks, conn=Depends(get_db)):
    # 503 during training — frontend shows overlay instead of fetching data
    if forecast_training_status.is_training():
        return JSONResponse(
            status_code=503,
            content={"detail": "Training in progress", "training_status": forecast_training_status.get_status()}
        )
    try:
        # Trigger background weather sync
        background_tasks.add_task(sync_weather_task)

        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

        # ---- Check cache first (fast path) ----
        # ---- Check cache first (fast path) ----
        target_date = today_str
        using_fallback = False
        cached = {}
        
        # 1. Try loading today's cache
        if is_revenue_cache_fresh(conn, today_str):
            cached = load_revenue_forecasts(conn, today_str)

        # 2. Check if today's cache is complete (has all 4 models + GP)
        has_gp = cached.get("gp") and len(cached.get("gp", [])) > 0
        is_complete = cached and len(cached) >= 4 and has_gp
        
        # 3. If missing or incomplete, try fallback to latest available
        if not is_complete:
            from src.core.learning.revenue_forecasting.forecast_cache import (
                get_latest_revenue_cache_generated_on,
                get_previous_revenue_cache_generated_on
            )
            latest_date = get_latest_revenue_cache_generated_on(conn)
            
            # If latest is today (which is incomplete), try the previous one
            if latest_date == today_str:
                latest_date = get_previous_revenue_cache_generated_on(conn, today_str)

            if latest_date and latest_date != today_str:
                logger.info(f"Forecast cache incomplete/missing for {today_str}. Checking fallback: {latest_date}")
                fallback_cached = load_revenue_forecasts(conn, latest_date)
                
                # Check if fallback is complete
                fb_has_gp = fallback_cached.get("gp") and len(fallback_cached.get("gp", [])) > 0
                if fallback_cached and len(fallback_cached) >= 4 and fb_has_gp:
                    cached = fallback_cached
                    target_date = latest_date
                    using_fallback = True
                    is_complete = True
                    logger.info(f"Using fallback forecast from {target_date}")

        # 4. Serve if complete (either today or fallback)
        if is_complete:
            if using_fallback:
                 logger.info(f"Serving fallback revenue forecasts from cache ({len(cached)} models) generated {target_date}")
            else:
                 logger.info(f"Serving revenue forecasts from cache ({len(cached)} models)")

            # We still need fresh historical data for the blue actuals line
            df = get_historical_data(conn, days=90)
            df_history = df[df['ds'].dt.date < today_date]
            history_30d = df_history[df_history['ds'] >= pd.Timestamp(today_date - timedelta(days=30))]

            history_rows = [
                {
                    "sale_date": row['ds'].strftime('%Y-%m-%d'),
                    "revenue": float(row['y']),
                    "orders": int(row['orders']),
                    "temp_max": float(row['temp_max']),
                    "rain_category": get_rain_cat(float(row['rain_sum']))
                }
                for _, row in history_30d.iterrows()
            ]

            raw_forecast = {
                "weekday_avg": cached.get("weekday_avg", []),
                "holt_winters": cached.get("holt_winters", []),
                "prophet": cached.get("prophet", []),
                "gp": cached.get("gp", []),
            }
            # Note: Backtest should also try fallback date if we are using fallback forecast
            backtest_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            backtest = _load_revenue_backtest(conn, backtest_date, MODEL_NAMES)
            
            merged = _merge_backtest_and_forecast(backtest, raw_forecast, target_date)
            forecast_weekday = merged["weekday_avg"]
            forecast_hw = merged["holt_winters"]
            forecast_proph = merged["prophet"]
            forecast_gp_res = merged["gp"]


            forecast_gp_res = merged["gp"]

            future_weekday = [f for f in forecast_weekday if f['date'] >= today_str]
            total_projected_revenue = sum(f['revenue'] for f in future_weekday)
            total_projected_orders = sum(f.get('orders', 0) for f in future_weekday)

            return {
                "summary": {
                    "generated_at": today_str,
                    "projected_7d_revenue": total_projected_revenue,
                    "projected_7d_orders": total_projected_orders
                },
                "historical": history_rows,
                "forecasts": {
                    "weekday_avg": forecast_weekday,
                    "holt_winters": forecast_hw,
                    "prophet": forecast_proph,
                    "gp": forecast_gp_res
                },
                "debug_info": {
                    "holt_winters_error": None,
                    "prophet_error": None,
                    "served_from_cache": True,
                    "using_fallback": using_fallback,
                    "original_generated_at": target_date if using_fallback else None,
                }
            }



        # ---- Cache miss — NO auto-compute. Return empty; user must use Pull from Cloud or Full Retrain ----
        from src.core.forecast_bootstrap import get_bootstrap_endpoint
        cloud_configured = bool(get_bootstrap_endpoint(conn))

        df = get_historical_data(conn, days=90)
        df_history = df[df['ds'].dt.date < today_date]
        history_30d = df_history[df_history['ds'] >= pd.Timestamp(today_date - timedelta(days=30))]
        history_rows = [
            {
                "sale_date": row['ds'].strftime('%Y-%m-%d'),
                "revenue": float(row['y']),
                "orders": int(row['orders']),
                "temp_max": float(row['temp_max']),
                "rain_category": get_rain_cat(float(row['rain_sum']))
            }
            for _, row in history_30d.iterrows()
        ]
        empty_forecast = []
        message = "Forecast cache is empty. Use Pull from Cloud or Full Retrain to populate."
        if not cloud_configured:
            message = "Forecast cache is empty. Configure Cloud Server URL in Configuration to use Pull from Cloud, or use Full Retrain."
        return {
            "summary": {
                "generated_at": today_str,
                "projected_7d_revenue": 0,
                "projected_7d_orders": 0
            },
            "historical": history_rows,
            "forecasts": {
                "weekday_avg": empty_forecast,
                "holt_winters": empty_forecast,
                "prophet": empty_forecast,
                "gp": empty_forecast,
            },
            "debug_info": {
                "holt_winters_error": None,
                "prophet_error": None,
                "awaiting_action": True,
                "cloud_not_configured": not cloud_configured,
                "message": message,
            }
        }
    except Exception as e:
        logger.error(f"Forecast generation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
