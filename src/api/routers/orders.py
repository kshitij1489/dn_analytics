"""
Orders Router - Order data view endpoints

Provides paginated views for orders, order items, customers, restaurants, etc.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, List
import json
from src.core.queries import table_queries, customer_queries
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.api.models import CustomerSearchResponse, CustomerProfileResponse, CustomerProfileOrder
from src.api.models import (
    CustomerMergePreviewResponse,
    CustomerMergeRequest,
    CustomerMergeHistoryEntry,
    CustomerMergeResult,
    CustomerSimilarityCandidate,
    CustomerUndoMergeRequest,
)


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
        search: Optional[str] = None,
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
            filter_dict,
            search=search,
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


@router.get("/customers/search", response_model=List[CustomerSearchResponse])
def search_customers(q: str, conn=Depends(get_db)):
    """Search for customers by name or phone"""
    results = customer_queries.search_customers(conn, q)
    return results


@router.get("/customers/{customer_id}/profile", response_model=CustomerProfileResponse)
def get_customer_profile(customer_id: str, conn=Depends(get_db)):
    """Get complete customer profile and order history"""
    customer, orders, addresses = customer_queries.fetch_customer_profile_data(conn, customer_id)
    
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    return {
        "customer": customer,
        "orders": orders,
        "addresses": addresses,
    }


@router.get("/customers/similar", response_model=List[CustomerSimilarityCandidate])
def get_similar_customers(limit: int = 20, min_score: float = 0.72, q: Optional[str] = None, conn=Depends(get_db)):
    """Get likely duplicate/related customer pairs using a basic similarity model."""
    return customer_queries.fetch_customer_similarity_candidates(conn, limit=limit, min_score=min_score, search_query=q)


@router.get("/customers/merge/preview", response_model=CustomerMergePreviewResponse)
def preview_customer_merge(source_customer_id: str, target_customer_id: str, conn=Depends(get_db)):
    """Preview the impact of merging one customer into another."""
    res = customer_queries.fetch_customer_merge_preview(conn, source_customer_id, target_customer_id)
    if res.get("status") == "error":
        raise HTTPException(400, res["message"])
    return res


@router.get("/customers/merge/history", response_model=List[CustomerMergeHistoryEntry])
def get_customer_merge_history(limit: int = 20, conn=Depends(get_db)):
    """Get recent customer merge audit history."""
    return customer_queries.fetch_customer_merge_history(conn, limit=limit)


@router.post("/customers/merge", response_model=CustomerMergeResult)
def execute_customer_merge(req: CustomerMergeRequest, conn=Depends(get_db)):
    """Merge a source customer into a target customer."""
    res = customer_queries.merge_customers(
        conn,
        req.source_customer_id,
        req.target_customer_id,
        similarity_score=req.similarity_score,
        model_name=req.model_name,
        reasons=req.reasons,
        mark_target_verified=req.mark_target_verified,
    )
    if res.get("status") == "error":
        raise HTTPException(400, res["message"])
    return res


@router.post("/customers/merge/undo", response_model=CustomerMergeResult)
def undo_customer_merge(req: CustomerUndoMergeRequest, conn=Depends(get_db)):
    """Undo a previous customer merge."""
    res = customer_queries.undo_customer_merge(conn, req.merge_id)
    if res.get("status") == "error":
        raise HTTPException(400, res["message"])
    return res
