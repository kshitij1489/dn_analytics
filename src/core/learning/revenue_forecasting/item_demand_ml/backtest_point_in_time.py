"""
Item Demand ML — Point-in-Time Backtest (T→T+1)

For each forecast_date D, train a model on 120 days ending at D-1,
then predict D. This gives "at T, what did we predict for T+1".

Results are cached in item_backtest_cache; only missing dates are computed.

Uses a single scratch directory that is overwritten for each date, avoiding
accumulation of per-date model files (saves disk space, especially in .dmg).
"""
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from src.core.utils.path_helper import get_data_path

logger = logging.getLogger(__name__)

# Single scratch dir — overwritten each backtest so only one model on disk at a time
_BACKTEST_SCRATCH_DIR = "data/models/item_demand_backtest/_scratch"


def train_and_predict_for_date(
    df_history: pd.DataFrame,
    items_info: pd.DataFrame,
    forecast_date: str,
) -> pd.DataFrame:
    """
    Train a model on data through (forecast_date - 1) and predict forecast_date.

    Args:
        df_history: DataFrame with columns [date, item_id, item_name, quantity_sold, ...]
                    covering at least 120 days ending the day before forecast_date.
        items_info: DataFrame with item_id, item_name for items to predict.
        forecast_date: Date to predict (YYYY-MM-DD).

    Returns:
        DataFrame with date, item_id, item_name, predicted_p50, predicted_p90, probability_of_sale.
        Empty if training or prediction fails.
    """
    try:
        from src.core.learning.revenue_forecasting.item_demand_ml.train import train_pipeline
        from src.core.learning.revenue_forecasting.item_demand_ml.predict import forecast_items
        from src.core.learning.revenue_forecasting.item_demand_ml.model_io import (
            clear_model_cache,
            get_models,
        )
    except (ImportError, OSError) as e:
        logger.warning(f"Item demand ML not available for point-in-time backtest: {e}")
        return pd.DataFrame()

    if df_history.empty or items_info.empty:
        return pd.DataFrame()

    model_trained_through_dt = datetime.strptime(forecast_date, "%Y-%m-%d").date() - timedelta(days=1)
    model_trained_through = model_trained_through_dt.isoformat()
    backtest_dir = get_data_path(_BACKTEST_SCRATCH_DIR)
    os.makedirs(backtest_dir, exist_ok=True)

    try:
        # 1. Train on history through D-1
        logger.info(f"Point-in-time backtest: training model for {forecast_date} (data through {model_trained_through})")
        train_pipeline(df=df_history, save_path=backtest_dir, evaluate=False)

        # 2. Clear cache so we load from backtest dir
        clear_model_cache()
        get_models(backtest_dir)

        # 3. Build single-day future grid and predict
        forecast_dt = pd.Timestamp(forecast_date)
        df_future = items_info[["item_id", "item_name"]].copy()
        df_future["date"] = forecast_dt
        df_future = df_future[["date", "item_id", "item_name"]]

        result = forecast_items(
            df_future_dates=df_future,
            df_history=df_history,
            model_dir=backtest_dir,
        )

        # 4. Restore main model for subsequent requests
        clear_model_cache()

        return result
    except Exception as e:
        logger.error(f"Point-in-time backtest failed for {forecast_date}: {e}", exc_info=True)
        clear_model_cache()
        return pd.DataFrame()
