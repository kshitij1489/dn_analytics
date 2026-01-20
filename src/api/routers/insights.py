"""
Insights Router - Analytics and KPI endpoints

Provides endpoints for dashboard KPIs, sales trends, customer analytics, etc.
"""

from fastapi import APIRouter, Depends
from src.core.queries import insights_queries
from src.api.dependencies import get_db
from src.api.utils import df_to_json

router = APIRouter()


@router.get("/kpis")
def get_kpis(conn=Depends(get_db)):
    """Get key performance indicators for the dashboard"""
    data = insights_queries.fetch_kpis(conn)
    return dict(data) if data else {}


@router.get("/daily_sales")
def get_daily_sales(conn=Depends(get_db)):
    """Get daily sales data"""
    df = insights_queries.fetch_daily_sales(conn)
    return df_to_json(df)


@router.get("/sales_trend")
def get_sales_trend(conn=Depends(get_db)):
    """Get sales trend over time"""
    df = insights_queries.fetch_sales_trend(conn)
    return df_to_json(df)


@router.get("/category_trend")
def get_category_trend(conn=Depends(get_db)):
    """Get sales trend by category"""
    df = insights_queries.fetch_category_trend(conn)
    return df_to_json(df)


@router.get("/top_items")
def top_items(conn=Depends(get_db)):
    """Get top selling items with revenue data"""
    df, total_revenue = insights_queries.fetch_top_items_data(conn)
    return {"items": df_to_json(df), "total_system_revenue": float(total_revenue)}


@router.get("/revenue_by_category")
def get_revenue_by_category(conn=Depends(get_db)):
    """Get revenue breakdown by category"""
    df, total_revenue = insights_queries.fetch_revenue_by_category_data(conn)
    return {"categories": df_to_json(df), "total_system_revenue": float(total_revenue)}


@router.get("/hourly_revenue")
def get_hourly_revenue(conn=Depends(get_db)):
    """Get hourly revenue distribution"""
    df = insights_queries.fetch_hourly_revenue_data(conn)
    return df_to_json(df)


@router.get("/order_source")
def get_order_source(conn=Depends(get_db)):
    """Get order distribution by source (POS, Website, Swiggy, Zomato)"""
    df = insights_queries.fetch_order_source_data(conn)
    return df_to_json(df)


@router.get("/customer/reorder_rate")
def get_customer_reorder_rate(conn=Depends(get_db)):
    """Get customer reorder rate statistics"""
    from src.core.queries.customer_queries import fetch_customer_reorder_rate
    data = fetch_customer_reorder_rate(conn)
    return data if data else {}


@router.get("/customer/loyalty")
def get_customer_loyalty(conn=Depends(get_db)):
    """Get customer loyalty/retention data"""
    from src.core.queries.customer_queries import fetch_customer_loyalty
    df = fetch_customer_loyalty(conn)
    return df_to_json(df)


@router.get("/customer/top")
def get_top_customers(conn=Depends(get_db)):
    """Get top customers by order count and spending"""
    from src.core.queries.customer_queries import fetch_top_customers
    df = fetch_top_customers(conn)
    return df_to_json(df)
