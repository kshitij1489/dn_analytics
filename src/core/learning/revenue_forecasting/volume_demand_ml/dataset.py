"""
Volume Demand ML — Dataset Loader

Loads and validates menu-item-level volume data for forecasting.
Expects columns: date, item_id, item_name, unit, volume_sold,
                 temperature, rain, weekday, is_weekend
Entity = menu_item_id (same as item demand). Target = volume_sold (float).
"""
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


REQUIRED_COLUMNS = ['date', 'item_id', 'volume_sold']
OPTIONAL_COLUMNS = {
    'item_name': 'Unknown',
    'unit': 'mg',
    'category': 'Unknown',
    'price': 0.0,
    'temperature': None,
    'rain': 0.0,
    # weekday, is_weekend: derived from date below (no warning)
}


def load_volume_sales(
    df: Optional[pd.DataFrame] = None,
    csv_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load menu-item-level volume sales data from a DataFrame or CSV file.

    Args:
        df: Pre-loaded DataFrame with volume sales data.
        csv_path: Path to a CSV file (used only if df is None).

    Returns:
        Cleaned DataFrame with all expected columns, sorted by (date, item_id).

    Raises:
        ValueError: If required columns are missing.
    """
    if df is None and csv_path is not None:
        logger.info(f"Loading volume sales from CSV: {csv_path}")
        df = pd.read_csv(csv_path, parse_dates=['date'])
    elif df is None:
        raise ValueError("Either df or csv_path must be provided.")
    else:
        df = df.copy()

    # Validate required columns
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Ensure date is datetime
    df['date'] = pd.to_datetime(df['date'])

    # Fill optional columns with defaults if missing
    for col, default in OPTIONAL_COLUMNS.items():
        if col not in df.columns:
            logger.warning(f"Column '{col}' not found — filling with default: {default}")
            df[col] = default

    # Derive weekday / is_weekend from date (DB query never includes these)
    df['weekday'] = df['date'].dt.weekday
    df['is_weekend'] = df['date'].dt.weekday.isin([5, 6]).astype(int)

    # Ensure numeric types (volume_sold is float for mg/units)
    df['volume_sold'] = pd.to_numeric(df['volume_sold'], errors='coerce').fillna(0).astype(float)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0.0)
    df['temperature'] = pd.to_numeric(df['temperature'], errors='coerce')
    df['rain'] = pd.to_numeric(df['rain'], errors='coerce').fillna(0.0)

    # Fill missing temperature with global rolling mean (fallback)
    if df['temperature'].isna().any():
        temp_mean = df['temperature'].mean()
        fallback = temp_mean if pd.notna(temp_mean) else 25.0
        n_missing = df['temperature'].isna().sum()
        df['temperature'] = df['temperature'].fillna(fallback)
        logger.info(f"Filled {n_missing} missing temperature values with {fallback:.1f}")

    df = df.sort_values(['date', 'item_id']).reset_index(drop=True)
    logger.info(f"Volume dataset loaded: {len(df)} rows, {df['item_id'].nunique()} items, "
                f"{df['date'].min().date()} to {df['date'].max().date()}")
    return df


def densify_daily_grid(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expand sparse volume data into a complete (date × item) daily grid.

    The raw sales query only returns rows where a menu item had volume sold.
    This function adds explicit zero-volume rows for every (date, item_id) pair
    from each item's first appearance through the last date in the data.

    Args:
        df: Sparse sales DataFrame with at least [date, item_id, volume_sold].

    Returns:
        Dense DataFrame with zero-filled rows for missing (date, item_id) pairs.
    """
    if df.empty or len(df) < 2:
        return df

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    all_dates = pd.date_range(df['date'].min(), df['date'].max(), freq='D')
    dates_df = pd.DataFrame({'date': all_dates})

    # Item metadata
    agg_dict = {'date': 'min'}
    for col in ['item_name', 'unit', 'category']:
        if col in df.columns:
            agg_dict[col] = 'first'

    item_info = df.groupby('item_id').agg(agg_dict).reset_index()
    item_info = item_info.rename(columns={'date': '_first_seen'})

    if 'price' in df.columns:
        latest_price = (
            df.sort_values('date')
            .groupby('item_id')['price']
            .last()
            .reset_index()
        )
        item_info = item_info.merge(latest_price, on='item_id', how='left')

    grid = (
        item_info.assign(_key=1)
        .merge(dates_df.assign(_key=1), on='_key')
        .drop('_key', axis=1)
    )
    grid = grid[grid['date'] >= grid['_first_seen']].copy()
    grid = grid.drop(columns=['_first_seen'])

    sales = df[['date', 'item_id', 'volume_sold']].drop_duplicates(['date', 'item_id'])
    grid = grid.merge(sales, on=['date', 'item_id'], how='left')
    grid['volume_sold'] = grid['volume_sold'].fillna(0).astype(float)

    for weather_col, default in [('temperature', 25.0), ('rain', 0.0)]:
        if weather_col in df.columns:
            date_weather = (
                df.groupby('date')[weather_col]
                .first()
                .reindex(all_dates)
                .ffill().bfill()
                .fillna(default)
                .reset_index()
            )
            date_weather.columns = ['date', weather_col]
            grid = grid.merge(date_weather, on='date', how='left')

    grid = grid.sort_values(['date', 'item_id']).reset_index(drop=True)
    grid['weekday'] = grid['date'].dt.weekday
    grid['is_weekend'] = (grid['weekday'] >= 5).astype(int)

    n_total = len(grid)
    n_zero = (grid['volume_sold'] == 0).sum()
    logger.info(
        f"Densified volume grid: {n_total} rows "
        f"({df['item_id'].nunique()} items × up to {len(all_dates)} days, "
        f"zero-fill rate: {n_zero / n_total:.1%})"
    )
    return grid
