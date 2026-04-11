"""
Menu Router - Menu item management endpoints

Provides endpoints for menu items, variants, merging, remapping, and verification.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List, Optional, Tuple
import json
from src.core.queries import menu_queries, table_queries
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.api.models import (
    MergeRequest,
    UndoMergeRequest,
    RemapRequest,
    UpdateVariantMappingRequest,
    ResolveVariantRequest,
    VerifyRequest,
)
from utils.clean_order_item import suggest_variant_for_resolution
from utils import menu_utils

router = APIRouter()


def _parse_merge_history_payload(raw_payload: Any) -> Any:
    if raw_payload is None:
        return None
    if isinstance(raw_payload, str):
        try:
            return json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
    return raw_payload


def _extract_variant_assignment_pairs(payload: Any) -> List[Tuple[str, str]]:
    if not isinstance(payload, dict) or payload.get("kind") not in {"variant_merge_v1", "resolution_variant_v1"}:
        return []

    assignments: List[Tuple[str, str]] = []
    seen = set()
    for section in ("mapping_rows", "order_items", "order_item_addons"):
        for row in payload.get(section, []):
            source_variant_id = row.get("old_variant_id")
            target_variant_id = row.get("new_variant_id")
            if not target_variant_id:
                continue
            normalized_source_variant_id = (
                menu_utils.NULL_VARIANT_SENTINEL
                if source_variant_id is None
                else str(source_variant_id)
            )
            pair = (normalized_source_variant_id, str(target_variant_id))
            if pair in seen:
                continue
            seen.add(pair)
            assignments.append(pair)

    return assignments


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
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    conn=Depends(get_db)
):
    """Paginated view of menu_items_summary_view"""
    filter_dict = json.loads(filters) if filters else {}
    if start_date or end_date:
        df, count, err = menu_queries.fetch_menu_items_summary(
            conn,
            page=page,
            page_size=page_size,
            sort_column=sort_by,
            sort_direction="DESC" if sort_desc else "ASC",
            filters=filter_dict,
            start_date=start_date,
            end_date=end_date,
        )
    else:
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
    cursor.execute("""
        SELECT menu_item_id, name, type, is_verified
        FROM menu_items
        ORDER BY name
    """)
    data = [
        {
            "menu_item_id": row[0],
            "name": row[1],
            "type": row[2],
            "is_verified": bool(row[3]),
        }
        for row in cursor.fetchall()
    ]
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

    parsed_payloads = []
    variant_ids = set()
    for result in results:
        payload = _parse_merge_history_payload(result.get("affected_order_items"))
        parsed_payloads.append(payload)
        for source_variant_id, target_variant_id in _extract_variant_assignment_pairs(payload):
            if source_variant_id != menu_utils.NULL_VARIANT_SENTINEL:
                variant_ids.add(source_variant_id)
            variant_ids.add(target_variant_id)

    variant_name_map: Dict[str, str] = {}
    if variant_ids:
        placeholders = ",".join("?" for _ in variant_ids)
        cursor.execute(
            f"SELECT variant_id, variant_name FROM variants WHERE variant_id IN ({placeholders})",
            list(variant_ids),
        )
        variant_name_map = {str(row[0]): row[1] for row in cursor.fetchall()}

    for result, payload in zip(results, parsed_payloads):
        result["variant_assignments"] = [
            {
                "source_variant_id": source_variant_id,
                "source_variant_name": (
                    menu_utils.NULL_VARIANT_LABEL
                    if source_variant_id == menu_utils.NULL_VARIANT_SENTINEL
                    else variant_name_map.get(source_variant_id, source_variant_id)
                ),
                "target_variant_id": target_variant_id,
                "target_variant_name": variant_name_map.get(target_variant_id, target_variant_id),
            }
            for source_variant_id, target_variant_id in _extract_variant_assignment_pairs(payload)
        ]

    cursor.close()
    return results


@router.get("/merge/preview")
def preview_merge(
    source_id: str,
    target_id: str,
    source_variant_id: Optional[str] = None,
    conn=Depends(get_db),
):
    """Preview the impact of merging a source menu item into a target."""
    res = menu_utils.preview_merge_menu_items(conn, source_id, target_id, source_variant_id)
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res


@router.post("/merge")
def execute_merge(req: MergeRequest, conn=Depends(get_db)):
    """Merge source menu item into target"""
    if req.variant_mappings is not None:
        res = menu_utils.merge_menu_items_with_variant_mappings(
            conn,
            req.source_id,
            req.target_id,
            [
                mapping.model_dump() if hasattr(mapping, "model_dump") else mapping.dict()
                for mapping in req.variant_mappings
            ],
        )
    else:
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


@router.post("/variant-mapping/update")
def update_variant_mapping(req: UpdateVariantMappingRequest, conn=Depends(get_db)):
    """Update an existing menu item + variant mapping to a different variant everywhere it is used."""
    res = menu_utils.update_menu_variant_mapping(
        conn,
        req.menu_item_id,
        req.current_variant_id,
        req.new_variant_id,
    )
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res


# --- Resolutions ---

@router.get("/resolutions/unverified")
def get_unverified(conn=Depends(get_db)):
    """Get list of unresolved menu item + variant pairs."""
    df = menu_queries.fetch_unverified_items(conn)
    items = df_to_json(df)
    for item in items:
        suggested_variant = suggest_variant_for_resolution(item.get("sample_order_name") or item.get("name"), item.get("type"))
        item["suggested_variant_id"] = (
            item.get("source_variant_id") or
            (suggested_variant["variant_id"] if suggested_variant else None)
        )
        item["suggested_variant_name"] = (
            item.get("source_variant_name") or
            (suggested_variant["variant_name"] if suggested_variant else None)
        )
        item["display_name"] = (
            f"{item.get('name')} ({item.get('source_variant_name')})"
            if item.get("source_variant_name")
            else item.get("name")
        )
    return items


@router.post("/resolutions/resolve")
def resolve_variant_endpoint(req: ResolveVariantRequest, conn=Depends(get_db)):
    """Resolve a single unresolved menu item + variant pair."""
    res = menu_utils.resolve_menu_item_variant(
        conn,
        req.source_menu_item_id,
        req.source_variant_id,
        req.target_menu_item_id,
        req.new_name,
        req.new_type,
        req.target_variant_id,
        req.new_variant_name,
    )
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res


@router.post("/resolutions/verify")
def verify_item_endpoint(req: VerifyRequest, conn=Depends(get_db)):
    """Verify a menu item, optionally renaming it"""
    res = menu_utils.verify_item(conn, req.menu_item_id, req.new_name, req.new_type, req.new_variant_id)
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res
