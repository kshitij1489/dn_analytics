"""
Menu Router - Menu item management endpoints

Provides endpoints for menu items, variants, merging, remapping, and verification.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List, Optional, Tuple
import json
from datetime import datetime, timedelta
from src.core.queries import menu_queries, table_queries
from src.api.dependencies import get_db
from src.api.utils import df_to_json
from src.core.utils.business_date import get_current_business_date
from src.api.models import (
    CreateVariantTypeRequest,
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


def _ensure_menu_edit_allowed(conn) -> None:
    """Block human menu edits when strict mode is on but cloud readiness is missing."""
    from src.core.menu_mutation_commit import (
        build_menu_edit_http_exception,
        strict_mode_edit_blocked_response,
    )

    blocked = strict_mode_edit_blocked_response(conn)
    if blocked:
        exc = build_menu_edit_http_exception(blocked)
        if exc:
            raise exc


def _finalize_menu_edit_response(res: Dict[str, Any]) -> Dict[str, Any]:
    """Map menu_utils edit results to HTTP responses (409 conflict, 503 blocked, etc.)."""
    from src.core.menu_mutation_commit import build_menu_edit_http_exception

    exc = build_menu_edit_http_exception(res)
    if exc:
        raise exc
    return res


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

@router.get("/summary")
def get_menu_summary(
    mode: str = Query("quantity", description="'volume' or 'quantity'"),
    as_of_date: Optional[str] = Query(
        None,
        description="Business date (YYYY-MM-DD) that ends each rolling window; defaults to current business date.",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    name_search: Optional[str] = None,
    sort_by: str = Query(
        "lifetime",
        description="Rollup column to sort by: day_1, day_2, day_3, day_5, day_7, day_14, month_1, month_2, lifetime.",
    ),
    sort_desc: bool = Query(True, description="Descending when true."),
    conn=Depends(get_db),
):
    """Rolling quantity or unit-volume totals by menu item (Menu → Summary)."""
    if mode not in ("volume", "quantity"):
        raise HTTPException(status_code=400, detail="mode must be 'volume' or 'quantity'")
    end_bd = as_of_date or get_current_business_date()
    df, count, err = menu_queries.fetch_menu_summary_rollups(
        conn,
        mode=mode,
        as_of_date=end_bd,
        page=page,
        page_size=page_size,
        name_search=name_search,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"data": df_to_json(df), "total": count, "page": page, "page_size": page_size, "as_of_date": end_bd}


@router.get("/summary-timeseries")
def get_menu_summary_timeseries(
    menu_item_ids: str = Query(
        ...,
        description="Comma-separated menu_item_id values (max 15).",
    ),
    start_date: Optional[str] = Query(
        None,
        description="Business date lower bound (YYYY-MM-DD). Defaults to 365 days before end_date.",
    ),
    end_date: Optional[str] = Query(
        None,
        description="Business date upper bound (YYYY-MM-DD). Defaults to current business date.",
    ),
    conn=Depends(get_db),
):
    """
    Daily quantity, volume, and revenue for selected menu items (Menu Summary chart).

    Uses the same event-level definitions as Menu → Summary rollups and Menu Items revenue.
    """
    ids = tuple(x.strip() for x in menu_item_ids.split(",") if x.strip())
    if not ids:
        raise HTTPException(status_code=400, detail="menu_item_ids is required")
    if len(ids) > 15:
        raise HTTPException(status_code=400, detail="At most 15 menu items per request")

    end_bd = end_date or get_current_business_date()
    if start_date:
        start_bd = start_date
    else:
        end_dt = datetime.fromisoformat(end_bd)
        start_bd = (end_dt - timedelta(days=365)).date().isoformat()

    df, err = menu_queries.fetch_menu_items_daily_timeseries(
        conn, ids, start_date=start_bd, end_date=end_bd
    )
    if err:
        raise HTTPException(status_code=500, detail=err)
    return {"data": df_to_json(df), "start_date": start_bd, "end_date": end_bd}


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


@router.post("/variants/create")
def create_variant_type_endpoint(req: CreateVariantTypeRequest, conn=Depends(get_db)):
    """Create a new variant type; uses the clustering pipeline's deterministic ID scheme."""
    res = menu_utils.create_variant_type(
        conn,
        req.variant_name,
        req.description,
        req.unit,
        req.value,
    )
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    return res


# --- Merge Logic ---

@router.get("/merge/history")
def get_merge_history(limit: int = 20, offset: int = 0, conn=Depends(get_db)):
    """Get paginated merge/resolution history"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM merge_history")
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute("""
        SELECT h.*, m.name as target_name
        FROM merge_history h
        LEFT JOIN menu_items m ON h.target_id = m.menu_item_id
        ORDER BY h.merged_at DESC, h.merge_id DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
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
    return {"entries": results, "total": total, "limit": limit, "offset": offset}


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
    _ensure_menu_edit_allowed(conn)
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
    return _finalize_menu_edit_response(res)


@router.post("/merge/undo")
def undo_merge(req: UndoMergeRequest, conn=Depends(get_db)):
    """Undo a previous merge operation"""
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.undo_merge(conn, req.merge_id)
    return _finalize_menu_edit_response(res)


@router.post("/merge/pull-from-cloud")
def pull_menu_merges_from_cloud(limit: int = 100, conn=Depends(get_db)):
    """Manually pull menu merge events from cloud and replay them locally."""
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.menu_merge_sync import (
        get_menu_merge_pull_endpoint,
        pull_and_apply_menu_merge_events,
    )

    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    endpoint = get_menu_merge_pull_endpoint(conn)
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail="Cloud sync URL not configured. Set cloud_sync_url in Configuration.",
        )

    _, auth_key = get_cloud_sync_config(conn)
    result = pull_and_apply_menu_merge_events(conn, endpoint, auth=auth_key, limit=limit)
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"Menu merge pull failed: {result['error']}")

    return {
        "message": "Menu merge events pulled from cloud",
        **result,
    }


@router.post("/mapping-verifications/pull-from-cloud")
def pull_menu_mapping_verifications_from_cloud(limit: int = 100, conn=Depends(get_db)):
    """Pull menu mapping verification events from cloud and apply locally."""
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.menu_mapping_verification_sync import (
        get_menu_mapping_verification_pull_endpoint,
        pull_and_apply_menu_mapping_verification_events,
    )

    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")

    endpoint = get_menu_mapping_verification_pull_endpoint(conn)
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail="Cloud sync URL not configured. Set cloud_sync_url in Configuration.",
        )

    _, auth_key = get_cloud_sync_config(conn)
    result = pull_and_apply_menu_mapping_verification_events(conn, endpoint, auth=auth_key, limit=limit)
    if result.get("error"):
        raise HTTPException(
            status_code=502,
            detail=f"Menu mapping verification pull failed: {result['error']}",
        )

    return {
        "message": "Menu mapping verification events pulled from cloud",
        **result,
    }


@router.get("/sync-conflicts")
def get_sync_conflicts(include_resolved: bool = False, conn=Depends(get_db)):
    """List quarantined sync events and supersede notices for user review."""
    from src.core.menu_assignment_apply import list_supersede_notices
    from src.core.menu_sync_quarantine import list_sync_conflicts

    conflicts = list_sync_conflicts(conn, include_resolved=include_resolved)
    unresolved_count = sum(1 for conflict in conflicts if not conflict.get("resolved_at"))
    notices = list_supersede_notices(conn, include_acknowledged=include_resolved)
    open_notices = sum(1 for notice in notices if not notice.get("acknowledged_at"))
    return {
        "count": unresolved_count + open_notices,
        "conflicts": conflicts,
        "supersede_notices": notices,
    }


@router.post("/sync-conflicts/notices/{notice_id}/acknowledge")
def acknowledge_supersede_notice_endpoint(notice_id: int, conn=Depends(get_db)):
    """Acknowledge a 'your resolution was superseded' notice."""
    from src.core.menu_assignment_apply import acknowledge_supersede_notice

    acknowledged = acknowledge_supersede_notice(conn, notice_id)
    if not acknowledged:
        raise HTTPException(status_code=404, detail="Open supersede notice not found")
    conn.commit()
    return {"status": "ok", "notice_id": notice_id}


@router.post("/sync-conflicts/{remote_event_id}/dismiss")
def dismiss_sync_conflict_endpoint(remote_event_id: str, conn=Depends(get_db)):
    """Dismiss a quarantined sync event so it stops retrying (kept for audit)."""
    from src.core.menu_sync_quarantine import dismiss_sync_conflict

    dismissed = dismiss_sync_conflict(conn, remote_event_id)
    if not dismissed:
        raise HTTPException(status_code=404, detail="Unresolved sync conflict not found")
    conn.commit()
    return {"status": "ok", "remote_event_id": remote_event_id}


@router.post("/bootstrap/pull-from-cloud")
def pull_menu_bootstrap_from_cloud(
    apply_mode: str = "seed_and_relink_orders",
    conn=Depends(get_db),
):
    """Manually pull the latest menu bootstrap snapshot from cloud and apply it locally."""
    from src.core.config.cloud_sync_config import get_cloud_sync_config
    from src.core.menu_bootstrap_sync import (
        SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES,
        fetch_and_apply_menu_bootstrap_snapshot,
        get_menu_bootstrap_pull_endpoint,
    )

    if apply_mode not in SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES:
        raise HTTPException(
            status_code=400,
            detail=(
                "apply_mode must be one of: "
                + ", ".join(sorted(SUPPORTED_MENU_BOOTSTRAP_APPLY_MODES))
            ),
        )

    endpoint = get_menu_bootstrap_pull_endpoint(conn)
    if not endpoint:
        raise HTTPException(
            status_code=400,
            detail="Cloud sync URL not configured. Set cloud_sync_url in Configuration.",
        )

    _, auth_key = get_cloud_sync_config(conn)
    result = fetch_and_apply_menu_bootstrap_snapshot(
        conn,
        endpoint,
        auth=auth_key,
        apply_mode=apply_mode,
    )
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"Menu bootstrap pull failed: {result['error']}")

    return {
        "message": "Menu bootstrap snapshot pulled from cloud",
        **result,
    }


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
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.remap_order_item_cluster(conn, req.order_item_id, req.new_menu_item_id, req.new_variant_id)
    return _finalize_menu_edit_response(res)


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
    _ensure_menu_edit_allowed(conn)
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
    return _finalize_menu_edit_response(res)


@router.post("/resolutions/verify")
def verify_item_endpoint(req: VerifyRequest, conn=Depends(get_db)):
    """Verify a menu item, optionally renaming it"""
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.verify_item(conn, req.menu_item_id, req.new_name, req.new_type, req.new_variant_id)
    return _finalize_menu_edit_response(res)
