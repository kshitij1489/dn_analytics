"""
Item Demand ML — Dataset Loader

Loads and validates item-level sales data for demand forecasting.
Expects columns: date, item_id, item_name, category, price, quantity_sold,
                 temperature, rain, weekday, is_weekend
Handles missing columns gracefully with sensible defaults.
"""
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


REQUIRED_COLUMNS = ['date', 'item_id', 'quantity_sold']
OPTIONAL_COLUMNS = {
    'item_name': 'Unknown',
    'category': 'Unknown',
    'price': 0.0,
    'temperature': None,   # Will be filled with rolling mean later
    'rain': 0.0,
    'weekday': None,       # Will be derived from date
    'is_weekend': None,    # Will be derived from date
}


def load_item_sales(
    df: Optional[pd.DataFrame] = None,
    csv_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load item-level sales data from a DataFrame or CSV file.

    Args:
        df: Pre-loaded DataFrame with item sales data.
        csv_path: Path to a CSV file (used only if df is None).

    Returns:
        Cleaned DataFrame with all expected columns, sorted by (date, item_id).

    Raises:
        ValueError: If required columns are missing.
    """
    if df is None and csv_path is not None:
        logger.info(f"Loading item sales from CSV: {csv_path}")
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

    # Derive weekday / is_weekend from date if not supplied or null
    if df['weekday'].isna().all():
        df['weekday'] = df['date'].dt.weekday
    if df['is_weekend'].isna().all():
        df['is_weekend'] = df['date'].dt.weekday.isin([5, 6]).astype(int)

    # Ensure numeric types
    df['quantity_sold'] = pd.to_numeric(df['quantity_sold'], errors='coerce').fillna(0).astype(int)
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
    logger.info(f"Dataset loaded: {len(df)} rows, {df['item_id'].nunique()} items, "
                f"{df['date'].min().date()} to {df['date'].max().date()}")
    return df


def densify_daily_grid(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expand sparse sales data into a complete (date × item) daily grid.

    The raw sales query only returns rows where an item was actually sold.
    This function adds explicit zero-sales rows for every (date, item) pair
    from each item's first appearance through the last date in the data.

    This is critical for training — without zero-sales rows the classifier
    sees only positive examples and becomes degenerate.

    Args:
        df: Sparse sales DataFrame with at least [date, item_id, quantity_sold].

    Returns:
        Dense DataFrame with zero-filled rows for missing (date, item) pairs.
        Weather columns are forward/backward filled per date.
    """
    if df.empty or len(df) < 2:
        return df

    df = df.copy()
    df['date'] = pd.to_datetime(df['date'])

    all_dates = pd.date_range(df['date'].min(), df['date'].max(), freq='D')
    dates_df = pd.DataFrame({'date': all_dates})

    # ---- Item metadata (carried to all rows for that item) ----
    agg_dict = {'date': 'min'}  # first-seen date
    for col in ['item_name', 'category']:
        if col in df.columns:
            agg_dict[col] = 'first'

    item_info = df.groupby('item_id').agg(agg_dict).reset_index()
    item_info = item_info.rename(columns={'date': '_first_seen'})

    # Price: use the most recent known price per item
    if 'price' in df.columns:
        latest_price = (
            df.sort_values('date')
            .groupby('item_id')['price']
            .last()
            .reset_index()
        )
        item_info = item_info.merge(latest_price, on='item_id', how='left')

    # ---- Cross join: items × all dates ----
    grid = (
        item_info.assign(_key=1)
        .merge(dates_df.assign(_key=1), on='_key')
        .drop('_key', axis=1)
    )

    # Only keep rows from each item's first appearance onward
    grid = grid[grid['date'] >= grid['_first_seen']].copy()
    grid = grid.drop(columns=['_first_seen'])

    # ---- Merge actual quantities (zero-fill missing) ----
    sales = df[['date', 'item_id', 'quantity_sold']].drop_duplicates(['date', 'item_id'])
    grid = grid.merge(sales, on=['date', 'item_id'], how='left')
    grid['quantity_sold'] = grid['quantity_sold'].fillna(0).astype(int)

    # ---- Weather: date-level, forward/backward filled ----
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

    # Derive weekday / is_weekend so zero-filled rows carry correct time features
    grid['weekday'] = grid['date'].dt.weekday
    grid['is_weekend'] = (grid['weekday'] >= 5).astype(int)

    n_total = len(grid)
    n_zero = (grid['quantity_sold'] == 0).sum()
    logger.info(
        f"Densified grid: {n_total} rows "
        f"({df['item_id'].nunique()} items × up to {len(all_dates)} days, "
        f"zero-fill rate: {n_zero / n_total:.1%})"
    )

    return grid


def generate_sample_data(n_items: int = 10, n_days: int = 180) -> pd.DataFrame:
    """
    Generate synthetic item-level sales data for testing.

    Args:
        n_items: Number of distinct menu items.
        n_days: Number of days of history.

    Returns:
        DataFrame matching the expected schema.
    """
    import numpy as np

    np.random.seed(42)
    dates = pd.date_range('2025-01-01', periods=n_days, freq='D')

    items = []
    for i in range(1, n_items + 1):
        category = ['ice_cream', 'shake', 'sundae', 'waffle'][i % 4]
        price = round(np.random.uniform(80, 350), 0)
        base_demand = np.random.uniform(3, 15)

        for d in dates:
            doy = d.dayofyear
            temp = 15 + 25 * np.sin(2 * np.pi * (doy - 100) / 365) + np.random.normal(0, 2)
            rain = max(0, np.random.choice([0, 0, 0, 0, 0, 2, 5, 10]))

            # Demand driven by temperature, weekday, randomness
            seasonal = 1 + 0.5 * np.sin(2 * np.pi * (doy - 100) / 365)
            weekend_boost = 1.3 if d.weekday() >= 5 else 1.0
            rain_penalty = 0.7 if rain > 3 else 1.0

            expected = base_demand * seasonal * weekend_boost * rain_penalty
            qty = max(0, int(np.random.poisson(max(0, expected))))

            items.append({
                'date': d,
                'item_id': f'ITEM_{i:03d}',
                'item_name': f'Item {i} ({category.title()})',
                'category': category,
                'price': price,
                'quantity_sold': qty,
                'temperature': round(temp, 1),
                'rain': rain,
                'weekday': d.weekday(),
                'is_weekend': int(d.weekday() >= 5),
            })

    return pd.DataFrame(items)
