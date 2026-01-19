from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np
from src.core.db.connection import get_db_connection
from src.core.queries import insights_queries

router = APIRouter()

def get_db():
    conn, err = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {err}")
    try:
        yield conn
    finally:
        conn.close()

def df_to_json(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Convert DataFrame to JSON-safe list of dicts"""
    # Convert all object-type numeric columns to float
    for col in df.columns:
        if df[col].dtype == 'object':
            try:
                df[col] = pd.to_numeric(df[col], errors='ignore')
            except:
                pass
    
    # Replace inf and nan with None
    df = df.replace([np.inf, -np.inf, np.nan], None)
    return df.to_dict(orient='records')

@router.get("/kpis")
def get_kpis(conn = Depends(get_db)):
    data = insights_queries.fetch_kpis(conn)
    return dict(data) if data else {}

@router.get("/daily_sales")
def get_daily_sales(conn = Depends(get_db)):
    df = insights_queries.fetch_daily_sales(conn)
    return df_to_json(df)

@router.get("/sales_trend")
def get_sales_trend(conn = Depends(get_db)):
    df = insights_queries.fetch_sales_trend(conn)
    return df_to_json(df)

@router.get("/category_trend")
def get_category_trend(conn = Depends(get_db)):
    df = insights_queries.fetch_category_trend(conn)
    return df_to_json(df)

@router.get("/top_items")
def top_items(conn=Depends(get_db)):
    df, total_revenue = insights_queries.fetch_top_items_data(conn)
    return {"items": df_to_json(df), "total_system_revenue": float(total_revenue)}

@router.get("/revenue_by_category")
def get_revenue_by_category(conn = Depends(get_db)):
    df, total_revenue = insights_queries.fetch_revenue_by_category_data(conn)
    return {"categories": df_to_json(df), "total_system_revenue": float(total_revenue)}

@router.get("/hourly_revenue")
def get_hourly_revenue(conn = Depends(get_db)):
    df = insights_queries.fetch_hourly_revenue_data(conn)
    return df_to_json(df)

@router.get("/order_source")
def get_order_source(conn = Depends(get_db)):
    df = insights_queries.fetch_order_source_data(conn)
    return df_to_json(df)

@router.get("/customer/reorder_rate")
def get_customer_reorder_rate(conn = Depends(get_db)):
    from src.core.queries.customer_queries import fetch_customer_reorder_rate
    data = fetch_customer_reorder_rate(conn)
    return data if data else {}

@router.get("/customer/loyalty")
def get_customer_loyalty(conn = Depends(get_db)):
    from src.core.queries.customer_queries import fetch_customer_loyalty
    df = fetch_customer_loyalty(conn)
    return df_to_json(df)

@router.get("/customer/top")
def get_top_customers(conn = Depends(get_db)):
    from src.core.queries.customer_queries import fetch_top_customers
    df = fetch_top_customers(conn)
    return df_to_json(df)
