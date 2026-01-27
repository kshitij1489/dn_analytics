"""
Sales Forecast API Router
Supports multiple forecasting algorithms: Weekday Average, Holt-Winters, Prophet.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import date, timedelta
from typing import List, Dict, Any
import pandas as pd
from src.api.routers.config import get_db_connection

router = APIRouter()

def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_historical_data(conn, days: int = 90) -> pd.DataFrame:
    """Fetch historical sales data as a DataFrame."""
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    query = """
        SELECT 
            DATE(created_on) as ds,
            SUM(total) as y,
            COUNT(*) as orders
        FROM orders
        WHERE order_status = 'Success'
          AND DATE(created_on) >= ?
          AND DATE(created_on) < ?
        GROUP BY 1
        ORDER BY 1 ASC
    """
    cursor = conn.execute(query, (start_date.isoformat(), end_date.isoformat()))
    rows = [dict(row) for row in cursor.fetchall()]
    
    if not rows:
        return pd.DataFrame(columns=['ds', 'y', 'orders'])
    
    df = pd.DataFrame(rows)
    df['ds'] = pd.to_datetime(df['ds'])
    df['y'] = pd.to_numeric(df['y'], errors='coerce').fillna(0)
    return df


def forecast_weekday_avg(df: pd.DataFrame, periods: int = 7) -> List[Dict]:
    """
    Forecast using same-weekday-last-4-weeks average.
    """
    results = []
    end_date = date.today()
    
    for i in range(periods):
        target_date = end_date + timedelta(days=i)
        past_weekdays = [target_date - timedelta(weeks=w) for w in range(1, 5)]
        
        # Filter historical data for matching weekdays
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
    
    return results


def forecast_holt_winters(df: pd.DataFrame, periods: int = 7) -> Dict:
    """
    Returns dict with results and error if any.
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
        
        # Forecast
        forecast = fitted.forecast(periods)
        
        results = []
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
             return {"data": [{"date": (date.today() + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0} for i in range(periods)], "error": "Not enough data"}
        
        prophet_df = df[['ds', 'y']].copy()
        
        model = Prophet(
            weekly_seasonality=True,
            daily_seasonality=False,
            yearly_seasonality=False,
            changepoint_prior_scale=0.05
        )
        model.fit(prophet_df)
        
        future = model.make_future_dataframe(periods=periods)
        forecast = model.predict(future)
        forecast_period = forecast.tail(periods)
        
        results = []
        for _, row in forecast_period.iterrows():
            results.append({
                "date": row['ds'].strftime('%Y-%m-%d'),
                "revenue": max(0, float(row['yhat'])),
                "orders": 0
            })
        
        return {"data": results, "error": None}
    except Exception as e:
        return {"data": [{"date": (date.today() + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0} for i in range(periods)], "error": str(e)}


@router.get("/")
def get_sales_forecast(conn=Depends(get_db)):
    try:
        df = get_historical_data(conn, days=90)
        
        history_30d = df[df['ds'] >= pd.Timestamp(date.today() - timedelta(days=30))]
        history_rows = [
            {"sale_date": row['ds'].strftime('%Y-%m-%d'), "revenue": float(row['y']), "orders": int(row['orders'])}
            for _, row in history_30d.iterrows()
        ]
        
        forecast_weekday = forecast_weekday_avg(df, periods=7)
        
        # Get forecasts with debug info
        hw_res = forecast_holt_winters(df, periods=7)
        proph_res = forecast_prophet(df, periods=7)
        
        forecast_hw = hw_res["data"]
        forecast_proph = proph_res["data"]
        
        total_projected_revenue = sum(f['revenue'] for f in forecast_weekday)
        total_projected_orders = sum(f['orders'] for f in forecast_weekday)
        
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
