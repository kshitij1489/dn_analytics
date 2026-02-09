import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List
from src.core.utils.business_date import get_current_business_date

def forecast_holt_winters(df: pd.DataFrame, periods: int = 7) -> Dict:
    """
    Returns dict with results (Historical Fitted + Future) and error if any.
    """
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        
        if len(df) < 14:
            today_str = get_current_business_date()
            today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
            return {"data": [{"date": (today_date + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0} for i in range(periods)], "error": "Not enough data"}
        
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
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        start_date_hist = pd.Timestamp(today_date - timedelta(days=30))
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
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        return {"data": [{"date": (today_date + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0} for i in range(periods)], "error": str(e)}
