"""
Menu Router - Menu item management endpoints

Provides endpoints for menu items, variants, merging, remapping, and verification.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
import json
from src.core.queries import menu_queries, table_queries
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.api.models import MergeRequest, UndoMergeRequest, RemapRequest, VerifyRequest
from utils import menu_utils

router = APIRouter()


# --- Basic Menu Endpoints ---

@router.get("/items")
def get_menu_stats(
    name_search: Optional[str] = None,
    type_choice: str = "All",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[List[str]] = Query(None),
    conn=Depends(get_db)
):
    """Get menu items with optional filtering"""
    df = menu_queries.fetch_menu_stats(
        conn, 
        name_search=name_search,  
        type_choice=type_choice, 
        start_date=start_date, 
        end_date=end_date, 
        selected_weekdays=days
    )
    return df_to_json(df)


@router.get("/types")
def get_menu_types(conn=Depends(get_db)):
    """Get list of menu item types"""
    types = menu_queries.fetch_menu_types(conn)
    return types


# --- Paginated View Endpoints ---

@router.get("/items-view")
def get_menu_items_view(
    page: int = 1, 
    page_size: int = 50, 
    sort_by: str = "total_revenue", 
    sort_desc: bool = True,
    filters: Optional[str] = None,
    conn=Depends(get_db)
):
    """Paginated view of menu_items_summary_view"""
    filter_dict = json.loads(filters) if filters else {}
    df, count, err = table_queries.fetch_paginated_table(
        conn, 
        "menu_items_summary_view", 
        page, 
        page_size, 
        sort_by, 
        "DESC" if sort_desc else "ASC", 
        filter_dict
    )
    if err: 
        raise HTTPException(500, err)
    return {"data": df_to_json(df), "total": count, "page": page, "page_size": page_size}


@router.get("/variants-view")
def get_variants_view(
    page: int = 1, 
    page_size: int = 50, 
    sort_by: str = "variant_name", 
    sort_desc: bool = False,
    filters: Optional[str] = None,
    conn=Depends(get_db)
):
    """Paginated view of variants"""
    filter_dict = json.loads(filters) if filters else {}
    df, count, err = table_queries.fetch_paginated_table(
        conn, 
        "variants", 
        page, 
        page_size, 
        sort_by, 
        "DESC" if sort_desc else "ASC", 
        filter_dict
    )
    if err: 
        raise HTTPException(500, err)
    return {"data": df_to_json(df), "total": count, "page": page, "page_size": page_size}


@router.get("/matrix")
def get_menu_matrix(conn=Depends(get_db)):
    """Full menu matrix for client-side pagination"""
    df = menu_queries.fetch_menu_matrix(conn)
    return df_to_json(df)


# --- Dropdown List Endpoints ---

@router.get("/list")
def get_menu_list(conn=Depends(get_db)):
    """Lightweight list of all items for dropdowns"""
    cursor = conn.cursor()
    cursor.execute("SELECT menu_item_id, name, type FROM menu_items ORDER BY name")
    data = [{"menu_item_id": row[0], "name": row[1], "type": row[2]} for row in cursor.fetchall()]
    cursor.close()
    return data


@router.get("/variants/list")
def get_variants_list(conn=Depends(get_db)):
    """Lightweight list of all variants for dropdowns"""
    cursor = conn.cursor()
    cursor.execute("SELECT variant_id, variant_name FROM variants ORDER BY variant_name")
    data = [{"variant_id": row[0], "name": row[1]} for row in cursor.fetchall()]
    cursor.close()
    return data


# --- Merge Logic ---

@router.get("/merge/history")
def get_merge_history(conn=Depends(get_db)):
    """Get recent merge history"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT h.*, m.name as target_name 
        FROM merge_history h
        LEFT JOIN menu_items m ON h.target_id = m.menu_item_id
        ORDER BY h.merged_at DESC 
        LIMIT 20
    """)
    cols = [desc[0] for desc in cursor.description]
    results = [dict(zip(cols, row)) for row in cursor.fetchall()]
    cursor.close()
    return results


@router.post("/merge")
def execute_merge(req: MergeRequest, conn=Depends(get_db)):
    """Merge source menu item into target"""
    res = menu_utils.merge_menu_items(conn, req.source_id, req.target_id)
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res


@router.post("/merge/undo")
def undo_merge(req: UndoMergeRequest, conn=Depends(get_db)):
    """Undo a previous merge operation"""
    res = menu_utils.undo_merge(conn, req.merge_id)
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res


# --- Remap Logic ---

@router.get("/remap/check/{order_item_id}")
def check_remap_target(order_item_id: str, conn=Depends(get_db)):
    """Check current mapping for an order item"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.name, v.variant_name, m.menu_item_id, v.variant_id
        FROM menu_item_variants mv
        JOIN menu_items m ON mv.menu_item_id = m.menu_item_id
        JOIN variants v ON mv.variant_id = v.variant_id
        WHERE mv.order_item_id = ?
    """, (order_item_id,))
    current = cursor.fetchone()
    cursor.close()
    if current:
        return {
            "found": True, 
            "current_item": current[0], 
            "current_variant": current[1],
            "menu_item_id": current[2],
            "variant_id": current[3]
        }
    return {"found": False}


@router.post("/remap")
def execute_remap(req: RemapRequest, conn=Depends(get_db)):
    """Remap an order item to a different menu item/variant"""
    res = menu_utils.remap_order_item_cluster(conn, req.order_item_id, req.new_menu_item_id, req.new_variant_id)
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res


# --- Resolutions ---

@router.get("/resolutions/unverified")
def get_unverified(conn=Depends(get_db)):
    """Get list of unverified menu items"""
    df = menu_queries.fetch_unverified_items(conn)
    return df_to_json(df)


@router.post("/resolutions/verify")
def verify_item_endpoint(req: VerifyRequest, conn=Depends(get_db)):
    """Verify a menu item, optionally renaming it"""
    res = menu_utils.verify_item(conn, req.menu_item_id, req.new_name, req.new_type)
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res
