"""
Item Demand ML — Prediction

Generates item-level demand forecasts using the two-stage approach:
  final_demand = P(sold) × predicted_quantity

Uses autoregressive day-by-day prediction: each day's forecast is fed back
as "actual" quantity for computing the next day's lag features, so that
multi-step forecasts use realistic lag signals instead of zeros.

Exposes forecast_items() as the main API, matching the interface pattern
of prophet_model.forecast_prophet() and gaussianprocess.forecast_days().
"""
import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.core.learning.revenue_forecasting.item_demand_ml.dataset import densify_daily_grid
from src.core.learning.revenue_forecasting.item_demand_ml.features import (
    build_features, get_feature_columns,
)
from src.core.learning.revenue_forecasting.item_demand_ml.model_io import get_models

logger = logging.getLogger(__name__)

_EMPTY_FORECAST_COLS = [
    'date', 'item_id', 'item_name',
    'predicted_p50', 'predicted_p90', 'probability_of_sale',
    'recommended_prep',
]


def _is_classifier_degenerate(classifier: Any) -> bool:
    """
    Detect whether the classifier was trained on single-class data.

    A degenerate classifier (only saw class 1 during training) will produce
    meaningless probabilities. Callers should bypass the classifier gate
    and set P(sold) = 1.0 when this returns True.
    """
    classes = getattr(classifier, 'classes_', None)
    if classes is not None and len(classes) < 2:
        return True
    return False


# ---------------------------------------------------------------------------
# PART 1: Weather future data leakage fix
# Replace naive constant fill (25°C / 0mm) with history-based estimates
# so that future weather features are realistic, not biased defaults.
# ---------------------------------------------------------------------------

def _infer_future_weather(
    df_history: pd.DataFrame,
    df_future: pd.DataFrame,
) -> pd.DataFrame:
    """
    Fill missing weather values in future rows using recent history averages.

    Uses the last 7 days of historical weather to estimate:
      - temperature: 7-day average
      - rain: 7-day average

    This replaces the naive fillna(25.0) / fillna(0.0) that biases predictions.
    """
    df_future = df_future.copy()

    if df_history is None or len(df_history) == 0:
        return df_future

    # Date-level weather from history (one value per date)
    recent_weather = (
        df_history.groupby('date')[['temperature', 'rain']]
        .first()
        .tail(7)
    )

    avg_temp = recent_weather['temperature'].mean()
    avg_rain = recent_weather['rain'].mean()

    # Fill missing / NaN temperature with history average
    if pd.notna(avg_temp):
        if 'temperature' not in df_future.columns:
            df_future['temperature'] = avg_temp
        else:
            df_future['temperature'] = (
                pd.to_numeric(df_future['temperature'], errors='coerce')
                .fillna(avg_temp)
            )

    # Fill missing / NaN rain with history average
    if pd.notna(avg_rain):
        if 'rain' not in df_future.columns:
            df_future['rain'] = avg_rain
        else:
            df_future['rain'] = (
                pd.to_numeric(df_future['rain'], errors='coerce')
                .fillna(avg_rain)
            )

    return df_future


def forecast_items(
    df_future_dates: pd.DataFrame,
    df_history: Optional[pd.DataFrame] = None,
    model_dir: Optional[str] = None,
) -> pd.DataFrame:
    """
    Generate item-level demand forecasts for future dates.

    Uses autoregressive prediction: forecasts are produced one day at a time,
    and each day's predicted quantity is fed back into the history so that
    lag / rolling features for subsequent days reflect the forecast rather
    than defaulting to zero.

    Args:
        df_future_dates: DataFrame with columns [date, item_id, item_name] for each
                         future (date, item) combination to forecast.
                         May also contain: temperature, rain, price, category.
        df_history: Optional historical DataFrame for computing lag features.
                    If provided, it is densified (zero-sales rows added) and
                    prepended before feature engineering.
        model_dir: Path to directory containing saved model artifacts.

    Returns:
        DataFrame with columns:
            date, item_id, item_name, predicted_p50, predicted_p90,
            probability_of_sale, recommended_prep
    """
    logger.info(f"Forecasting items: {len(df_future_dates)} future rows")

    # Load models (lazy — cached after first call)
    classifier, reg_p50, reg_p90, feature_cols = get_models(model_dir)

    # Detect degenerate classifier (trained on single-class data)
    clf_degenerate = _is_classifier_degenerate(classifier)
    if clf_degenerate:
        logger.warning(
            "Classifier is degenerate (single-class training data). "
            "Bypassing classifier gate — using P(sold)=1.0 for all items."
        )

    # Densify history so lag features are calendar-day-aligned (no date gaps)
    if df_history is not None and len(df_history) > 0:
        df_history = densify_daily_grid(df_history)
    else:
        df_history = pd.DataFrame()

    # PART 1: Infer realistic future weather from recent history
    # (replaces naive constant fill that biases predictions)
    df_future_dates = _infer_future_weather(df_history, df_future_dates)

    # Ensure future rows have quantity_sold placeholder
    df_future_dates = df_future_dates.copy()
    if 'quantity_sold' not in df_future_dates.columns:
        df_future_dates['quantity_sold'] = 0

    # Sort future dates for autoregressive iteration
    future_dates_sorted = sorted(df_future_dates['date'].unique())

    # Running history grows as we feed back predicted quantities each day
    running_history = df_history.copy()

    results = []

    for target_date in future_dates_sorted:
        # Rows for this future day
        day_rows = df_future_dates[df_future_dates['date'] == target_date].copy()
        day_rows['quantity_sold'] = 0
        day_rows['_is_future'] = True

        # Combine history + this day
        if len(running_history) > 0:
            hist = running_history.copy()
            hist['_is_future'] = False
            combined = pd.concat([hist, day_rows], ignore_index=True)
            combined = combined.sort_values(['item_id', 'date']).reset_index(drop=True)
        else:
            combined = day_rows.copy()

        # Feature engineering (is_future=True keeps NaN lags for new items)
        df_feat = build_features(combined, is_future=True)

        # Filter to the target future day
        df_target = df_feat[df_feat['_is_future'] == True].copy()  # noqa: E712

        if len(df_target) == 0:
            continue

        # Fill any remaining NaN features with 0 (new items with no history)
        X = df_target[feature_cols].fillna(0)

        # ---- Stage 1: Classification — P(sold) ----
        if clf_degenerate:
            prob_sold = np.ones(len(X))
        else:
            prob_sold = classifier.predict_proba(X)[:, 1]

        # ---- Stage 2: Regression — quantity if sold ----
        pred_p50 = np.maximum(0, reg_p50.predict(X))
        pred_p90 = np.maximum(0, reg_p90.predict(X))

        # PART 3: Quantile crossing fix — ensure p90 >= p50
        # Tree-based quantile regressors can produce crossing quantiles.
        pred_p90 = np.maximum(pred_p90, pred_p50)

        # Final demand = probability × quantity
        final_p50 = prob_sold * pred_p50
        final_p90 = prob_sold * pred_p90

        # PART 8: Inventory-friendly recommended prep quantity
        # Blends p50 (likely) and p90 (safe) for production planning.
        recommended_prep = np.ceil(0.7 * final_p90 + 0.3 * final_p50)

        # Collect this day's results
        item_names = (
            df_target['item_name'].values
            if 'item_name' in df_target.columns
            else ['Unknown'] * len(df_target)
        )
        day_result = pd.DataFrame({
            'date': df_target['date'].values,
            'item_id': df_target['item_id'].values,
            'item_name': item_names,
            'predicted_p50': np.round(final_p50, 2),
            'predicted_p90': np.round(final_p90, 2),
            'probability_of_sale': np.round(prob_sold, 4),
            'recommended_prep': recommended_prep.astype(int),
        })
        results.append(day_result)

        # ---- Autoregressive feedback ----
        # Feed predicted p50 back as "actual" quantity for next day's lag features
        feedback = day_rows.drop(columns=['_is_future'], errors='ignore').copy()
        feedback['quantity_sold'] = np.maximum(0, np.round(final_p50)).astype(int)
        running_history = pd.concat([running_history, feedback], ignore_index=True)

    if not results:
        logger.warning("No future rows to predict after feature engineering.")
        return pd.DataFrame(columns=_EMPTY_FORECAST_COLS)

    result = pd.concat(results, ignore_index=True)
    logger.info(f"Forecast generated: {len(result)} rows, "
                f"{result['item_id'].nunique()} items")
    return result
