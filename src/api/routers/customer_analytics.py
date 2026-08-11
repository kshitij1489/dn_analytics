"""
Customer Analytics Router - Customer-specific KPI and analytics endpoints

Provides endpoints for customer return rate, retention rate, repeat order rate,
loyalty summary, reorder trends, top customers, and brand awareness.

All Stores combines *order atoms* and re-runs the same pure calculators, so every
rate is recomputed from the combined numerator and denominator. Customers stay
profile-qualified: two stores' customer records are never merged by name, phone,
or a local ID (plan §7.4).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
import pandas as pd
from typing import List, Optional
from src.api.dependencies import ScopedReader, get_reader
from src.api.utils import df_to_json
from src.core.queries.multi_store_reducers import Ratio, Sum, group_rows, union_rows

router = APIRouter()


def _qualified_rows(source, payload, key="rows"):
    """Restore local customer IDs and name the store that owns each row."""
    rows = payload.get(key) or []
    return {**payload, key: source.qualify_rows(rows)}


@router.get("/reorder_rate")
def get_customer_reorder_rate(reader: ScopedReader = Depends(get_reader)):
    """Get customer reorder rate statistics"""
    from src.core.queries.customer_queries import fetch_customer_reorder_rate

    def build(members, source):
        conn = members[0][1] if members else None
        data = fetch_customer_reorder_rate(conn, orders_source=source)
        return data if data else {}

    return reader.read_together(build)


@router.get("/return_rate_analysis")
def get_customer_return_rate_analysis(
    evaluation_start_date: str = None,
    evaluation_end_date: str = None,
    lookback_start_date: str = None,
    lookback_end_date: str = None,
    lookback_days: int = None,
    min_orders_per_customer: int = 2,
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Get detailed customer return-rate analytics for a custom evaluation and lookback window."""
    from src.core.queries.customer_queries import fetch_customer_return_rate_analysis

    def build(members, source):
        conn = members[0][1] if members else None
        return _qualified_rows(
            source,
            fetch_customer_return_rate_analysis(
                conn,
                evaluation_start_date=evaluation_start_date,
                evaluation_end_date=evaluation_end_date,
                lookback_start_date=lookback_start_date,
                lookback_end_date=lookback_end_date,
                lookback_days=lookback_days,
                min_orders_per_customer=min_orders_per_customer,
                order_sources=tuple(order_sources) if order_sources else None,
                orders_source=source,
            ),
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/retention_rate_analysis")
def get_customer_retention_rate_analysis(
    evaluation_start_date: str = None,
    evaluation_end_date: str = None,
    lookback_start_date: str = None,
    lookback_end_date: str = None,
    lookback_days: int = None,
    min_orders_per_customer: int = 2,
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Get detailed customer retention-rate analytics for a custom evaluation and lookback window."""
    from src.core.queries.customer_queries import fetch_customer_retention_rate_analysis

    def build(members, source):
        conn = members[0][1] if members else None
        return _qualified_rows(
            source,
            fetch_customer_retention_rate_analysis(
                conn,
                evaluation_start_date=evaluation_start_date,
                evaluation_end_date=evaluation_end_date,
                lookback_start_date=lookback_start_date,
                lookback_end_date=lookback_end_date,
                lookback_days=lookback_days,
                min_orders_per_customer=min_orders_per_customer,
                order_sources=tuple(order_sources) if order_sources else None,
                orders_source=source,
            ),
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/repeat_order_rate_analysis")
def get_repeat_order_rate_analysis(
    evaluation_start_date: str = None,
    evaluation_end_date: str = None,
    min_orders_per_customer: int = 2,
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Get detailed repeat-order-rate analytics for a custom evaluation window."""
    from src.core.queries.customer_queries import fetch_repeat_order_rate_analysis

    def build(members, source):
        conn = members[0][1] if members else None
        return _qualified_rows(
            source,
            fetch_repeat_order_rate_analysis(
                conn,
                evaluation_start_date=evaluation_start_date,
                evaluation_end_date=evaluation_end_date,
                min_orders_per_customer=min_orders_per_customer,
                order_sources=tuple(order_sources) if order_sources else None,
                orders_source=source,
            ),
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/affinity_analysis")
def get_customer_affinity_analysis(
    evaluation_start_date: str = None,
    evaluation_end_date: str = None,
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Customer affinity (new / repeat / lapsed) for an evaluation window — Zomato-style 60d / 365d rules."""
    from src.core.queries.customer_queries import fetch_customer_affinity_analysis

    def build(members, source):
        conn = members[0][1] if members else None
        return _qualified_rows(
            source,
            fetch_customer_affinity_analysis(
                conn,
                evaluation_start_date=evaluation_start_date,
                evaluation_end_date=evaluation_end_date,
                order_sources=tuple(order_sources) if order_sources else None,
                orders_source=source,
            ),
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/affinity_trend")
def get_customer_affinity_trend(
    months: int = Query(6, ge=1, le=24),
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Month-level affinity counts for recent calendar months (newest row first)."""
    from src.core.queries.customer_queries import fetch_customer_affinity_trend

    def build(members, source):
        conn = members[0][1] if members else None
        return fetch_customer_affinity_trend(
            conn,
            months=months,
            order_sources=tuple(order_sources) if order_sources else None,
            orders_source=source,
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/return_rate_trend")
def get_customer_return_rate_trend(
    months: int = Query(6, ge=1, le=24),
    min_orders_per_customer: int = Query(2, ge=2, le=99),
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Return rate by calendar month with 30d, 60d, and lifetime lookbacks."""
    from src.core.queries.customer_queries import fetch_customer_return_rate_trend

    def build(members, source):
        conn = members[0][1] if members else None
        return fetch_customer_return_rate_trend(
            conn,
            months=months,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=tuple(order_sources) if order_sources else None,
            orders_source=source,
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/retention_rate_trend")
def get_customer_retention_rate_trend(
    months: int = Query(6, ge=1, le=24),
    min_orders_per_customer: int = Query(2, ge=1, le=99),
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Retention rate by calendar month with 30d, 60d, and lifetime lookbacks."""
    from src.core.queries.customer_queries import fetch_customer_retention_rate_trend

    def build(members, source):
        conn = members[0][1] if members else None
        return fetch_customer_retention_rate_trend(
            conn,
            months=months,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=tuple(order_sources) if order_sources else None,
            orders_source=source,
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/repeat_order_rate_trend")
def get_customer_repeat_order_rate_trend(
    months: int = Query(6, ge=1, le=24),
    min_orders_per_customer: int = Query(2, ge=2, le=99),
    order_sources: Optional[List[str]] = Query(None),
    reader: ScopedReader = Depends(get_reader),
):
    """Repeat order rate by calendar month (evaluation window only)."""
    from src.core.queries.customer_queries import fetch_customer_repeat_order_rate_trend

    def build(members, source):
        conn = members[0][1] if members else None
        return fetch_customer_repeat_order_rate_trend(
            conn,
            months=months,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=tuple(order_sources) if order_sources else None,
            orders_source=source,
        )

    try:
        return reader.read_together(build)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/quick_view")
def get_customer_quick_view(reader: ScopedReader = Depends(get_reader)):
    """Get customer quick-view KPIs for the Customers workspace."""
    from src.core.queries import insights_queries
    from src.core.queries.multi_store_reducers import reduce_mapping

    def build(members, source):
        conn = members[0][1] if members else None
        base_kpis = None
        if reader.is_all:
            # Estimated customer counts are store-customer records, so they sum;
            # the rate metrics below come from combined order atoms.
            base_kpis = reduce_mapping(
                [insights_queries.fetch_kpis(member_conn) or {} for _p, member_conn in members],
                {
                    "total_customers_estimate_low": Sum(),
                    "total_customers_estimate_high": Sum(),
                    "total_orders": Sum(),
                    "total_revenue": Sum(),
                    "avg_order_value": Ratio("total_revenue", "total_orders", digits=None),
                    "verified_orders": Sum(),
                    "verified_customers": Sum(),
                    "today_revenue": Sum(),
                },
            )
        data = insights_queries.fetch_customer_quick_view(
            conn, orders_source=source, base_kpis=base_kpis
        )
        return dict(data) if data else {}

    return reader.read_together(build)


@router.get("/reorder_rate_trend")
def get_reorder_rate_trend(
    granularity: str = 'day',
    start_date: str = None,
    end_date: str = None,
    metric: str = 'orders',
    reader: ScopedReader = Depends(get_reader),
):
    """
    Get reorder rate trend over time.
    Granularity: 'day', 'week', 'month'
    Metric: 'orders' (Repeat Order Rate), 'customers' (Repeat Customer Rate)
    """
    from src.core.queries.customer_queries import fetch_reorder_rate_trend

    def query(conn, _profile):
        data = fetch_reorder_rate_trend(conn, granularity, start_date, end_date, metric)
        return df_to_json(pd.DataFrame(data)) if data else []

    def reduce(pairs):
        # Repeat rate is recomputed per bucket from combined counts. A customer
        # counted in two stores counts twice: they are two store-customers.
        return group_rows(
            pairs,
            group_by=("date", "metric_label"),
            spec={
                "total_orders": Sum(),
                "reordered_orders": Sum(),
                "value": Ratio("reordered_orders", "total_orders", scale=100.0),
            },
            sort_by="date",
            descending=False,
        )

    return reader.read(query, reduce)


@router.get("/loyalty")
def get_customer_loyalty(reader: ScopedReader = Depends(get_reader)):
    """Get customer loyalty/retention data"""
    from src.core.queries.customer_queries import fetch_customer_loyalty

    def build(members, source):
        conn = members[0][1] if members else None
        return df_to_json(fetch_customer_loyalty(conn, orders_source=source))

    return reader.read_together(build)


@router.get("/top")
def get_top_customers(reader: ScopedReader = Depends(get_reader)):
    """Get top customers by order count and spending"""
    from src.core.queries.customer_queries import fetch_top_customers

    def query(conn, _profile):
        return df_to_json(fetch_top_customers(conn))

    def reduce(pairs):
        # Each store already returns its own top 50 by spend, so the global top
        # 50 of the union is exact. Rows stay profile-qualified.
        return union_rows(
            pairs,
            key_fields=("customer_id",),
            sort_by="total_spent",
            descending=True,
            limit=50,
        )

    return reader.read(query, reduce)
