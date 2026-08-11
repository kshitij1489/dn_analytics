"""
Insights Router - Analytics and KPI endpoints

Provides endpoints for dashboard KPIs, sales trends, revenue breakdowns, etc.

Every endpoint declares how its numbers combine in All Stores mode: true counts
and money are summed, and every average/share is recomputed from the combined
numerator and denominator (plan §7.1). Nothing averages store-level averages.
"""

import pandas as pd
from fastapi import APIRouter, Depends

from src.api.dependencies import ScopedReader, get_reader
from src.api.utils import df_to_json
from src.core.queries import insights_queries
from src.core.queries.multi_store_reducers import (
    Ratio,
    Sum,
    group_rows,
    reduce_mapping,
)

router = APIRouter()


def _rows_only(pairs):
    return [value for _, value in pairs]


@router.get("/kpis")
def get_kpis(reader: ScopedReader = Depends(get_reader)):
    """Get key performance indicators for the dashboard"""

    def query(conn, _profile):
        data = insights_queries.fetch_kpis(conn)
        return dict(data) if data else {}

    def reduce(pairs):
        return reduce_mapping(
            _rows_only(pairs),
            {
                "total_orders": Sum(),
                "total_revenue": Sum(),
                # Portfolio AOV = combined revenue / combined orders.
                "avg_order_value": Ratio("total_revenue", "total_orders", digits=None),
                "verified_orders": Sum(),
                "verified_customers": Sum(),
                "today_revenue": Sum(),
                # Customers are profile-qualified in Phase 2, so store-customer
                # records add up rather than being deduplicated across stores.
                "total_customers_estimate_low": Sum(),
                "total_customers_estimate_high": Sum(),
            },
        )

    return reader.read(query, reduce)


@router.get("/daily_sales")
def get_daily_sales(reader: ScopedReader = Depends(get_reader)):
    """Get daily sales data"""

    def query(conn, _profile):
        return df_to_json(insights_queries.fetch_daily_sales(conn))

    def reduce(pairs):
        return group_rows(
            pairs,
            group_by=("order_date",),
            spec={
                "total_revenue": Sum(),
                "net_revenue": Sum(),
                "tax_collected": Sum(),
                "total_orders": Sum(),
                "Website Revenue": Sum(),
                "POS Revenue": Sum(),
                "Swiggy Revenue": Sum(),
                "Zomato Revenue": Sum(),
            },
            sort_by="order_date",
            descending=True,
        )

    return reader.read(query, reduce)


@router.get("/sales_trend")
def get_sales_trend(reader: ScopedReader = Depends(get_reader)):
    """Get sales trend over time"""

    def query(conn, _profile):
        return df_to_json(insights_queries.fetch_sales_trend(conn))

    def reduce(pairs):
        return group_rows(
            pairs,
            group_by=("date",),
            spec={"revenue": Sum(), "num_orders": Sum()},
            sort_by="date",
            descending=False,
        )

    return reader.read(query, reduce)


@router.get("/category_trend")
def get_category_trend(reader: ScopedReader = Depends(get_reader)):
    """Get sales trend by category"""

    def query(conn, _profile):
        return df_to_json(insights_queries.fetch_category_trend(conn))

    def reduce(pairs):
        # Categories are durable display dimensions, so they combine across
        # stores; transient menu_item_id never does (plan §7.3).
        return group_rows(
            pairs,
            group_by=("date", "category"),
            spec={"revenue": Sum()},
            sort_by="date",
            descending=False,
        )

    return reader.read(query, reduce)


@router.get("/top_items")
def top_items(
    start_date: str = None,
    end_date: str = None,
    reader: ScopedReader = Depends(get_reader),
):
    """Get top selling items with revenue data. Optional start_date/end_date = business days (5:00 AM–4:59:59 AM IST)."""

    def query(conn, _profile):
        # All Stores ranks after combining, so it needs every item, not each
        # store's own top ten.
        df, total_revenue = insights_queries.fetch_top_items_data(
            conn,
            start_date=start_date,
            end_date=end_date,
            limit=None if reader.is_all else 10,
        )
        return {"items": df_to_json(df), "total_system_revenue": float(total_revenue)}

    def reduce(pairs):
        items = group_rows(
            pairs,
            group_by=("name", "item_type"),
            spec={"total_sold": Sum(), "item_revenue": Sum()},
            sort_by="total_sold",
            descending=True,
            limit=10,
            contributor_fields=("total_sold", "item_revenue"),
            rows_of=lambda value: value["items"],
        )
        return {
            "items": items,
            "total_system_revenue": sum(
                float(value["total_system_revenue"] or 0) for _, value in pairs
            ),
        }

    return reader.read(query, reduce)


@router.get("/revenue_by_category")
def get_revenue_by_category(
    start_date: str = None,
    end_date: str = None,
    reader: ScopedReader = Depends(get_reader),
):
    """Get revenue breakdown by category. Optional start_date/end_date = business days (5:00 AM–4:59:59 AM IST)."""

    def query(conn, _profile):
        df, total_revenue = insights_queries.fetch_revenue_by_category_data(
            conn, start_date=start_date, end_date=end_date
        )
        return {"categories": df_to_json(df), "total_system_revenue": float(total_revenue)}

    def reduce(pairs):
        categories = group_rows(
            pairs,
            group_by=("category",),
            spec={"revenue": Sum()},
            sort_by="revenue",
            descending=True,
            rows_of=lambda value: value["categories"],
        )
        return {
            "categories": categories,
            "total_system_revenue": sum(
                float(value["total_system_revenue"] or 0) for _, value in pairs
            ),
        }

    return reader.read(query, reduce)


@router.get("/hourly_revenue")
def get_hourly_revenue(
    days: str = None,
    start_date: str = None,
    end_date: str = None,
    reader: ScopedReader = Depends(get_reader),
):
    """Get hourly revenue distribution (business day 5am–4:59am).

    Args:
        days: Optional comma-separated day numbers to include (0=Sun, 1=Mon, ..., 6=Sat)
        start_date: Optional begin date YYYY-MM-DD (inclusive)
        end_date: Optional end date YYYY-MM-DD (inclusive). Use with start_date.
    """
    days_list = None
    if days:
        try:
            days_list = [int(d.strip()) for d in days.split(',') if d.strip()]
        except ValueError:
            pass  # Invalid format, ignore filter

    def query(conn, _profile):
        atoms = insights_queries.fetch_hourly_revenue_atoms(
            conn, days=days_list, start_date=start_date, end_date=end_date
        )
        if reader.is_all:
            return atoms
        return df_to_json(insights_queries.build_hourly_revenue_rows([atoms]))

    def reduce(pairs):
        # Per-hour average uses the union of business dates: dividing summed
        # revenue by one store's day count would inflate every bar.
        return df_to_json(
            insights_queries.build_hourly_revenue_rows([value for _, value in pairs])
        )

    return reader.read(query, reduce)


@router.get("/hourly_revenue_by_date")
def get_hourly_revenue_by_date(date: str, reader: ScopedReader = Depends(get_reader)):
    """Get hourly revenue for a specific date

    Args:
        date: Date in YYYY-MM-DD format
    """

    def query(conn, _profile):
        return df_to_json(insights_queries.fetch_hourly_revenue_by_date(conn, date))

    def reduce(pairs):
        rows = group_rows(
            pairs,
            group_by=("hour_num",),
            spec={"revenue": Sum()},
        )
        rows.sort(key=lambda row: insights_queries.hour_display_order(row["hour_num"]))
        return rows

    return reader.read(query, reduce)


@router.get("/order_source")
def get_order_source(
    start_date: str = None,
    end_date: str = None,
    reader: ScopedReader = Depends(get_reader),
):
    """Get order distribution by source (POS, Website, Swiggy, Zomato). Optional start_date/end_date = business days (5:00 AM–4:59:59 AM IST)."""

    def query(conn, _profile):
        return df_to_json(
            insights_queries.fetch_order_source_data(
                conn, start_date=start_date, end_date=end_date
            )
        )

    def reduce(pairs):
        return group_rows(
            pairs,
            group_by=("order_from",),
            spec={"count": Sum(), "revenue": Sum()},
            sort_by="count",
            descending=True,
        )

    return reader.read(query, reduce)


@router.get("/avg_revenue_by_day")
def get_avg_revenue_by_day(
    start_date: str = None,
    end_date: str = None,
    reader: ScopedReader = Depends(get_reader),
):
    """Get average revenue by day of week

    Args:
        start_date: Optional filter (YYYY-MM-DD)
        end_date: Optional filter (YYYY-MM-DD)
    """

    def query(conn, _profile):
        if reader.is_all:
            # Atoms: one revenue row per business date, combined before the
            # weekday average is taken.
            return df_to_json(insights_queries.fetch_daily_sales(conn))
        return df_to_json(
            insights_queries.fetch_avg_revenue_by_day(conn, start_date, end_date)
        )

    def reduce(pairs):
        daily = group_rows(
            pairs,
            group_by=("order_date",),
            spec={"total_revenue": Sum()},
            sort_by="order_date",
            descending=False,
        )
        return df_to_json(
            insights_queries.build_avg_revenue_by_day(
                pd.DataFrame(daily, columns=["order_date", "total_revenue"]),
                start_date,
                end_date,
            )
        )

    return reader.read(query, reduce)


@router.get("/brand_awareness")
def get_brand_awareness(
    granularity: str = 'day', reader: ScopedReader = Depends(get_reader)
):
    """Get new verified customer growth over time.

    Args:
        granularity: 'day', 'week', 'month'
    """
    from src.core.queries.customer_queries import fetch_brand_awareness

    def query(conn, _profile):
        return {"data": fetch_brand_awareness(conn, granularity)}

    def reduce(pairs):
        return {
            "data": group_rows(
                pairs,
                group_by=("date",),
                spec={"new_customers": Sum()},
                sort_by="date",
                descending=False,
                rows_of=lambda value: value["data"],
            )
        }

    return reader.read(query, reduce)
