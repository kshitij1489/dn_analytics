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
def get_hourly_revenue(days: str = None, conn=Depends(get_db)):
    """Get hourly revenue distribution
    
    Args:
        days: Optional comma-separated day numbers to include (0=Sun, 1=Mon, ..., 6=Sat)
              Example: "1,2,3,4,5" for Mon-Fri only
    """
    days_list = None
    if days:
        try:
            days_list = [int(d.strip()) for d in days.split(',') if d.strip()]
        except ValueError:
            pass  # Invalid format, ignore filter
    
    df = insights_queries.fetch_hourly_revenue_data(conn, days_list)
    return df_to_json(df)


@router.get("/hourly_revenue_by_date")
def get_hourly_revenue_by_date(date: str, conn=Depends(get_db)):
    """Get hourly revenue for a specific date (IST)
    
    Args:
        date: Date in YYYY-MM-DD format (IST timezone)
    """
    df = insights_queries.fetch_hourly_revenue_by_date(conn, date)
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
@router.get("/avg_revenue_by_day")
def get_avg_revenue_by_day(
    start_date: str = None, 
    end_date: str = None, 
    conn=Depends(get_db)
):
    """Get average revenue by day of week
    
    Args:
        start_date: Optional filter (YYYY-MM-DD)
        end_date: Optional filter (YYYY-MM-DD)
    """
    df = insights_queries.fetch_avg_revenue_by_day(conn, start_date, end_date)
    return df_to_json(df)


@router.get("/brand_awareness")
def get_brand_awareness(granularity: str = 'day', conn=Depends(get_db)):
    """Get new verified customer growth over time.
    
    Args:
        granularity: 'day', 'week', 'month'
    """
    from src.core.queries.customer_queries import fetch_brand_awareness
    data = fetch_brand_awareness(conn, granularity)
    return {"data": data}



