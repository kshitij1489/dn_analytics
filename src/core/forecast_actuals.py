"""
Local order actuals for forecast chart history lines (blue actuals).

No ML dependencies — used by central forecast projection routers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from src.core.utils.business_date import BUSINESS_DATE_SQL, get_current_business_date
from src.core.utils.weather_helpers import get_rain_cat


def build_revenue_history_rows(conn, today_date, days: int = 30) -> List[dict]:
    """Daily revenue + weather actuals for the revenue forecast chart."""
    today_str = get_current_business_date()
    end_dt = today_date + timedelta(days=1)
    start_dt = end_dt - timedelta(days=90 + 1)

    query = f"""
        SELECT
            {BUSINESS_DATE_SQL} as ds,
            SUM(o.total) as y,
            COUNT(*) as orders,
            COALESCE(w.temp_max, 25.0) as temp_max,
            COALESCE(w.rain_sum, 0) as rain_sum
        FROM orders o
        LEFT JOIN weather_daily w ON {BUSINESS_DATE_SQL} = w.date AND w.city = 'Gurugram'
        WHERE o.order_status = 'Success'
          AND {BUSINESS_DATE_SQL} >= ?
          AND {BUSINESS_DATE_SQL} < ?
        GROUP BY {BUSINESS_DATE_SQL}
        ORDER BY ds
    """
    rows = conn.execute(query, (start_dt.isoformat(), end_dt.isoformat())).fetchall()
    if not rows:
        return []
    df = pd.DataFrame(rows, columns=["ds", "y", "orders", "temp_max", "rain_sum"])
    df["ds"] = pd.to_datetime(df["ds"])
    df_history = df[df["ds"].dt.date < today_date]
    history_window = df_history[
        df_history["ds"] >= pd.Timestamp(today_date - timedelta(days=days))
    ]
    return [
        {
            "sale_date": row["ds"].strftime("%Y-%m-%d"),
            "revenue": float(row["y"]),
            "orders": int(row["orders"]),
            "temp_max": float(row["temp_max"]),
            "rain_category": get_rain_cat(float(row["rain_sum"])),
        }
        for _, row in history_window.iterrows()
    ]


def _item_history_query(conn, params: list, item_filter: str) -> pd.DataFrame:
    query = f"""
        SELECT
            {BUSINESS_DATE_SQL} as date,
            mi.menu_item_id as item_id,
            mi.name as item_name,
            mi.type as category,
            oi.unit_price as price,
            SUM(oi.quantity) as quantity_sold
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        WHERE o.order_status = 'Success'
          AND {BUSINESS_DATE_SQL} >= ?
          AND {BUSINESS_DATE_SQL} <= ?
          {item_filter}
        GROUP BY {BUSINESS_DATE_SQL}, mi.menu_item_id
        ORDER BY date, item_id
    """
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["quantity_sold"] = pd.to_numeric(df["quantity_sold"], errors="coerce").fillna(0).astype(int)
    df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0)
    return df


def get_item_history_dataframe(
    conn,
    *,
    item_id: Optional[str] = None,
    days: int = 90,
) -> pd.DataFrame:
    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
    start_dt = today_date - timedelta(days=days)
    item_filter = "AND mi.menu_item_id = ?" if item_id else ""
    params = [start_dt.isoformat(), today_str]
    if item_id:
        params.append(item_id)
    return _item_history_query(conn, params, item_filter)


def build_item_history_rows(
    conn,
    today_date,
    *,
    item_id: Optional[str] = None,
    history_days: int = 30,
) -> tuple[list[dict], list[dict]]:
    """
    Returns (items_list, history_rows) for item demand charts.
    Densifies zero-sale days for active items (sold in last 14 days).
    """
    df_history_all = get_item_history_dataframe(conn, item_id=item_id, days=90)
    df_history = df_history_all[df_history_all["date"] < pd.Timestamp(today_date)].copy()
    if df_history.empty:
        return [], []

    cutoff = today_date - timedelta(days=14)
    active_items = set(
        df_history[df_history["date"] >= pd.Timestamp(cutoff)]["item_id"].unique()
    )
    df_active = df_history[df_history["item_id"].isin(active_items)]
    items_info = df_active.groupby("item_id").agg({"item_name": "first"}).reset_index()
    items_list = [
        {"item_id": row["item_id"], "item_name": row["item_name"]}
        for _, row in items_info.iterrows()
    ]

    hist_start = pd.Timestamp(today_date - timedelta(days=history_days))
    df_hist_recent = df_history[df_history["date"] >= hist_start]
    hist_grid_dates = pd.date_range(hist_start, today_date - timedelta(days=1), freq="D")
    if hist_grid_dates.empty or items_info.empty:
        return items_list, []

    hist_bloat = items_info[["item_id", "item_name"]].assign(key=1).merge(
        pd.DataFrame({"date": hist_grid_dates, "key": 1}), on="key"
    ).drop("key", axis=1)
    df_hist_final = hist_bloat.merge(
        df_hist_recent[["date", "item_id", "quantity_sold"]],
        on=["date", "item_id"],
        how="left",
    )
    df_hist_final["quantity_sold"] = df_hist_final["quantity_sold"].fillna(0).astype(int)
    history_rows = [
        {
            "date": row["date"].strftime("%Y-%m-%d"),
            "item_id": row["item_id"],
            "qty": int(row["quantity_sold"]),
        }
        for _, row in df_hist_final.iterrows()
    ]
    return items_list, history_rows


def _get_item_unit_from_variants(conn, item_id: str) -> str:
    row = conn.execute(
        """
        SELECT DISTINCT UPPER(COALESCE(v.unit, 'G')) as unit
        FROM variants v
        JOIN menu_item_variants miv ON v.variant_id = miv.variant_id
        WHERE miv.menu_item_id = ?
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    if not row:
        return "g"
    unit = str(row[0] or "G").upper()
    if unit == "COUNT":
        return "units"
    return "g"


def get_volume_history_dataframe(
    conn,
    *,
    item_id: Optional[str] = None,
    days: int = 90,
) -> pd.DataFrame:
    today_str = get_current_business_date()
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()
    start_dt = today_date - timedelta(days=days)
    item_filter = "AND mi.menu_item_id = ?" if item_id else ""
    params = [start_dt.isoformat(), today_str]
    if item_id:
        params.append(item_id)

    query = f"""
        SELECT
            {BUSINESS_DATE_SQL} as date,
            mi.menu_item_id as item_id,
            mi.name as item_name,
            SUM(
                CASE
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'COUNT' THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'ML' THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) IN ('GMS', 'G') THEN oi.quantity * COALESCE(v.value, 1)
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'KG' THEN oi.quantity * COALESCE(v.value, 1) * 1000
                    WHEN UPPER(COALESCE(v.unit, 'MG')) = 'MG' THEN oi.quantity * COALESCE(v.value, 1) / 1000.0
                    ELSE oi.quantity * COALESCE(v.value, 1) / 1000.0
                END
            ) as volume_sold
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        JOIN variants v ON oi.variant_id = v.variant_id
        WHERE o.order_status = 'Success'
          AND oi.menu_item_id IS NOT NULL
          AND oi.variant_id IS NOT NULL
          AND {BUSINESS_DATE_SQL} >= ?
          AND {BUSINESS_DATE_SQL} <= ?
          {item_filter}
        GROUP BY {BUSINESS_DATE_SQL}, mi.menu_item_id
        ORDER BY date, item_id
    """
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["volume_sold"] = pd.to_numeric(df["volume_sold"], errors="coerce").fillna(0).astype(float)
    return df


def build_volume_history_rows(
    conn,
    today_date,
    *,
    item_id: Optional[str] = None,
    history_days: int = 30,
) -> tuple[list[dict], list[dict]]:
    """Returns (items_list, history_rows) for volume charts."""
    df_history_all = get_volume_history_dataframe(conn, item_id=item_id, days=90)
    df_history = df_history_all[df_history_all["date"] < pd.Timestamp(today_date)].copy()
    if df_history.empty:
        return [], []

    cutoff = today_date - timedelta(days=14)
    active_items = set(
        df_history[df_history["date"] >= pd.Timestamp(cutoff)]["item_id"].unique()
    )
    df_active = df_history[df_history["item_id"].isin(active_items)]
    items_info = df_active.groupby("item_id").agg({"item_name": "first"}).reset_index()
    items_info["unit"] = items_info["item_id"].apply(lambda x: _get_item_unit_from_variants(conn, x))
    items_list = [
        {"item_id": r["item_id"], "item_name": r["item_name"], "unit": r["unit"]}
        for _, r in items_info.iterrows()
    ]

    hist_start = pd.Timestamp(today_date - timedelta(days=history_days))
    df_hist_recent = df_history[df_history["date"] >= hist_start]
    hist_grid_dates = pd.date_range(hist_start, today_date - timedelta(days=1), freq="D")
    if hist_grid_dates.empty or items_info.empty:
        return items_list, []

    hist_bloat = items_info[["item_id", "item_name", "unit"]].assign(key=1).merge(
        pd.DataFrame({"date": hist_grid_dates, "key": 1}), on="key"
    ).drop("key", axis=1)
    df_hist_final = hist_bloat.merge(
        df_hist_recent[["date", "item_id", "volume_sold"]],
        on=["date", "item_id"],
        how="left",
    )
    df_hist_final["volume_sold"] = df_hist_final["volume_sold"].fillna(0).astype(float)
    history_rows = [
        {
            "date": row["date"].strftime("%Y-%m-%d"),
            "item_id": row["item_id"],
            "volume": float(row["volume_sold"]),
        }
        for _, row in df_hist_final.iterrows()
    ]
    return items_list, history_rows
