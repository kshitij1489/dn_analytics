"""
Volume Demand ML — Feature Engineering

Builds time, lag, weather, price, and context features for menu-item volume model.
Same structure as item demand: entity = item_id (menu_item_id), target = volume_sold (float).
"""
import logging
from typing import List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_features(df: pd.DataFrame, is_future: bool = False) -> pd.DataFrame:
    """
    Build all features from raw volume-sales DataFrame.

    Args:
        df: DataFrame with [date, item_id, volume_sold, temperature, rain, ...].
        is_future: If True, skip lag feature NaN dropping.

    Returns:
        DataFrame with all original columns plus engineered features.
    """
    df = df.copy()

    df = _add_time_features(df)
    df['temperature'] = pd.to_numeric(df['temperature'], errors='coerce').fillna(25.0)
    df['rain'] = pd.to_numeric(df['rain'], errors='coerce').fillna(0.0)
    df = _add_store_context_features(df)
    df = _add_lag_features(df)
    df = _add_price_features(df)
    df = _add_cold_start_features(df)
    df['temp_weekend'] = df['temperature'] * df['is_weekend']

    if not is_future:
        lag_cols = ['lag_1', 'lag_7', 'rolling_mean_7', 'rolling_mean_14', 'rolling_trend_3']
        before = len(df)
        df = df.dropna(subset=lag_cols).reset_index(drop=True)
        dropped = before - len(df)
        if dropped > 0:
            logger.info(f"Dropped {dropped} rows with NaN lag features (early history)")

    return df


def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add time-based features derived from date."""
    df['day_of_week'] = df['date'].dt.weekday
    df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
    df['month'] = df['date'].dt.month
    first_seen = df.groupby('item_id')['date'].transform('min')
    df['days_since_launch'] = (df['date'] - first_seen).dt.days
    return df


def _add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add lag and rolling features per menu item (uses volume_sold)."""
    df = df.sort_values(['item_id', 'date']).reset_index(drop=True)
    group = df.groupby('item_id')['volume_sold']

    df['lag_1'] = group.shift(1)
    df['lag_7'] = group.shift(7)
    df['rolling_mean_7'] = group.transform(
        lambda x: x.shift(1).rolling(window=7, min_periods=1).mean()
    )
    df['rolling_mean_14'] = group.transform(
        lambda x: x.shift(1).rolling(window=14, min_periods=1).mean()
    )
    df['rolling_trend_3'] = group.transform(
        lambda x: x.shift(1).rolling(3, min_periods=1).mean() -
                  x.shift(4).rolling(3, min_periods=1).mean()
    )
    return df


def _add_store_context_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Store-wide volume signals EXCLUDING the current item to avoid leakage.
    store_volume_excluding_item = total_store_volume - item_volume
    Without this, the model learns identity (high store total → high item) because
    the item's own volume is part of the store total.
    """
    daily_total = df.groupby('date')['volume_sold'].sum()
    store_last3 = daily_total.shift(1).rolling(3, min_periods=1).sum()
    store_last7 = daily_total.shift(1).rolling(7, min_periods=1).sum()

    store_df = pd.DataFrame({
        'store_total_last3': store_last3,
        'store_total_last7': store_last7,
    })
    store_df.index.name = 'date'
    store_df = store_df.reset_index()

    df = df.merge(store_df, on='date', how='left')
    df['store_total_last3'] = df['store_total_last3'].fillna(0)
    df['store_total_last7'] = df['store_total_last7'].fillna(0)

    # Per-item contribution to those windows (exclude from store total to prevent leakage)
    df = df.sort_values(['item_id', 'date']).reset_index(drop=True)
    grp = df.groupby('item_id')['volume_sold']
    item_last3 = grp.transform(lambda x: x.shift(1).rolling(3, min_periods=1).sum())
    item_last7 = grp.transform(lambda x: x.shift(1).rolling(7, min_periods=1).sum())

    df['store_total_last3'] = np.maximum(0, df['store_total_last3'] - item_last3.fillna(0))
    df['store_total_last7'] = np.maximum(0, df['store_total_last7'] - item_last7.fillna(0))
    return df


def _add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """Price-relative features. Also add item_median_volume for target normalization."""
    df['item_median_price'] = df.groupby('item_id')['price'].transform('median')
    df['item_median_volume'] = df.groupby('item_id')['volume_sold'].transform(
        lambda x: x[x > 0].median()
    )
    df['item_median_volume'] = df['item_median_volume'].fillna(1.0).clip(lower=1e-6)
    df['price_ratio'] = np.where(
        df['item_median_price'] > 0,
        df['price'] / df['item_median_price'],
        1.0,
    )
    return df


def _add_cold_start_features(df: pd.DataFrame) -> pd.DataFrame:
    """Category/global priors for new menu items."""
    daily_global = df.groupby('date')['volume_sold'].mean()
    global_avg = daily_global.shift(1).rolling(7, min_periods=1).mean()
    global_df = pd.DataFrame({'global_avg_last7': global_avg})
    global_df.index.name = 'date'
    global_df = global_df.reset_index()
    df = df.merge(global_df, on='date', how='left')
    df['global_avg_last7'] = df['global_avg_last7'].fillna(0)

    if 'category' in df.columns:
        cat_daily = (
            df.groupby(['date', 'category'])['volume_sold']
            .mean()
            .reset_index()
            .sort_values(['category', 'date'])
        )
        cat_daily['category_avg_last7'] = cat_daily.groupby('category')[
            'volume_sold'
        ].transform(lambda x: x.shift(1).rolling(7, min_periods=1).mean())
        df = df.merge(
            cat_daily[['date', 'category', 'category_avg_last7']],
            on=['date', 'category'],
            how='left',
        )
    else:
        df['category_avg_last7'] = df['global_avg_last7']

    df['category_avg_last7'] = (
        df['category_avg_last7'].fillna(df['global_avg_last7']).fillna(0)
    )

    cold_mask = df['days_since_launch'] < 3
    n_cold = cold_mask.sum()
    if n_cold > 0:
        lag_cols = ['lag_1', 'lag_7', 'rolling_mean_7', 'rolling_mean_14', 'rolling_trend_3']
        for col in lag_cols:
            if col in df.columns:
                df.loc[cold_mask, col] = df.loc[cold_mask, 'category_avg_last7']
        logger.info(f"Cold start: replaced lag features for {n_cold} rows (items with < 3 days history)")

    return df


def get_feature_columns() -> List[str]:
    """Return the ordered list of feature columns used by the model."""
    return [
        'day_of_week', 'is_weekend', 'month', 'days_since_launch',
        'lag_1', 'lag_7', 'rolling_mean_7', 'rolling_mean_14', 'rolling_trend_3',
        'temperature', 'rain',
        'price', 'item_median_price', 'price_ratio',
        'category_avg_last7', 'global_avg_last7',
        'store_total_last3', 'store_total_last7',
        'temp_weekend',
    ]


def prepare_train_data(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """
    Prepare training data for classifier and regressor.

    Target normalization: regressor trains on volume_sold / item_median_volume to avoid
    scale instability (Brownie 3–12 units vs Ice cream 20k–200k mg). Predictions are
    denormalized at inference time.

    Returns:
        X_clf, y_clf: Features and binary target (volume_sold > 0).
        X_reg, y_reg: Features and NORMALIZED volume target (only rows where volume > 0).
    """
    feature_cols = get_feature_columns()
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    X = df[feature_cols].copy()
    y_clf = (df['volume_sold'] > 0).astype(int)

    mask_positive = df['volume_sold'] > 0
    X_reg = X[mask_positive].copy()
    # Normalize target: volume_sold / item_median_volume (avoids scale instability)
    scale = df.loc[mask_positive, 'item_median_volume'].values
    y_reg = (df.loc[mask_positive, 'volume_sold'].values / scale).astype(float)

    logger.info(f"Volume classification: {len(X)} rows ({y_clf.sum()} positive)")
    logger.info(f"Volume regression: {len(X_reg)} rows (normalized, mean={y_reg.mean():.3f})")

    return X, y_clf, X_reg, y_reg
