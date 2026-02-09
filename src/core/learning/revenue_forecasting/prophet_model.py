"""
Prophet Forecaster Module
Uses Facebook Prophet with weather regressors (temp_max, rain_sum) for revenue forecasting.
"""
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict

from src.core.utils.business_date import get_current_business_date
from src.core.utils.weather_helpers import get_rain_cat

logger = logging.getLogger(__name__)


def forecast_prophet(df: pd.DataFrame, periods: int = 7) -> Dict:
    """
    Generate Prophet forecast with weather regressors.
    
    Args:
        df: Historical data with columns [ds, y, temp_max, rain_sum, forecast_snapshot]
        periods: Number of future days to forecast
        
    Returns:
        Dict with 'data' (list of forecast points) and 'error' (str or None)
    """
    try:
        from prophet import Prophet
        logging.getLogger('prophet').setLevel(logging.WARNING)
        logging.getLogger('cmdstanpy').setLevel(logging.WARNING)
        
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        
        if len(df) < 14:
            return {
                "data": [
                    {"date": (today_date + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0, "temp_max": 0, "rain_category": "none"}
                    for i in range(periods)
                ],
                "error": "Not enough data"
            }
        
        # Filter training data to exclude Today (as it's incomplete)
        train_df = df[df['ds'].dt.date < today_date].copy()
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
        
        # Create future dataframe
        future = model.make_future_dataframe(periods=periods)
        
        # Merge historical regressors
        future = future.merge(df[['ds', 'temp_max', 'rain_sum']], on='ds', how='left')
        
        # Fill future NaNs with Snapshot data from TODAY's row
        # The snapshot is stored in Open-Meteo format:
        # {"time": [...], "temperature_2m_max": [...], "precipitation_sum": [...]}
        today_row = df[df['ds'].dt.date == today_date]
        
        if not today_row.empty:
            snapshot_str = today_row.iloc[0].get('forecast_snapshot')
            if pd.notna(snapshot_str) and snapshot_str:
                try:
                    snapshot = json.loads(snapshot_str)
                    # Parse Open-Meteo format (NOT list of dicts)
                    times = snapshot.get('time', [])
                    temps = snapshot.get('temperature_2m_max', [])
                    precip = snapshot.get('precipitation_sum', [])
                    
                    # Build lookup dictionaries
                    temp_lookup = {t: temps[i] for i, t in enumerate(times) if i < len(temps)}
                    rain_lookup = {t: precip[i] for i, t in enumerate(times) if i < len(precip)}
                    
                    # Fill NaN values in future dataframe
                    for i, row in future.iterrows():
                        date_str = row['ds'].strftime('%Y-%m-%d')
                        if pd.isna(row['temp_max']) and date_str in temp_lookup:
                            future.loc[i, 'temp_max'] = temp_lookup[date_str]
                        if pd.isna(row['rain_sum']) and date_str in rain_lookup:
                            future.loc[i, 'rain_sum'] = rain_lookup[date_str]
                            
                except Exception as e:
                    logger.warning(f"Error parsing forecast snapshot: {e}")
        
        # Fallback for any remaining NaNs
        future['temp_max'] = future['temp_max'].ffill().fillna(25.0)
        future['rain_sum'] = future['rain_sum'].fillna(0)
        
        # Generate predictions
        forecast = model.predict(future)
        
        results = []
        
        # Filter to last 30 days + future
        start_date_hist = pd.Timestamp(today_date - timedelta(days=30))
        hist_forecast = forecast[forecast['ds'] >= start_date_hist]
        
        # CRITICAL: Get raw weather values from 'future' dataframe, NOT from forecast
        # Prophet's predict() overwrites regressor columns with standardized effects
        for _, row in hist_forecast.iterrows():
            date_str = row['ds'].strftime('%Y-%m-%d')
            
            # Look up raw weather values from the input 'future' dataframe
            future_mask = future['ds'] == row['ds']
            if future_mask.any():
                input_row = future[future_mask].iloc[0]
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
        logger.error(f"Prophet forecast error: {e}")
        today_str = get_current_business_date()
        today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
        return {
            "data": [
                {"date": (today_date + timedelta(days=i)).isoformat(), "revenue": 0, "orders": 0, "temp_max": 0, "rain_category": "none"}
                for i in range(periods)
            ],
            "error": str(e)
        }
