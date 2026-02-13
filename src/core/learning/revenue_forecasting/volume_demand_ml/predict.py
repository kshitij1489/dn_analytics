"""
Volume Demand ML — Prediction

Generates menu-item-level volume forecasts using:
  final_volume = P(sold) × predicted_volume

Uses autoregressive day-by-day prediction (same as item demand).
Entity = menu_item_id. Target = volume_sold (gms/ml/units).
"""
import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.core.learning.revenue_forecasting.volume_demand_ml.dataset import densify_daily_grid
from src.core.learning.revenue_forecasting.volume_demand_ml.features import (
    build_features, get_feature_columns,
)
from src.core.learning.revenue_forecasting.volume_demand_ml.model_io import get_models

logger = logging.getLogger(__name__)

_EMPTY_FORECAST_COLS = [
    'date', 'item_id', 'item_name', 'unit',
    'predicted_p50', 'predicted_p90', 'probability_of_sale',
    'recommended_volume',
]


def _is_classifier_degenerate(classifier: Any) -> bool:
    """Detect single-class classifier; bypass and use P(sold)=1.0 if so."""
    classes = getattr(classifier, 'classes_', None)
    return classes is not None and len(classes) < 2


def _infer_future_weather(
    df_history: pd.DataFrame,
    df_future: pd.DataFrame,
) -> pd.DataFrame:
    """Fill missing weather in future rows using recent history averages."""
    df_future = df_future.copy()
    if df_history is None or len(df_history) == 0:
        return df_future

    recent_weather = (
        df_history.groupby('date')[['temperature', 'rain']]
        .first()
        .tail(7)
    )
    avg_temp = recent_weather['temperature'].mean()
    avg_rain = recent_weather['rain'].mean()

    if pd.notna(avg_temp):
        if 'temperature' not in df_future.columns:
            df_future['temperature'] = avg_temp
        else:
            df_future['temperature'] = (
                pd.to_numeric(df_future['temperature'], errors='coerce').fillna(avg_temp)
            )
    if pd.notna(avg_rain):
        if 'rain' not in df_future.columns:
            df_future['rain'] = avg_rain
        else:
            df_future['rain'] = (
                pd.to_numeric(df_future['rain'], errors='coerce').fillna(avg_rain)
            )
    return df_future


def forecast_volumes(
    df_future_dates: pd.DataFrame,
    df_history: Optional[pd.DataFrame] = None,
    model_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Generate menu-item-level volume forecasts for future dates.

    Args:
        df_future_dates: DataFrame with [date, item_id, item_name, unit] for each
                         future (date, item) combination. May include category, price.
        df_history: Optional historical DataFrame for lag features.
        model_dir: Path to model artifacts.

    Returns:
        DataFrame with date, item_id, item_name, unit,
        predicted_p50, predicted_p90, probability_of_sale, recommended_volume.
    """
    logger.info(f"Forecasting volumes: {len(df_future_dates)} future rows")

    classifier, reg_p50, reg_p90, feature_cols = get_models(model_dir)
    clf_degenerate = _is_classifier_degenerate(classifier)
    if clf_degenerate:
        logger.warning("Classifier degenerate — using P(sold)=1.0 for all items.")

    if df_history is not None and len(df_history) > 0:
        df_history = densify_daily_grid(df_history)
    else:
        df_history = pd.DataFrame()

    df_future_dates = _infer_future_weather(df_history, df_future_dates)
    df_future_dates = df_future_dates.copy()
    if 'volume_sold' not in df_future_dates.columns:
        df_future_dates['volume_sold'] = 0

    future_dates_sorted = sorted(df_future_dates['date'].unique())
    running_history = df_history.copy()
    results = []

    for target_date in future_dates_sorted:
        day_rows = df_future_dates[df_future_dates['date'] == target_date].copy()
        day_rows['volume_sold'] = 0
        day_rows['_is_future'] = True

        if len(running_history) > 0:
            hist = running_history.copy()
            hist['_is_future'] = False
            combined = pd.concat([hist, day_rows], ignore_index=True)
            combined = combined.sort_values(['item_id', 'date']).reset_index(drop=True)
        else:
            combined = day_rows.copy()

        df_feat = build_features(combined, is_future=True)
        df_target = df_feat[df_feat['_is_future'] == True].copy()  # noqa: E712

        if len(df_target) == 0:
            continue

        X = df_target[feature_cols].fillna(0)

        if clf_degenerate:
            prob_sold = np.ones(len(X))
        else:
            prob_sold = classifier.predict_proba(X)[:, 1]

        pred_p50_norm = np.maximum(0, reg_p50.predict(X))
        pred_p90_norm = np.maximum(0, reg_p90.predict(X))
        pred_p90_norm = np.maximum(pred_p90_norm, pred_p50_norm)

        # Denormalize: model predicts volume_sold / item_median_volume
        scale = df_target['item_median_volume'].fillna(1.0).clip(lower=1e-6).values
        pred_p50 = pred_p50_norm * scale
        pred_p90 = pred_p90_norm * scale

        final_p50 = prob_sold * pred_p50
        final_p90 = prob_sold * pred_p90
        recommended_volume = np.ceil(0.7 * final_p90 + 0.3 * final_p50)

        item_names = (
            df_target['item_name'].values
            if 'item_name' in df_target.columns
            else ['Unknown'] * len(df_target)
        )
        units = (
            df_target['unit'].values
            if 'unit' in df_target.columns
            else ['mg'] * len(df_target)
        )
        day_result = pd.DataFrame({
            'date': df_target['date'].values,
            'item_id': df_target['item_id'].values,
            'item_name': item_names,
            'unit': units,
            'predicted_p50': np.round(final_p50, 2),
            'predicted_p90': np.round(final_p90, 2),
            'probability_of_sale': np.round(prob_sold, 4),
            'recommended_volume': recommended_volume.astype(float),
        })
        results.append(day_result)

        feedback = day_rows.drop(columns=['_is_future'], errors='ignore').copy()
        feedback['volume_sold'] = np.maximum(0, final_p50)
        running_history = pd.concat([running_history, feedback], ignore_index=True)

    if not results:
        logger.warning("No future rows to predict.")
        return pd.DataFrame(columns=_EMPTY_FORECAST_COLS)

    result = pd.concat(results, ignore_index=True)
    logger.info(f"Volume forecast: {len(result)} rows, {result['item_id'].nunique()} items")
    return result
