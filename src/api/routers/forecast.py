"""
Sales Forecast API Router
Supports multiple forecasting algorithms: Weekday Average, Holt-Winters, Prophet, Gaussian Process.
"""
import logging
import threading
import json

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from datetime import timedelta, datetime
from typing import List, Dict, Optional

import pandas as pd

from src.api.routers.config import get_db_connection
from src.core.services.weather_service import WeatherService
from src.core.utils.business_date import BUSINESS_DATE_SQL, get_current_business_date, get_last_complete_business_date
from src.core.utils.weather_helpers import get_rain_cat
from src.core.learning.revenue_forecasting.gaussianprocess import RollingGPForecaster
from src.core.learning.revenue_forecasting.weekday import forecast_weekday_avg
from src.core.learning.revenue_forecasting.holtwinters import forecast_holt_winters
from src.core.learning.revenue_forecasting.prophet_model import forecast_prophet

logger = logging.getLogger(__name__)
router = APIRouter()

# Lock to prevent concurrent training runs.
# NOTE: Only effective for single-worker deployments. For multi-worker (uvicorn --workers > 1),
# use a file lock (e.g., filelock) or external coordinator to prevent model file corruption.
_training_lock = threading.Lock()


def _load_and_check_stale(gp: RollingGPForecaster) -> bool:
    """
    Load the GP model and check if it needs retraining.
    
    Side effect: Loads the model into `gp` if a persisted model exists.
    
    Returns True if:
    - No model file exists
    - Model's window_end is older than last complete business date
    """
    if not gp.load():
        return True  # No model exists
    
    expected_end_str = get_last_complete_business_date()
    expected_end = pd.Timestamp(expected_end_str)
    
    if gp.window_end is None or gp.window_end < expected_end:
        logger.info(f"Model stale: window_end={gp.window_end}, expected={expected_end}")
        return True
    
    return False

def get_db():
    conn, _ = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


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
        # Handle flexible date formats using pandas
        start_dt = pd.to_datetime(start_date, dayfirst=True).date()
        end_dt = pd.to_datetime(end_date, dayfirst=True).date()
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

def train_gp_task():
    """Background task for daily GP training with proper locking and error handling."""
    # Idempotency: prevent concurrent training runs
    if not _training_lock.acquire(blocking=False):
        logger.warning("GP training already in progress, skipping duplicate request.")
        return
    
    conn = None
    try:
        from src.api.routers.config import get_db_connection
        conn, _ = get_db_connection()
        logger.info("Starting Daily GP Training...")
        
        # Fetch enough data for lag features + 90 days window
        df = get_historical_data(conn, days=120)
        
        # CRITICAL: Filter to only include complete business days
        # Use last complete business date (yesterday relative to 5am cutoff)
        last_complete_str = get_last_complete_business_date()
        last_complete_date = pd.Timestamp(last_complete_str)
        df = df[df['ds'] <= last_complete_date].copy()
        
        # NOTE: Do NOT filter y > 0 here! The lag features must be computed on the
        # full calendar first (inside update_and_fit) to preserve temporal ordering.
        # Zero-revenue days are handled inside RollingGPForecaster.update_and_fit().
        
        logger.info(f"Training data after date filtering: {len(df)} rows, ending at {df['ds'].max()}")
        
        # RollingGPForecaster handles the windowing logic (keeping last 90 days)
        gp = RollingGPForecaster()
        gp.update_and_fit(df)
        
        # ----------------------------------------------------
        # Generate and Save Forecast Snapshot 
        # ----------------------------------------------------
        logger.info("Generating forecast snapshot...")
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        
        # Fetch actual forecast weather from weather_daily table
        future_dates = [today_date + timedelta(days=i) for i in range(7)]
        future_weather = pd.DataFrame({'ds': pd.to_datetime(future_dates)})
        
        # Try to get weather from Today's forecast_snapshot in DB
        weather_temps = _fetch_forecast_weather(conn, today_str, future_dates)
        future_weather['temp_max'] = weather_temps
        
        forecast_df = gp.predict_next_days(future_weather)
        gp.save_daily_forecast_snapshot(conn, today_str, forecast_df)
        
        logger.info("Daily GP Training & Snapshot Completed.")
        
    except Exception as e:
        logger.error(f"GP Training failed: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
        _training_lock.release()


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
    Returns the forecast snapshot generated on 'run_date' 
    AND the actual revenue that occurred on those target dates.
    """
    # Validate run_date format
    try:
        datetime.strptime(run_date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format: '{run_date}'. Expected YYYY-MM-DD.")
    
    try:
        # 1. Fetch Forecast Snapshot (include pred_std and model window metadata)
        cursor = conn.execute("""
            SELECT target_date, pred_mean, pred_std, lower_95, upper_95, 
                   model_window_start, model_window_end
            FROM forecast_snapshots
            WHERE forecast_run_date = ?
            ORDER BY target_date ASC
        """, (run_date,))
        
        forecast_rows = []
        target_dates = []
        model_window: Optional[Dict] = None
        
        for row in cursor.fetchall():
            target_dates.append(row[0])  # string 'YYYY-MM-DD'
            forecast_rows.append({
                "date": row[0],
                "pred_mean": row[1],
                "pred_std": row[2],
                "lower_95": row[3],
                "upper_95": row[4]
            })
            if model_window is None and (row[5] or row[6]):
                model_window = {"start": row[5], "end": row[6]}
            
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
                "actual_revenue": actuals_map.get(dt, None)  # Null if not occurred yet
            })
            
        return {
            "data": results,
            "model_window": model_window
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Replay endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

def forecast_gp(future_weather: pd.DataFrame) -> List[Dict]:
    """
    Generates GP forecast using the persistent trained model.
    
    If model is stale, triggers background training and returns empty result.
    GP data will be available on next page load/refresh.
    """
    try:
        gp = RollingGPForecaster()
        
        # Check if model is stale or missing
        if _load_and_check_stale(gp):
            # Trigger training in background thread (non-blocking)
            logger.info("GP model is stale or missing, triggering background training...")
            threading.Thread(target=train_gp_task, daemon=True).start()
            return []  # GP unavailable this request cycle
        
        # Model is loaded and up-to-date, generate predictions
        forecast_df = gp.predict_next_days(future_weather)
        
        results = []
        for _, row in forecast_df.iterrows():
            results.append({
                "date": row['ds'].strftime('%Y-%m-%d'),
                "revenue": max(0, float(row['pred_mean'])),
                "orders": 0, # GP doesn't predict orders yet
                "gp_lower": max(0, float(row['lower'])),
                "gp_upper": max(0, float(row['upper']))
            })
            
        return results
    except Exception as e:
        logger.warning(f"GP Forecast error: {e}")
        return []

@router.get("/")
def get_sales_forecast(background_tasks: BackgroundTasks, conn=Depends(get_db)):
    try:
        # Trigger background weather sync
        background_tasks.add_task(sync_weather_task)

        df = get_historical_data(conn, days=90)
        
        # Exclude Today from historical "truth" to prevent partial data from skewing stats
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        
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
        
        forecast_weekday = forecast_weekday_avg(df_history, periods=7)
        # Weekday Forecast doesn't predict weather, so fill defaults
        for f in forecast_weekday:
            f['temp_max'] = 0
            f['rain_category'] = 'none'

        hw_res = forecast_holt_winters(df_history, periods=7)
        for f in hw_res['data']:
            f['temp_max'] = 0
            f['rain_category'] = 'none'

        # Prophet needs FULL df (including Today's snapshot) but knows how to exclude Today from training
        proph_res = forecast_prophet(df, periods=7)
        
        forecast_hw = hw_res["data"]
        forecast_proph = proph_res["data"]
        
        # Calculate Projected Revenue/Orders only for FUTURE dates (next 7 days)
        # forecast_weekday now includes history, so we must filter.
        future_start_date = today_str
        
        # Filter for summary stats
        future_weekday = [f for f in forecast_weekday if f['date'] >= future_start_date]
        
        total_projected_revenue = sum(f['revenue'] for f in future_weekday)
        total_projected_orders = sum(f['orders'] for f in future_weekday)
        
        # Gaussian Process Forecast (Rolling)
        # Prepare future weather dataframe for GP
        # We can reuse Prophet's future dataframe logic or built it simply
        future_weather_gp = pd.DataFrame({
            'ds': pd.to_datetime([f['date'] for f in forecast_hw[-7:]]), # Last 7 days are future
            'temp_max': [f.get('temp_max', 25) for f in forecast_proph[-7:]] # Use prophet's filled weather or default
        })
        forecast_gp_res = forecast_gp(future_weather_gp)

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
                "holt_winters_error": hw_res.get("error"),
                "prophet_error": proph_res.get("error")
            }
        }
    except Exception as e:
        logger.error(f"Forecast generation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
