"""
Orders Router - Order data view endpoints

Provides paginated views for orders, order items, customers, restaurants, etc.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict, List, Optional
import json
from src.core.queries import table_queries, customer_queries
from src.api.dependencies import ScopedReader, get_authorized_db, get_db, get_reader
from src.api.utils import df_to_json
from src.core.queries.multi_store_reducers import sort_attributed_rows, union_rows
from src.api.models import CustomerProfileResponse
from src.api.models import (
    CustomerMergePreviewResponse,
    CustomerMergeRequest,
    CustomerMergeHistoryEntry,
    CustomerMergeResult,
    CustomerSimilarityCandidate,
    CustomerUndoMergeRequest,
)


router = APIRouter()


def _ensure_customer_edit_allowed(conn) -> None:
    """Block customer merge/undo when cloud readiness is missing (the server is always strict)."""
    from src.core.customer_mutation_commit import (
        build_customer_edit_http_exception,
        strict_mode_edit_blocked_response,
    )

    blocked = strict_mode_edit_blocked_response(conn)
    if blocked:
        exc = build_customer_edit_http_exception(blocked)
        if exc:
            raise exc


def _finalize_customer_edit_response(res: Dict[str, Any]) -> Dict[str, Any]:
    """Map customer merge/undo results to HTTP responses (409 conflict, 503 blocked, etc.)."""
    from src.core.customer_mutation_commit import build_customer_edit_http_exception

    exc = build_customer_edit_http_exception(res)
    if exc:
        raise exc
    return res


# Offset fan-out costs one store-page per store per requested page. Bound it so a
# deep page cannot quietly turn into a full-table scan across every profile;
# migrate these endpoints to keyset pagination before raising it (plan §7.2).
MAX_FEDERATED_PAGE_ROWS = 5000

# Local integer primary keys are not globally unique, so every federated row
# carries its own table's key qualified by restaurant.
TABLE_ROW_KEYS = {
    "orders": ("order_id",),
    "order_items": ("order_item_id",),
    "order_item_addons": ("order_item_addon_id",),
    "customers": ("customer_id",),
    "restaurants": ("restaurant_id",),
    "order_taxes": ("order_tax_id",),
    "order_discounts": ("order_discount_id",),
}


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
        reader: ScopedReader = Depends(get_reader),
    ):
        filter_dict = json.loads(filters) if filters else {}
        # Each store is filtered with identical normalized inputs; global sorting
        # and pagination happen after the per-store candidates are merged.
        fanout_rows = page * page_size
        if reader.is_all and fanout_rows > MAX_FEDERATED_PAGE_ROWS:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": (
                        f"All Stores paging is bounded to {MAX_FEDERATED_PAGE_ROWS} rows; "
                        "narrow the filter or search instead"
                    ),
                    "code": "deep_page_not_supported",
                },
            )

        def query(conn, _profile):
            df, count, err = table_queries.fetch_paginated_table(
                conn,
                table_name,
                1 if reader.is_all else page,
                fanout_rows if reader.is_all else page_size,
                sort_by,
                "DESC" if sort_desc else "ASC",
                filter_dict,
                search=search,
            )
            if err:
                raise HTTPException(500, err)
            return {"data": df_to_json(df), "total": count}

        def reduce(pairs):
            rows = union_rows(
                pairs,
                key_fields=TABLE_ROW_KEYS.get(table_name, ()),
                rows_of=lambda value: value["data"],
            )
            sort_attributed_rows(rows, sort_by, bool(sort_desc))
            start = (page - 1) * page_size
            return {
                "data": rows[start:start + page_size],
                "total": sum(int(value["total"] or 0) for _, value in pairs),
            }

        result = reader.read(query, reduce)
        if reader.is_all:
            result["data"]["page"] = page
            result["data"]["page_size"] = page_size
            return result
        return {**result, "page": page, "page_size": page_size}


# Register table view endpoints
create_table_endpoint(router, "/view", "orders", "created_on")
create_table_endpoint(router, "/items-view", "order_items", "created_at")
create_table_endpoint(router, "/addons-view", "order_item_addons", "created_at")
create_table_endpoint(router, "/customers-view", "customers", "last_order_date")
create_table_endpoint(router, "/restaurants-view", "restaurants", "restaurant_id")
create_table_endpoint(router, "/taxes-view", "order_taxes", "created_at")
create_table_endpoint(router, "/discounts-view", "order_discounts", "created_at")


# No response_model: All Stores returns the completeness envelope, and federated
# rows carry restaurant attribution that a fixed row model would strip.
@router.get("/customers/search")
def search_customers(q: str, reader: ScopedReader = Depends(get_reader)):
    """Search for customers by name or phone.

    All Stores returns profile-qualified matches. Customers are never merged
    across stores by name or phone (plan §7.4); each hit names its restaurant so
    the caller can open the right physical profile.
    """

    def query(conn, _profile):
        return customer_queries.search_customers(conn, q)

    def reduce(pairs):
        return union_rows(
            pairs,
            key_fields=("customer_id",),
            sort_by="last_order_date",
            descending=True,
        )

    return reader.read(query, reduce)


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
def execute_customer_merge(
    req: CustomerMergeRequest, conn=Depends(get_authorized_db)
):
    """Merge a source customer into a target customer."""
    _ensure_customer_edit_allowed(conn)
    res = customer_queries.merge_customers(
        conn,
        req.source_customer_id,
        req.target_customer_id,
        similarity_score=req.similarity_score,
        model_name=req.model_name,
        reasons=req.reasons,
        mark_target_verified=req.mark_target_verified,
    )
    return _finalize_customer_edit_response(res)


@router.post("/customers/merge/undo", response_model=CustomerMergeResult)
def undo_customer_merge(
    req: CustomerUndoMergeRequest, conn=Depends(get_authorized_db)
):
    """Undo a previous customer merge."""
    _ensure_customer_edit_allowed(conn)
    res = customer_queries.undo_customer_merge(conn, req.merge_id)
    return _finalize_customer_edit_response(res)


@router.post("/customers/merge/pull-from-cloud")
def pull_customer_merges_from_cloud(
    limit: int = 100, conn=Depends(get_authorized_db)
):
    """Manually pull customer merge events from cloud and replay them locally."""
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.customer_merge_sync import (
        get_customer_merge_pull_endpoint,
        pull_and_apply_customer_merge_events,
    )

    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    endpoint = get_customer_merge_pull_endpoint(conn)
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail="Cloud sync URL not configured. Set cloud_sync_url in Configuration.",
        )

    _, auth_key = get_cloud_sync_config(conn)
    result = pull_and_apply_customer_merge_events(conn, endpoint, auth=auth_key, limit=limit)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"Customer merge pull failed: {result['error']}")

    return {
        "message": "Customer merge events pulled from cloud",
        **result,
    }
