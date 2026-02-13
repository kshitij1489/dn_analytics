"""
Volume Demand ML — Point-in-Time Backtest (T→T+1)

For each forecast_date D, train on data through D-1, predict D.
Results cached in volume_backtest_cache.
Entity = menu_item_id (same as item demand).
"""
import logging
import os
from datetime import datetime, timedelta

import pandas as pd

from src.core.utils.path_helper import get_data_path

logger = logging.getLogger(__name__)

_BACKTEST_SCRATCH_DIR = "data/models/volume_demand_backtest/_scratch"


def train_and_predict_for_date(
    df_history: pd.DataFrame,
    items_info: pd.DataFrame,
    forecast_date: str,
) -> pd.DataFrame:
    """
    Train on data through (forecast_date - 1) and predict forecast_date.

    Args:
        df_history: Volume sales DataFrame covering ≥120 days ending before forecast_date.
        items_info: DataFrame with item_id, item_name, unit for menu items to predict.
        forecast_date: Date to predict (YYYY-MM-DD).

    Returns:
        DataFrame with date, item_id, item_name, unit,
        predicted_p50, predicted_p90, probability_of_sale.
    """
    try:
        from src.core.learning.revenue_forecasting.volume_demand_ml.train import train_pipeline
        from src.core.learning.revenue_forecasting.volume_demand_ml.predict import forecast_volumes
        from src.core.learning.revenue_forecasting.volume_demand_ml.model_io import (
            clear_model_cache,
            get_models,
        )
    except (ImportError, OSError) as e:
        logger.warning(f"Volume demand ML not available: {e}")
        return pd.DataFrame()

    if df_history.empty or items_info.empty:
        return pd.DataFrame()

    model_trained_through_dt = datetime.strptime(forecast_date, "%Y-%m-%d").date() - timedelta(days=1)
    model_trained_through = model_trained_through_dt.isoformat()
    backtest_dir = get_data_path(_BACKTEST_SCRATCH_DIR)
    os.makedirs(backtest_dir, exist_ok=True)

    try:
        logger.info(f"Point-in-time volume backtest: {forecast_date} (data through {model_trained_through})")
        train_pipeline(df=df_history, save_path=backtest_dir, evaluate=False)

        clear_model_cache()
        get_models(backtest_dir)

        forecast_dt = pd.Timestamp(forecast_date)
        df_future = items_info[["item_id", "item_name", "unit"]].copy()
        df_future["date"] = forecast_dt
        df_future = df_future[["date", "item_id", "item_name", "unit"]]

        result = forecast_volumes(
            df_future_dates=df_future,
            df_history=df_history,
            model_dir=backtest_dir,
        )

        clear_model_cache()
        return result
    except Exception as e:
        logger.error(f"Volume backtest failed for {forecast_date}: {e}", exc_info=True)
        clear_model_cache()
        return pd.DataFrame()
