"""
Sales Forecast API Router
Supports multiple forecasting algorithms: Weekday Average, Holt-Winters, Prophet.
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from datetime import date, timedelta
from typing import List, Dict, Any
import pandas as pd
from src.api.routers.config import get_db_connection
from src.core.services.weather_service import WeatherService

import json

router = APIRouter()

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
        print(f"Background weather sync failed: {e}")

def get_rain_cat(mm):
    if mm >= 2.5: return "heavy"
    if mm >= 0.6: return "drizzle"
    return "none"

def get_historical_data(conn, days: int = 90) -> pd.DataFrame:
    """Fetch historical sales and weather data as a DataFrame."""
    end_date = date.today() + timedelta(days=1)
    start_date = end_date - timedelta(days=days + 1)
    
    # Left join orders with weather_daily
    # We aggregate orders first, then join weather
    query = """
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
                DATE(created_on) as sale_date,
                SUM(total) as revenue,
                COUNT(*) as orders_count
            FROM orders
            WHERE order_status = 'Success'
            GROUP BY 1
        ) sales ON d.day = sales.sale_date
        LEFT JOIN weather_daily w ON d.day = w.date AND w.city = 'Gurugram'
        ORDER BY d.day ASC
    """
    cursor = conn.execute(query, (start_date.isoformat(), end_date.isoformat()))
    rows = [dict(row) for row in cursor.fetchall()]
    
    if not rows:
        return pd.DataFrame(columns=['ds', 'y', 'orders', 'temp_max', 'rain_sum', 'weather_code'])
    
    df = pd.DataFrame(rows)
    df['ds'] = pd.to_datetime(df['ds'])
    df['y'] = pd.to_numeric(df['y'], errors='coerce').fillna(0)
    df['orders'] = pd.to_numeric(df['orders'], errors='coerce').fillna(0)
    
    # Fill missing weather with 0 or mean (for propnet regressors we need non-null)
    # Using specific fill values
    df['temp_max'] = pd.to_numeric(df['temp_max'], errors='coerce').fillna(method='ffill').fillna(25.0) 
    df['rain_sum'] = pd.to_numeric(df['rain_sum'], errors='coerce').fillna(0)
    
    return df


def forecast_weekday_avg(df: pd.DataFrame, periods: int = 7) -> List[Dict]:
    """
    Forecast using same-weekday-last-4-weeks average.
    Returns:
       - Historical fitted values (rolling forecast) for the input dataframe's date range (last 30 days)
       - Future forecast for 'periods' days.
    """
    results = []
    
    # We want to cover:
    # 1. Historical Data (last 30 days from df)
    # 2. Future Data (next 'periods' days)
    
    # Filter df to last 30 days for clarity if passed larger df
    start_date_hist = date.today() - timedelta(days=30)
    df_hist = df[df['ds'].dt.date >= start_date_hist].copy()
    
    all_dates = []
    
    # Add historical dates
    for d in df_hist['ds'].dt.date:
        all_dates.append(d)
        
    # Add future dates
    end_date = date.today()
    for i in range(periods):
        all_dates.append(end_date + timedelta(days=i))
        
    # Compute rolling avg for each target date
    for target_date in all_dates:
        # Looking back 4 weeks from THIS target date
        past_weekdays = [target_date - timedelta(weeks=w) for w in range(1, 5)]
        
        # Filter usage/training data (must be STRICTLY BEFORE target_date)
        # We can use the full input 'df' (history) to find these past values
        mask = df['ds'].dt.date.isin(past_weekdays)
        subset = df[mask]
        
        if len(subset) > 0:
            avg_revenue = subset['y'].mean()
            avg_orders = subset['orders'].mean()
        else:
            avg_revenue = 0
            avg_orders = 0
            
        results.append({
            "date": target_date.isoformat(),
            "revenue": float(avg_revenue),
            "orders": round(float(avg_orders))
        })
    
    # Deduplicate by date (just in case), though specific logic above avoids it if input df is clean.
    # Actually, simplistic list append is fine.
    
    return results


def forecast_holt_winters(df: pd.DataFrame, periods: int = 7) -> Dict:
    """
    Returns dict with results (Historical Fitted + Future) and error if any.
    """
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        
        if len(df) < 14:
            return {"data": [{"date": (date.today() + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0} for i in range(periods)], "error": "Not enough data"}
        
        # Prepare time series with proper date range
        df_copy = df.copy()
        df_copy = df_copy.set_index('ds')
        
        # Create a complete date range and reindex
        full_range = pd.date_range(start=df_copy.index.min(), end=df_copy.index.max(), freq='D')
        ts = df_copy['y'].reindex(full_range).fillna(0)
        
        # Fit Holt-Winters model with weekly seasonality
        model = ExponentialSmoothing(
            ts,
            seasonal_periods=7,
            trend='add',
            seasonal='add',
            damped_trend=True
        )
        fitted = model.fit(optimized=True)
        
        # Forecast future
        forecast = fitted.forecast(periods)
        
        results = []
        
        # 1. Historical Fitted Values (Last 30 days)
        start_date_hist = pd.Timestamp(date.today() - timedelta(days=30))
        # Filter fitted values to >= 30 days ago
        fitted_hist = fitted.fittedvalues[fitted.fittedvalues.index >= start_date_hist]
        
        for idx, val in fitted_hist.items():
            results.append({
                "date": idx.strftime('%Y-%m-%d'),
                "revenue": max(0, float(val)),
                "orders": 0
            })
            
        # 2. Future Forecast
        for idx, val in forecast.items():
            results.append({
                "date": idx.strftime('%Y-%m-%d'),
                "revenue": max(0, float(val)), 
                "orders": 0
            })
        
        return {"data": results, "error": None}
    except Exception as e:
        return {"data": [{"date": (date.today() + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0} for i in range(periods)], "error": str(e)}

def forecast_prophet(df: pd.DataFrame, periods: int = 7) -> Dict:
    try:
        from prophet import Prophet
        import logging
        logging.getLogger('prophet').setLevel(logging.WARNING)
        logging.getLogger('cmdstanpy').setLevel(logging.WARNING)
        
        if len(df) < 14:
             return {"data": [{"date": (date.today() + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0, "temp_max": 0, "rain_category": "none"} for i in range(periods)], "error": "Not enough data"}
        
        # Filter training data to exclude Today (as it's incomplete)
        # But we keep full 'df' to access Today's forecast snapshot
        train_df = df[df['ds'].dt.date < date.today()].copy()
        prophet_df = train_df[['ds', 'y', 'temp_max', 'rain_sum']]
        
        model = Prophet(
            weekly_seasonality=True,
            daily_seasonality=False,
            yearly_seasonality=False,
            changepoint_prior_scale=0.05
        )
        model.add_regressor('temp_max')
        model.add_regressor('rain_sum')
        model.fit(prophet_df)
        
        # periods + 1 because make_future_dataframe starts from the last date in train_df (Yesterday)
        # So period=1 is Today. period=7 is Today + 6 days.
        future = model.make_future_dataframe(periods=periods)
        
        # 1. Merge historical regressors
        future = future.merge(df[['ds', 'temp_max', 'rain_sum']], on='ds', how='left')
        
        # 2. Fill future NaNs with Snapshot data from the LAST row (Today)
        # This is CRITICAL because our DB only has data up to Today. 
        # Future 7 days depend on the forecast snapshot stored in today's row.
        last_row = df.iloc[-1]
        
        if last_row.get('forecast_snapshot'):
            try:
                snapshot = json.loads(last_row['forecast_snapshot'])
                times = snapshot.get('time', [])
                temps = snapshot.get('temperature_2m_max', [])
                rains = snapshot.get('precipitation_sum', []) # precipitation_sum includes rain+showers
                
                for i, t_str in enumerate(times):
                    # Update rows where ds matches (and is mostly likely NaN if it's future)
                    # We iterate through snapshot dates and fill 'future' dataframe
                    mask = (future['ds'].dt.strftime('%Y-%m-%d') == t_str)
                    if mask.any():
                        future.loc[mask, 'temp_max'] = future.loc[mask, 'temp_max'].fillna(temps[i])
                        future.loc[mask, 'rain_sum'] = future.loc[mask, 'rain_sum'].fillna(rains[i])
            except Exception as e:
                print(f"Error parsing forecast parsing: {e}")
                
        # Fill any remaining NaNs (fallback)
        future['temp_max'] = future['temp_max'].fillna(method='ffill').fillna(25.0)
        future['rain_sum'] = future['rain_sum'].fillna(0)
        
        forecast = model.predict(future)
        
        # We need to return:
        # 1. Historical Fitted Values (Last 30 days)
        # 2. Future Forecasts (Today ... +6 days)
        
        start_date_hist = pd.Timestamp(date.today() - timedelta(days=30))
        # Filter: ds >= 30 days ago
        forecast_period = forecast[forecast['ds'] >= start_date_hist]
        
        
        results = []
        for idx, row in forecast_period.iterrows():
            # Get the corresponding future inputs to return them
            date_str = row['ds'].strftime('%Y-%m-%d')
            
            # Find input weather for this date from 'future' df (which has merged/snapshot data)
            # Efficient lookup:
            mask = (future['ds'] == row['ds'])
            if mask.any():
                input_row = future[mask].iloc[0]
                temp = float(input_row['temp_max'])
                rain_val = float(input_row['rain_sum'])
            else:
                temp = 0.0
                rain_val = 0.0
            
            results.append({
                "date": date_str,
                "revenue": max(0, float(row['yhat'])),
                "orders": 0,
                "temp_max": temp,
                "rain_category": get_rain_cat(rain_val)
            })
        
        return {"data": results, "error": None}
    except Exception as e:
        return {"data": [{"date": (date.today() + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0, "temp_max": 0, "rain_category": "none"} for i in range(periods)], "error": str(e)}


@router.get("/")
def get_sales_forecast(background_tasks: BackgroundTasks, conn=Depends(get_db)):
    try:
        # Trigger background weather sync
        background_tasks.add_task(sync_weather_task)

        df = get_historical_data(conn, days=90)
        
        # Exclude Today from historical "truth" to prevent partial data from skewing stats
        df_history = df[df['ds'].dt.date < date.today()]
        
        history_30d = df_history[df_history['ds'] >= pd.Timestamp(date.today() - timedelta(days=30))]
        

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
        future_start_date = date.today().isoformat()
        
        # Filter for summary stats
        future_weekday = [f for f in forecast_weekday if f['date'] >= future_start_date]
        
        total_projected_revenue = sum(f['revenue'] for f in future_weekday)
        total_projected_orders = sum(f['orders'] for f in future_weekday)
        
        return {
            "summary": {
                "generated_at": date.today().isoformat(),
                "projected_7d_revenue": total_projected_revenue,
                "projected_7d_orders": total_projected_orders
            },
            "historical": history_rows,
            "forecasts": {
                "weekday_avg": forecast_weekday,
                "holt_winters": forecast_hw,
                "prophet": forecast_proph
            },
            "debug_info": {
                "holt_winters_error": hw_res.get("error"),
                "prophet_error": proph_res.get("error")
            }
        }
    except Exception as e:
        print(f"Forecast generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
