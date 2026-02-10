"""
Item Demand ML — Feature Engineering

Builds time, lag, weather, price, and context features for global demand model.
All features are computed per-item using groupby to preserve temporal ordering.
"""
import logging
from typing import List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_features(df: pd.DataFrame, is_future: bool = False) -> pd.DataFrame:
    """
    Build all features from raw item-sales DataFrame.

    Args:
        df: DataFrame with columns [date, item_id, quantity_sold, temperature, rain, ...].
            Must be sorted by (date, item_id).
        is_future: If True, skip lag feature NaN dropping (lags are pre-filled).

    Returns:
        DataFrame with all original columns plus engineered features.
        Rows with NaN lags (early history) are dropped unless is_future=True.
    """
    df = df.copy()

    # ---- Time Features ----
    df = _add_time_features(df)

    # ---- Weather Features (already present, just ensure types) ----
    df['temperature'] = pd.to_numeric(df['temperature'], errors='coerce').fillna(25.0)
    df['rain'] = pd.to_numeric(df['rain'], errors='coerce').fillna(0.0)

    # ---- PART 6: Store-level demand context (computed before per-item features) ----
    df = _add_store_context_features(df)

    # ---- Lag Features (per item) ----
    df = _add_lag_features(df)

    # ---- PART 5: Price sensitivity features ----
    df = _add_price_features(df)

    # ---- PART 4: Cold start — category/global priors for new items ----
    df = _add_cold_start_features(df)

    # ---- PART 7: Weekend × temperature interaction ----
    df['temp_weekend'] = df['temperature'] * df['is_weekend']

    # ---- Drop rows with NaN lags (only for training, not future prediction) ----
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

    # Days since first appearance of each item (proxy for item maturity)
    first_seen = df.groupby('item_id')['date'].transform('min')
    df['days_since_launch'] = (df['date'] - first_seen).dt.days

    return df


def _add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add lag and rolling features per item.

    IMPORTANT: shift(N) produces calendar-day lags ONLY because the input
    has been densified via densify_daily_grid() — i.e. there is exactly one
    row per (item, calendar_day).  If the input is sparse (sale-event rows
    only), shift(1) would mean "previous sale event", NOT "yesterday".
    """
    df = df.sort_values(['item_id', 'date']).reset_index(drop=True)

    group = df.groupby('item_id')['quantity_sold']

    df['lag_1'] = group.shift(1)
    df['lag_7'] = group.shift(7)

    # Rolling statistics (use min_periods=1 to maximize data, shift to avoid leakage)
    df['rolling_mean_7'] = group.transform(
        lambda x: x.shift(1).rolling(window=7, min_periods=1).mean()
    )
    df['rolling_mean_14'] = group.transform(
        lambda x: x.shift(1).rolling(window=14, min_periods=1).mean()
    )

    # Rolling trend: mean of last 3 days minus mean of previous 3 days (momentum)
    df['rolling_trend_3'] = group.transform(
        lambda x: x.shift(1).rolling(3, min_periods=1).mean() -
                  x.shift(4).rolling(3, min_periods=1).mean()
    )

    return df


# ---------------------------------------------------------------------------
# PART 6: Store-level demand context
# Ice cream demand is mood-driven — a busy store day boosts all items.
# These date-level features capture overall store traffic.
# ---------------------------------------------------------------------------

def _add_store_context_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add store-wide demand signals (same value for all items on a given date)."""
    # Daily total quantity across ALL items
    daily_total = df.groupby('date')['quantity_sold'].sum()

    # Shifted rolling sums — shift(1) avoids leakage from today's sales
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

    return df


# ---------------------------------------------------------------------------
# PART 5: Price sensitivity
# Captures whether current price is above/below the item's typical price,
# which happens during discounts or menu price updates.
# ---------------------------------------------------------------------------

def _add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add price-relative features to detect discounts / price changes."""
    # Item median price: static per item across all available history
    df['item_median_price'] = df.groupby('item_id')['price'].transform('median')

    # Price ratio: current price relative to item's typical price
    # >1 = premium/increase, <1 = discount, 1 = normal
    df['price_ratio'] = np.where(
        df['item_median_price'] > 0,
        df['price'] / df['item_median_price'],
        1.0,  # default for free/zero-priced items
    )

    return df


# ---------------------------------------------------------------------------
# PART 4: Cold start handling
# New items have no lag history → predictions collapse to near-zero.
# We compute category-level and global priors, then substitute them for
# items with fewer than 3 days of history.
# ---------------------------------------------------------------------------

def _add_cold_start_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add category/global priors and patch lag features for new items."""
    # ---- Global average last 7 days (mean daily qty across all items) ----
    daily_global = df.groupby('date')['quantity_sold'].mean()
    global_avg = daily_global.shift(1).rolling(7, min_periods=1).mean()
    global_df = pd.DataFrame({'global_avg_last7': global_avg})
    global_df.index.name = 'date'
    global_df = global_df.reset_index()
    df = df.merge(global_df, on='date', how='left')
    df['global_avg_last7'] = df['global_avg_last7'].fillna(0)

    # ---- Category average last 7 days ----
    if 'category' in df.columns:
        cat_daily = (
            df.groupby(['date', 'category'])['quantity_sold']
            .mean()
            .reset_index()
            .sort_values(['category', 'date'])
        )
        cat_daily['category_avg_last7'] = cat_daily.groupby('category')[
            'quantity_sold'
        ].transform(lambda x: x.shift(1).rolling(7, min_periods=1).mean())
        df = df.merge(
            cat_daily[['date', 'category', 'category_avg_last7']],
            on=['date', 'category'],
            how='left',
        )
    else:
        # No category info — fall back to global average
        df['category_avg_last7'] = df['global_avg_last7']

    # Fill any remaining NaN with global average, then 0
    df['category_avg_last7'] = (
        df['category_avg_last7'].fillna(df['global_avg_last7']).fillna(0)
    )

    # ---- Cold start replacement ----
    # For items with < 3 days of history, lag features are unreliable.
    # Replace them with the category average as a better prior than 0/NaN.
    cold_mask = df['days_since_launch'] < 3
    n_cold = cold_mask.sum()
    if n_cold > 0:
        lag_cols = ['lag_1', 'lag_7', 'rolling_mean_7', 'rolling_mean_14', 'rolling_trend_3']
        for col in lag_cols:
            if col in df.columns:
                df.loc[cold_mask, col] = df.loc[cold_mask, 'category_avg_last7']
        logger.info(
            f"Cold start: replaced lag features for {n_cold} rows "
            f"(items with < 3 days history)"
        )

    return df


def get_feature_columns() -> List[str]:
    """Return the ordered list of feature columns used by the model."""
    return [
        # Time
        'day_of_week',
        'is_weekend',
        'month',
        'days_since_launch',
        # Lag / rolling (per item)
        'lag_1',
        'lag_7',
        'rolling_mean_7',
        'rolling_mean_14',
        'rolling_trend_3',
        # Weather
        'temperature',
        'rain',
        # Price
        'price',
        'item_median_price',   # PART 5: item's typical price level
        'price_ratio',         # PART 5: current price vs typical (discount detector)
        # Cold start priors (PART 4)
        'category_avg_last7',
        'global_avg_last7',
        # Store context (PART 6)
        'store_total_last3',
        'store_total_last7',
        # Interaction (PART 7)
        'temp_weekend',        # PART 7: temperature × is_weekend
    ]


def prepare_train_data(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    """
    Prepare training data for both classifier and regressor.

    Args:
        df: Feature-engineered DataFrame (output of build_features).

    Returns:
        X_clf, y_clf: Features and binary target for classification (sold > 0).
        X_reg, y_reg: Features and quantity target for regression (only rows where qty > 0).
    """
    feature_cols = get_feature_columns()

    # Ensure all feature columns exist
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    X = df[feature_cols].copy()

    # Classification: did it sell?
    y_clf = (df['quantity_sold'] > 0).astype(int)

    # Regression: how much? (only positive sales)
    mask_positive = df['quantity_sold'] > 0
    X_reg = X[mask_positive].copy()
    y_reg = df.loc[mask_positive, 'quantity_sold'].copy()

    logger.info(f"Classification data: {len(X)} rows ({y_clf.sum()} positive, "
                f"{(~y_clf.astype(bool)).sum()} zero)")
    logger.info(f"Regression data: {len(X_reg)} rows (mean qty={y_reg.mean():.1f})")

    return X, y_clf, X_reg, y_reg
