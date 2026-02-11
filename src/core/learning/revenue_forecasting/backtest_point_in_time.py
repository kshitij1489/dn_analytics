"""
Revenue Forecast — Point-in-Time Backtest (T→T+1)

For each forecast_date D and each model, train/fit on data through D-1,
then predict D. Gives "at T, what did we predict for T+1".
"""
import logging
import os
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Any

import pandas as pd

from src.core.utils.path_helper import get_data_path
from src.core.utils.weather_helpers import get_rain_cat

logger = logging.getLogger(__name__)

_MODELS = ["weekday_avg", "holt_winters", "prophet", "gp"]


def _predict_weekday_avg(df: pd.DataFrame, forecast_date: str) -> Dict[str, Any]:
    """Point-in-time: for D, use same-weekday avg from past 4 weeks (data < D)."""
    d = datetime.strptime(forecast_date, "%Y-%m-%d").date()
    past_weekdays = [d - timedelta(weeks=w) for w in range(1, 5)]
    df_before = df[df["ds"].dt.date < d]
    mask = df_before["ds"].dt.date.isin(past_weekdays)
    subset = df_before[mask]
    if len(subset) > 0:
        revenue = float(subset["y"].mean())
        orders = int(subset["orders"].mean())
    else:
        revenue = 0.0
        orders = 0
    return {"date": forecast_date, "revenue": revenue, "orders": orders}


def _predict_holt_winters(df: pd.DataFrame, forecast_date: str) -> Dict[str, Any]:
    """Point-in-time: fit on data through D-1, forecast 1 step (D)."""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        d = datetime.strptime(forecast_date, "%Y-%m-%d").date()
        df_train = df[df["ds"].dt.date < d].copy()
        if len(df_train) < 14:
            return {"date": forecast_date, "revenue": 0, "orders": 0}
        df_train = df_train.set_index("ds")
        full_range = pd.date_range(start=df_train.index.min(), end=df_train.index.max(), freq="D")
        ts = df_train["y"].reindex(full_range).fillna(0)
        model = ExponentialSmoothing(
            ts, seasonal_periods=7, trend="add", seasonal="add", damped_trend=True
        )
        fitted = model.fit(optimized=True)
        forecast = fitted.forecast(1)
        revenue = max(0, float(forecast.iloc[0]))
        return {"date": forecast_date, "revenue": revenue, "orders": 0}
    except Exception as e:
        logger.warning(f"Holt-Winters backtest for {forecast_date}: {e}")
        return {"date": forecast_date, "revenue": 0, "orders": 0}


def _predict_prophet(df: pd.DataFrame, forecast_date: str) -> Dict[str, Any]:
    """Point-in-time: fit on data through D-1, predict D."""
    try:
        from prophet import Prophet
        import logging as log
        log.getLogger("prophet").setLevel(log.WARNING)
        log.getLogger("cmdstanpy").setLevel(log.WARNING)

        d = datetime.strptime(forecast_date, "%Y-%m-%d").date()
        df_train = df[df["ds"].dt.date < d].copy()
        if len(df_train) < 14:
            return {"date": forecast_date, "revenue": 0, "orders": 0, "temp_max": 25.0, "rain_category": "none"}
        prophet_df = df_train[["ds", "y", "temp_max", "rain_sum"]]
        model = Prophet(
            weekly_seasonality=True, daily_seasonality=False, yearly_seasonality=False,
            changepoint_prior_scale=0.05,
        )
        model.add_regressor("temp_max")
        model.add_regressor("rain_sum")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            model.fit(prophet_df)
        future = model.make_future_dataframe(periods=1)
        future = future.merge(df[["ds", "temp_max", "rain_sum"]], on="ds", how="left")
        future["temp_max"] = future["temp_max"].ffill().fillna(25.0)
        future["rain_sum"] = future["rain_sum"].fillna(0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            forecast = model.predict(future)
        last_row = forecast.iloc[-1]
        date_str = last_row["ds"].strftime("%Y-%m-%d")
        revenue = max(0, float(last_row["yhat"]))
        temp = float(future.iloc[-1]["temp_max"])
        rain_val = float(future.iloc[-1]["rain_sum"])
        return {
            "date": date_str,
            "revenue": revenue,
            "orders": 0,
            "temp_max": temp,
            "rain_category": get_rain_cat(rain_val),
        }
    except Exception as e:
        logger.warning(f"Prophet backtest for {forecast_date}: {e}")
        return {"date": forecast_date, "revenue": 0, "orders": 0, "temp_max": 25.0, "rain_category": "none"}


# Single scratch file — overwritten each backtest so only one GP on disk at a time
_GP_BACKTEST_SCRATCH = "data/models/gp_backtest/_scratch.pkl"


def _predict_gp(df: pd.DataFrame, forecast_date: str, conn=None) -> Dict[str, Any]:
    """Point-in-time: fit GP on 90 days through D-1, predict D."""
    try:
        from src.core.learning.revenue_forecasting.gaussianprocess import RollingGPForecaster

        d = datetime.strptime(forecast_date, "%Y-%m-%d").date()
        df_train = df[df["ds"].dt.date < d].copy()
        if len(df_train) < 30:
            return {"date": forecast_date, "revenue": 0, "orders": 0, "gp_lower": 0, "gp_upper": 0}

        # Use single scratch file — overwritten each date to avoid accumulation
        storage_path = get_data_path(_GP_BACKTEST_SCRATCH)
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)

        gp = RollingGPForecaster(storage_path=storage_path)
        gp.update_and_fit(df_train)

        # Single-day future weather
        temp = float(df_train["temp_max"].tail(7).mean()) if "temp_max" in df_train.columns else 25.0
        future_weather = pd.DataFrame({
            "ds": [pd.Timestamp(forecast_date)],
            "temp_max": [temp],
        })
        forecast_df = gp.predict_next_days(future_weather)
        if forecast_df.empty:
            return {"date": forecast_date, "revenue": 0, "orders": 0, "gp_lower": 0, "gp_upper": 0}
        row = forecast_df.iloc[0]
        return {
            "date": forecast_date,
            "revenue": max(0, float(row["pred_mean"])),
            "orders": 0,
            "gp_lower": max(0, float(row["lower"])),
            "gp_upper": max(0, float(row["upper"])),
            "pred_std": float(row.get("pred_std", 0)),
        }
    except Exception as e:
        logger.warning(f"GP backtest for {forecast_date}: {e}")
        return {"date": forecast_date, "revenue": 0, "orders": 0, "gp_lower": 0, "gp_upper": 0}


def predict_revenue_for_date(
    df: pd.DataFrame,
    forecast_date: str,
    model_name: str,
    conn=None,
) -> Dict[str, Any]:
    """
    Point-in-time prediction for one model and one date.
    df must contain data through (forecast_date - 1).
    """
    if model_name == "weekday_avg":
        return _predict_weekday_avg(df, forecast_date)
    if model_name == "holt_winters":
        return _predict_holt_winters(df, forecast_date)
    if model_name == "prophet":
        return _predict_prophet(df, forecast_date)
    if model_name == "gp":
        return _predict_gp(df, forecast_date, conn=conn)
    return {"date": forecast_date, "revenue": 0, "orders": 0}


def run_backtest_for_date(
    df: pd.DataFrame,
    forecast_date: str,
    model_names: List[str],
    conn=None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Run point-in-time backtest for all requested models for one date.
    Returns Dict[model_name, [single row]].
    """
    results: Dict[str, List[Dict[str, Any]]] = {}
    for m in model_names:
        try:
            row = predict_revenue_for_date(df, forecast_date, m, conn=conn)
            # Normalize to list format
            out = {"date": row["date"], "revenue": row.get("revenue", 0), "orders": row.get("orders", 0)}
            if m == "gp":
                out["gp_lower"] = row.get("gp_lower", 0)
                out["gp_upper"] = row.get("gp_upper", 0)
                out["pred_std"] = row.get("pred_std")
            elif m == "prophet":
                out["temp_max"] = row.get("temp_max", 0)
                out["rain_category"] = row.get("rain_category", "none")
            else:
                out["temp_max"] = 0
                out["rain_category"] = "none"
            results[m] = [out]
        except Exception as e:
            logger.warning(f"Backtest {m} for {forecast_date}: {e}")
    return results
