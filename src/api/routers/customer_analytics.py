"""
Customer Analytics Router - Customer-specific KPI and analytics endpoints

Provides endpoints for customer return rate, retention rate, repeat order rate,
loyalty summary, reorder trends, top customers, and brand awareness.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
import pandas as pd
from typing import List, Optional
from src.api.dependencies import get_db
from src.api.utils import df_to_json

router = APIRouter()


@router.get("/reorder_rate")
def get_customer_reorder_rate(conn=Depends(get_db)):
    """Get customer reorder rate statistics"""
    from src.core.queries.customer_queries import fetch_customer_reorder_rate
    data = fetch_customer_reorder_rate(conn)
    return data if data else {}


@router.get("/return_rate_analysis")
def get_customer_return_rate_analysis(
    evaluation_start_date: str = None,
    evaluation_end_date: str = None,
    lookback_start_date: str = None,
    lookback_end_date: str = None,
    lookback_days: int = None,
    min_orders_per_customer: int = 2,
    order_sources: Optional[List[str]] = Query(None),
    conn=Depends(get_db),
):
    """Get detailed customer return-rate analytics for a custom evaluation and lookback window."""
    from src.core.queries.customer_queries import fetch_customer_return_rate_analysis

    try:
        return fetch_customer_return_rate_analysis(
            conn,
            evaluation_start_date=evaluation_start_date,
            evaluation_end_date=evaluation_end_date,
            lookback_start_date=lookback_start_date,
            lookback_end_date=lookback_end_date,
            lookback_days=lookback_days,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=tuple(order_sources) if order_sources else None,
        )
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
    conn=Depends(get_db),
):
    """Get detailed customer retention-rate analytics for a custom evaluation and lookback window."""
    from src.core.queries.customer_queries import fetch_customer_retention_rate_analysis

    try:
        return fetch_customer_retention_rate_analysis(
            conn,
            evaluation_start_date=evaluation_start_date,
            evaluation_end_date=evaluation_end_date,
            lookback_start_date=lookback_start_date,
            lookback_end_date=lookback_end_date,
            lookback_days=lookback_days,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=tuple(order_sources) if order_sources else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/repeat_order_rate_analysis")
def get_repeat_order_rate_analysis(
    evaluation_start_date: str = None,
    evaluation_end_date: str = None,
    min_orders_per_customer: int = 2,
    order_sources: Optional[List[str]] = Query(None),
    conn=Depends(get_db),
):
    """Get detailed repeat-order-rate analytics for a custom evaluation window."""
    from src.core.queries.customer_queries import fetch_repeat_order_rate_analysis

    try:
        return fetch_repeat_order_rate_analysis(
            conn,
            evaluation_start_date=evaluation_start_date,
            evaluation_end_date=evaluation_end_date,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=tuple(order_sources) if order_sources else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/quick_view")
def get_customer_quick_view(conn=Depends(get_db)):
    """Get customer quick-view KPIs for the Customers workspace."""
    from src.core.queries import insights_queries
    data = insights_queries.fetch_customer_quick_view(conn)
    return dict(data) if data else {}


@router.get("/reorder_rate_trend")
def get_reorder_rate_trend(
    granularity: str = 'day',
    start_date: str = None,
    end_date: str = None,
    metric: str = 'orders',
    conn=Depends(get_db)
):
    """
    Get reorder rate trend over time.
    Granularity: 'day', 'week', 'month'
    Metric: 'orders' (Repeat Order Rate), 'customers' (Repeat Customer Rate)
    """
    from src.core.queries.customer_queries import fetch_reorder_rate_trend
    data = fetch_reorder_rate_trend(conn, granularity, start_date, end_date, metric)
    return df_to_json(pd.DataFrame(data)) if data else []


@router.get("/loyalty")
def get_customer_loyalty(conn=Depends(get_db)):
    """Get customer loyalty/retention data"""
    from src.core.queries.customer_queries import fetch_customer_loyalty
    df = fetch_customer_loyalty(conn)
    return df_to_json(df)


@router.get("/top")
def get_top_customers(conn=Depends(get_db)):
    """Get top customers by order count and spending"""
    from src.core.queries.customer_queries import fetch_top_customers
    df = fetch_top_customers(conn)
    return df_to_json(df)
