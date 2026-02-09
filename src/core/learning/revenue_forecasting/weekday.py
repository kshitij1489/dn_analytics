import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict
from src.core.utils.business_date import get_current_business_date

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
    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
    
    start_date_hist = today_date - timedelta(days=30)
    df_hist = df[df['ds'].dt.date >= start_date_hist].copy()
    
    all_dates = []
    
    # Add historical dates
    for d in df_hist['ds'].dt.date:
        all_dates.append(d)
        
    # Add future dates
    end_date = today_date
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
    
    return results
