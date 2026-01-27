"""
Orders Router - Order data view endpoints

Provides paginated views for orders, order items, customers, restaurants, etc.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import json
from src.core.queries import table_queries
from src.api.dependencies import get_db
from src.api.utils import df_to_json

router = APIRouter()


def create_table_endpoint(router: APIRouter, path: str, table_name: str, default_sort_col: str = "created_at"):
    """
    Factory function to create paginated table view endpoints.
    
    Reduces boilerplate by generating similar endpoints for different tables.
    """
    @router.get(path)
    def view_table(
        page: int = 1, 
        page_size: int = 50, 
        sort_by: str = default_sort_col, 
        sort_desc: bool = True,
        filters: Optional[str] = None,
        conn=Depends(get_db)
    ):
        filter_dict = json.loads(filters) if filters else {}
        df, count, err = table_queries.fetch_paginated_table(
            conn, 
            table_name, 
            page, 
            page_size, 
            sort_by, 
            "DESC" if sort_desc else "ASC", 
            filter_dict
        )
        if err: 
            raise HTTPException(500, err)
        return {"data": df_to_json(df), "total": count, "page": page, "page_size": page_size}


# Register table view endpoints
create_table_endpoint(router, "/view", "orders", "created_on")
create_table_endpoint(router, "/items-view", "order_items", "created_at")
create_table_endpoint(router, "/customers-view", "customers", "last_order_date")
create_table_endpoint(router, "/restaurants-view", "restaurants", "restaurant_id")
create_table_endpoint(router, "/taxes-view", "order_taxes", "created_at")
create_table_endpoint(router, "/discounts-view", "order_discounts", "created_at")
