"""
Menu Router - Menu item management endpoints

Provides endpoints for menu items, variants, merging, remapping, and verification.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List, Optional, Tuple
import json
from datetime import datetime, timedelta
from src.core.queries import menu_queries, table_queries
from src.api.dependencies import ScopedReader, get_authorized_db, get_db, get_reader
from src.api.utils import df_to_json
from src.core.queries.multi_store import FederationAbort
from src.core.queries.multi_store_reducers import (
    AllTrue,
    AnyTrue,
    First,
    Min,
    Ratio,
    Sum,
    group_rows,
    group_menu_identity_rows,
    identity_aware_federated_data,
    menu_identity_payload,
    union_rows,
)
from src.core.utils.business_date import get_current_business_date
from src.api.models import (
    CreateVariantTypeRequest,
    MergeRequest,
    RetypeMenuItemRequest,
    UndoMergeRequest,
    RemapRequest,
    UpdateVariantMappingRequest,
    ResolveVariantRequest,
    VerifyRequest,
    VerifyAssignmentRequest,
    GlobalMenuMutationPreviewRequest,
    GlobalMenuLocalMutationPreviewRequest,
    GlobalMenuMutationCommitRequest,
    GlobalMenuResolutionContextRequest,
    GlobalMenuAliasPreviewRequest,
    GlobalMenuAliasCommitRequest,
)
from utils.clean_order_item import suggest_variant_for_resolution
from utils import menu_utils
from src.core.mapping_anomalies import (
    list_open_anomalies,
    dismiss_anomaly,
)

router = APIRouter()

# All Stores reads the whole catalog per store before grouping. Menu catalogs are
# small; this bound keeps a pathological one from fanning out unbounded.
MENU_FEDERATION_ROW_LIMIT = 5000


def _enforce_menu_federation_bound(count: int, surface: str) -> None:
    """Refuse a truncated All Stores catalog instead of returning partial data."""
    if count <= MENU_FEDERATION_ROW_LIMIT:
        return
    raise FederationAbort(
        HTTPException(
            status_code=400,
            detail={
                "error": (
                    f"All Stores {surface} is bounded to {MENU_FEDERATION_ROW_LIMIT} "
                    "rows per store; narrow the filter or search"
                ),
                "code": "deep_page_not_supported",
            },
        )
    )


def _ensure_menu_edit_allowed(conn) -> None:
    """Block human menu edits when cloud readiness is missing (the server is always strict)."""
    try:
        from src.core.global_menu_schema import resolve_global_menu_capability

        status = resolve_global_menu_capability(conn)
    except Exception:
        status = None
    if status is not None and status.server_advertised:
        from src.core.global_menu_schema import GlobalMenuCapabilityError

        error = GlobalMenuCapabilityError(
            "Canonical menu changes are group-owned; use the global preview and commit workflow"
        )
        error.code = "global_menu_shadow_write_blocked"
        _raise_global_menu_error(error)

    from src.core.menu_mutation_commit import (
        build_menu_edit_http_exception,
        strict_mode_edit_blocked_response,
    )

    blocked = strict_mode_edit_blocked_response(conn)
    if blocked:
        exc = build_menu_edit_http_exception(blocked)
        if exc:
            raise exc


def _global_menu_active(conn) -> bool:
    from src.core.global_menu_schema import resolve_global_menu_capability

    status = resolve_global_menu_capability(conn)
    return status.active and status.mutation_advertised


def _global_menu_request_active(conn, request) -> bool:
    """Never reinterpret a global-preview commit as a legacy local mutation."""
    active = _global_menu_active(conn)
    preview_fields = (
        "global_mutation_id",
        "global_preview_digest",
        "global_menu_group_id",
        "global_preview_revision",
        "global_mutation_type",
        "global_mutation_payload",
    )
    requested = any(getattr(request, field, None) is not None for field in preview_fields)
    if requested and not active:
        from src.core.global_menu_mutation import is_global_menu_resolution_mutation
        from src.core.global_menu_schema import resolve_global_menu_capability

        try:
            status = resolve_global_menu_capability(conn)
        except Exception:
            status = None
        active = bool(
            status is not None
            and status.resolution_ready
            and is_global_menu_resolution_mutation(
                getattr(request, "global_mutation_type", None)
            )
        )
    if requested and not active:
        from src.core.global_menu_schema import GlobalMenuCapabilityError

        _raise_global_menu_error(
            GlobalMenuCapabilityError(
                "Global menu capability is unavailable or stale; refresh the restaurant registry"
            )
        )
    return active


def _raise_global_menu_error(error: BaseException) -> None:
    code = str(getattr(error, "code", "global_menu_mutation_failed"))
    if code in {"global_menu_editor_required", "global_menu_editor_forbidden"}:
        status_code = 403
    elif code in {
        "global_menu_preview_required",
        "global_menu_preview_stale",
        "global_menu_preview_blocked",
        "global_menu_coverage_incomplete",
        "global_menu_identity_unresolved",
        "global_menu_capability_required",
        "global_menu_mutations_disabled",
        "global_menu_operation_unsupported",
        "global_menu_resolution_disabled",
        "global_menu_resolution_not_ready",
        "global_menu_shared_pos_catalog_required",
        "global_menu_group_pos_aliases_required",
        "global_menu_pos_policy_conflict",
        "global_menu_shadow_write_blocked",
    }:
        status_code = 409
    else:
        status_code = 503
    raise HTTPException(
        status_code=status_code,
        detail={"error": str(error), "code": code},
    ) from error


def _ensure_legacy_menu_pull_allowed(conn) -> None:
    """Legacy menu streams cannot mutate a global-menu projection."""
    from src.core.global_menu_schema import resolve_global_menu_capability

    status = resolve_global_menu_capability(conn)
    if not status.server_advertised:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "error": (
                "Legacy menu pulls are disabled while global menu mode is active; "
                "use Sync DB to refresh the global snapshot and assignments"
            ),
            "code": "global_menu_legacy_pull_disabled",
        },
    )


def _commit_global_request(conn, request) -> Dict[str, Any]:
    from src.core.global_menu_mutation import (
        commit_global_mutation,
        preview_reference_from_request,
    )

    try:
        return commit_global_mutation(
            conn, preview=preview_reference_from_request(request)
        )
    except Exception as exc:
        _raise_global_menu_error(exc)


@router.get("/global/status")
def get_global_menu_status(conn=Depends(get_db)):
    from src.core.global_menu_schema import resolve_global_menu_capability
    from src.core.queries.global_menu_diagnostics import fetch_global_menu_diagnostics

    status = resolve_global_menu_capability(conn).to_dict()
    diagnostics = fetch_global_menu_diagnostics(conn)
    return {
        **status,
        "mapping_count": diagnostics["mapping_count"],
        "price_count": diagnostics["price_count"],
        "assignment_coverage": diagnostics["assignment_coverage"],
        "history_count": diagnostics["history_count"],
        "catalog_digest": diagnostics["catalog_digest"],
        "matrix_digest": diagnostics["matrix_digest"],
        "history_digest": diagnostics["history_digest"],
        "diagnostics": diagnostics,
    }


def _require_group_catalog_view(conn):
    from src.core.global_menu_schema import (
        GlobalMenuCapabilityError,
        require_global_menu_capability,
    )

    try:
        return require_global_menu_capability(conn)
    except GlobalMenuCapabilityError as error:
        _raise_global_menu_error(error)


def _require_group_pos_view(conn):
    from src.core.global_menu_schema import (
        GlobalMenuCapabilityError,
        require_global_menu_capability,
    )

    try:
        capability = require_global_menu_capability(conn)
    except GlobalMenuCapabilityError as error:
        _raise_global_menu_error(error)
    if not capability.group_pos_policy_ready:
        alias_policy = capability.group_pos_aliases_advertised
        error = GlobalMenuCapabilityError(
            "The group POS projection is unavailable until bootstrap completes"
        )
        error.code = (
            "global_menu_group_pos_aliases_required"
            if alias_policy
            else "global_menu_shared_pos_catalog_required"
        )
        _raise_global_menu_error(error)
    return capability


@router.get("/global/catalog")
def get_global_menu_catalog(conn=Depends(get_db)):
    """Active canonical items and variants owned by the selected menu group."""
    # Canonical targets are readable in shadow under global_menu_v1; neither
    # POS policy may gate the alias-resolution target picker.
    capability = _require_group_catalog_view(conn)
    catalog = menu_queries.fetch_group_menu_catalog(conn, capability.menu_group_id)
    return {
        "menu_group_id": capability.menu_group_id,
        "catalog_revision": capability.catalog_revision,
        **catalog,
    }


@router.get("/global/matrix")
def get_global_menu_matrix(conn=Depends(get_db)):
    """Active group-owned Petpooja rules and their current catalog price."""
    capability = _require_group_pos_view(conn)
    return menu_queries.fetch_group_menu_matrix(conn, capability.menu_group_id)


@router.post("/global/mutations/preview")
def preview_global_menu_mutation(
    request: GlobalMenuMutationPreviewRequest,
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_mutation import preview_global_mutation

    try:
        return preview_global_mutation(
            conn,
            action={"mutation_type": request.mutation_type, "payload": request.payload},
            mutation_id=request.mutation_id,
        )
    except Exception as exc:
        _raise_global_menu_error(exc)


@router.post("/global/mutations/preview-local")
def preview_local_global_menu_mutation(
    request: GlobalMenuLocalMutationPreviewRequest,
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_mutation import (
        build_global_action_from_local,
        preview_global_mutation,
    )

    try:
        action = build_global_action_from_local(
            conn,
            mutation_type=request.mutation_type,
            source_local_menu_item_id=request.source_local_menu_item_id,
            source_local_variant_id=request.source_local_variant_id,
            target_local_menu_item_id=request.target_local_menu_item_id,
            target_local_variant_id=request.target_local_variant_id,
            details=request.details,
        )
        return preview_global_mutation(
            conn, action=action, mutation_id=request.mutation_id
        )
    except Exception as exc:
        _raise_global_menu_error(exc)


@router.post("/global/mutations/commit")
def commit_global_menu_mutation(
    request: GlobalMenuMutationCommitRequest,
    conn=Depends(get_authorized_db),
):
    return _commit_global_request(conn, request)


@router.get("/global/mutations/{mutation_id}")
def get_global_menu_mutation_status(
    mutation_id: str,
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_mutation import global_mutation_status

    try:
        return global_mutation_status(conn, mutation_id)
    except Exception as exc:
        _raise_global_menu_error(exc)


def _raise_alias_resolution_error(error: BaseException) -> None:
    from src.core.global_menu_alias_resolution import AliasResolutionError

    if isinstance(error, AliasResolutionError):
        detail = error.payload or {"error": error.message, "code": error.code}
        raise HTTPException(
            status_code=error.status_code or (503 if error.retryable else 409),
            detail=detail,
        ) from error
    _raise_global_menu_error(error)


@router.get("/global/alias-resolutions")
def get_global_menu_alias_resolutions(
    status: str = "all",
    after: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_alias_resolution import fetch_alias_queue

    try:
        return fetch_alias_queue(
            conn,
            status=status,
            after=after,
            limit=limit,
        )
    except Exception as exc:
        _raise_alias_resolution_error(exc)


@router.post("/global/alias-resolutions/preview")
def preview_global_menu_alias_resolution(
    request: GlobalMenuAliasPreviewRequest,
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_alias_resolution import preview_alias_decision

    try:
        return preview_alias_decision(conn, request.model_dump())
    except Exception as exc:
        _raise_alias_resolution_error(exc)


@router.post("/global/alias-resolutions/commit")
def commit_global_menu_alias_resolution(
    request: GlobalMenuAliasCommitRequest,
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_alias_resolution import commit_alias_decision

    try:
        payload = request.model_dump()
        from src.core.sync_identity import get_sync_attribution

        attribution = get_sync_attribution(conn)
        payload["uploaded_by"] = attribution.get("employee")
        payload["uploaded_from"] = attribution.get("device")
        return commit_alias_decision(conn, payload)
    except Exception as exc:
        _raise_alias_resolution_error(exc)


@router.get("/global/alias-resolutions/{mutation_id}")
def get_global_menu_alias_resolution_status(
    mutation_id: str,
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_alias_resolution import alias_decision_status

    try:
        return alias_decision_status(conn, mutation_id)
    except Exception as exc:
        _raise_alias_resolution_error(exc)


@router.get("/global/alias-reconciliation/plan")
def get_global_menu_alias_reconciliation_plan(conn=Depends(get_authorized_db)):
    from src.core.global_menu_alias_resolution import (
        fetch_alias_reconciliation_plan,
    )

    try:
        return fetch_alias_reconciliation_plan(conn)
    except Exception as exc:
        _raise_alias_resolution_error(exc)


@router.get("/global/alias-reconciliation/status")
def get_global_menu_alias_reconciliation_status(conn=Depends(get_authorized_db)):
    from src.core.global_menu_alias_resolution import (
        fetch_alias_reconciliation_status,
    )

    try:
        return fetch_alias_reconciliation_status(conn)
    except Exception as exc:
        _raise_alias_resolution_error(exc)


@router.post("/global/resolution-context")
def get_global_menu_resolution_context(
    request: GlobalMenuResolutionContextRequest,
    conn=Depends(get_authorized_db),
):
    from src.core.global_menu_mutation import global_resolution_context

    try:
        return global_resolution_context(
            conn,
            local_menu_item_id=request.local_menu_item_id,
            local_variant_id=request.local_variant_id,
        )
    except Exception as exc:
        _raise_global_menu_error(exc)


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
    reader: ScopedReader = Depends(get_reader),
):
    """Get menu items with optional filtering.

    All Stores groups by the durable name/type dimensions and recomputes every
    rate from combined counts; transient menu_item_id never groups (plan §7.3).
    """

    def query(conn, _profile):
        rows = df_to_json(
            menu_queries.fetch_menu_stats(
                conn,
                name_search=name_search,
                type_choice=type_choice,
                start_date=start_date,
                end_date=end_date,
                selected_weekdays=days,
                include_identity=reader.is_all,
            )
        )
        return (
            menu_identity_payload(conn, rows, item_field="menu_item_id")
            if reader.is_all
            else rows
        )

    def reduce(pairs):
        rows, coverage = group_menu_identity_rows(
            pairs,
            legacy_group_by=("Item Name", "Type"),
            spec={
                "As Addon (Qty)": Sum(),
                "As Item (Qty)": Sum(),
                "Total Sold (Qty)": Sum(),
                "Total Revenue": Sum(),
                "Repeat Revenue": Sum(),
                "Total GMS": Sum(),
                "Total ML": Sum(),
                "Total COUNT": Sum(),
                "Reorder Count": Sum(),
                "Repeat Customer (Lifetime)": Sum(),
                "Unique Customers": Sum(),
                "Reorder Rate %": Ratio(
                    "Repeat Customer (Lifetime)", "Unique Customers", scale=100.0
                ),
                "Repeat Revenue %": Ratio("Repeat Revenue", "Total Revenue", scale=100.0),
            },
            sort_by="Total Revenue",
            descending=True,
            item_id_field="menu_item_id",
            canonical_item_name_field="Item Name",
            canonical_item_type_field="Type",
            contributor_fields=("menu_item_id",),
        )
        return identity_aware_federated_data(rows, coverage)

    return reader.read(query, reduce)


@router.get("/types")
def get_menu_types(reader: ScopedReader = Depends(get_reader)):
    """Get list of menu item types"""

    def query(conn, _profile):
        return menu_queries.fetch_menu_types(conn)

    def reduce(pairs):
        combined = []
        for _profile, types in pairs:
            for menu_type in types or []:
                if menu_type not in combined:
                    combined.append(menu_type)
        return sorted(combined, key=lambda value: str(value))

    return reader.read(query, reduce)


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
    reader: ScopedReader = Depends(get_reader),
):
    """Rolling quantity or unit-volume totals by menu item (Menu → Summary)."""
    if mode not in ("volume", "quantity"):
        raise HTTPException(status_code=400, detail="mode must be 'volume' or 'quantity'")
    window_columns = (
        "day_1", "day_2", "day_3", "day_5", "day_7", "day_14",
        "month_1", "month_2", "lifetime",
    )

    def query(conn, _profile):
        # This executes inside ScopedReader's per-profile timezone context.
        end_bd = as_of_date or get_current_business_date()
        # Menu catalogs are small, so All Stores reads every row once and
        # paginates after grouping instead of paging each store separately.
        df, count, err = menu_queries.fetch_menu_summary_rollups(
            conn,
            mode=mode,
            as_of_date=end_bd,
            page=1 if reader.is_all else page,
            page_size=MENU_FEDERATION_ROW_LIMIT if reader.is_all else page_size,
            name_search=name_search,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )
        if err:
            raise HTTPException(status_code=500, detail=err)
        if reader.is_all:
            _enforce_menu_federation_bound(count, "menu summary")
        rows = df_to_json(df)
        if reader.is_all:
            identity = menu_identity_payload(conn, rows, item_field="menu_item_id")
            rows = identity["rows"]
        else:
            identity = None
        return {
            "data": rows,
            "total": count,
            "as_of_date": end_bd,
            "identity": identity["identity"] if identity else None,
        }

    def reduce(pairs):
        group_by = ("name", "type", "unit") if mode == "volume" else ("name", "type")
        identity_pairs = [
            (
                profile,
                {"rows": value["data"], "identity": value.get("identity") or {}},
            )
            for profile, value in pairs
        ]
        rows, coverage = group_menu_identity_rows(
            identity_pairs,
            legacy_group_by=group_by,
            spec={column: Sum() for column in window_columns},
            sort_by=sort_by if sort_by in window_columns else "lifetime",
            descending=sort_desc,
            contributor_fields=("menu_item_id", "lifetime"),
            item_id_field="menu_item_id",
            canonical_item_name_field="name",
            canonical_item_type_field="type",
            additional_group_by=("unit",) if mode == "volume" else (),
        )
        start = (page - 1) * page_size
        as_of_dates = {
            profile.restaurant_id: value["as_of_date"] for profile, value in pairs
        }
        return identity_aware_federated_data({
            "data": rows[start:start + page_size],
            "total": len(rows),
            "page": page,
            "page_size": page_size,
            "as_of_date": max(as_of_dates.values(), default=None),
            "as_of_dates": as_of_dates,
        }, coverage)

    result = reader.read(query, reduce)
    if reader.is_all:
        return result
    return {**result, "page": page, "page_size": page_size}


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
    reader: ScopedReader = Depends(get_reader),
):
    """
    Daily quantity, volume, and revenue for selected menu items (Menu Summary chart).

    Uses the same event-level definitions as Menu → Summary rollups and Menu Items revenue.

    All Stores accepts every contributor ID behind a combined row; each store
    matches only its own IDs and the series are combined by item name and date.
    """
    ids = tuple(x.strip() for x in menu_item_ids.split(",") if x.strip())
    if not ids:
        raise HTTPException(status_code=400, detail="menu_item_ids is required")
    max_ids = 15 * max(1, len(reader.scope.profiles)) if reader.is_all else 15
    if len(ids) > max_ids:
        raise HTTPException(status_code=400, detail=f"At most {max_ids} menu items per request")

    def query(conn, _profile):
        # Defaults are profile-local; explicit dates remain identical for all.
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
        rows = df_to_json(df)
        identity = menu_identity_payload(conn, rows, item_field="menu_item_id") if reader.is_all else None
        return {
            "data": identity["rows"] if identity else rows,
            "identity": identity["identity"] if identity else None,
            "start_date": start_bd,
            "end_date": end_bd,
        }

    def reduce(pairs):
        start_dates = {
            profile.restaurant_id: value["start_date"] for profile, value in pairs
        }
        end_dates = {
            profile.restaurant_id: value["end_date"] for profile, value in pairs
        }
        identity_pairs = [
            (profile, {"rows": value["data"], "identity": value.get("identity") or {}})
            for profile, value in pairs
        ]
        rows, coverage = group_menu_identity_rows(
                identity_pairs,
                legacy_group_by=("menu_item_name", "date"),
                spec={"quantity": Sum(), "volume": Sum(), "revenue": Sum()},
                sort_by="date",
                descending=False,
                contributor_fields=("menu_item_id",),
                item_id_field="menu_item_id",
                canonical_item_name_field="menu_item_name",
                additional_group_by=("date",),
            )
        return identity_aware_federated_data({
            "data": rows,
            "start_date": min(start_dates.values(), default=None),
            "end_date": max(end_dates.values(), default=None),
            "start_dates": start_dates,
            "end_dates": end_dates,
        }, coverage)

    result = reader.read(query, reduce)
    if reader.is_all:
        return result
    return result


@router.get("/items-view")
def get_menu_items_view(
    page: int = 1, 
    page_size: int = 50, 
    sort_by: str = "total_revenue", 
    sort_desc: bool = True,
    filters: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    reader: ScopedReader = Depends(get_reader),
):
    """Paginated view of menu_items_summary_view"""
    filter_dict = json.loads(filters) if filters else {}

    def query(conn, _profile):
        fetch_page = 1 if reader.is_all else page
        fetch_size = MENU_FEDERATION_ROW_LIMIT if reader.is_all else page_size
        if start_date or end_date:
            df, count, err = menu_queries.fetch_menu_items_summary(
                conn,
                page=fetch_page,
                page_size=fetch_size,
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
                fetch_page,
                fetch_size,
                sort_by,
                "DESC" if sort_desc else "ASC",
                filter_dict,
            )
        if err:
            raise HTTPException(500, err)
        if reader.is_all:
            _enforce_menu_federation_bound(count, "menu items")
        rows = df_to_json(df)
        identity = menu_identity_payload(conn, rows, item_field="menu_item_id") if reader.is_all else None
        return {
            "data": identity["rows"] if identity else rows,
            "identity": identity["identity"] if identity else None,
            "total": count,
        }

    def reduce(pairs):
        identity_pairs = [
            (profile, {"rows": value["data"], "identity": value.get("identity") or {}})
            for profile, value in pairs
        ]
        rows, coverage = group_menu_identity_rows(
            identity_pairs,
            legacy_group_by=("name", "type"),
            spec={
                "total_revenue": Sum(),
                "total_sold": Sum(),
                "sold_as_item": Sum(),
                "sold_as_addon": Sum(),
                "is_active": AnyTrue(),
            },
            sort_by=sort_by,
            descending=sort_desc,
            contributor_fields=("menu_item_id", "total_revenue", "total_sold"),
            item_id_field="menu_item_id",
            canonical_item_name_field="name",
            canonical_item_type_field="type",
        )
        start = (page - 1) * page_size
        return identity_aware_federated_data(
            {"data": rows[start:start + page_size], "total": len(rows)}, coverage
        )

    result = reader.read(query, reduce)
    if reader.is_all:
        result["data"].update({"page": page, "page_size": page_size})
        return result
    return {**result, "page": page, "page_size": page_size}


@router.get("/variants-view")
def get_variants_view(
    page: int = 1, 
    page_size: int = 50, 
    sort_by: str = "variant_name", 
    sort_desc: bool = False,
    filters: Optional[str] = None,
    reader: ScopedReader = Depends(get_reader),
):
    """Paginated view of variants"""
    filter_dict = json.loads(filters) if filters else {}

    def query(conn, _profile):
        df, count, err = table_queries.fetch_paginated_table(
            conn,
            "variants",
            1 if reader.is_all else page,
            MENU_FEDERATION_ROW_LIMIT if reader.is_all else page_size,
            sort_by,
            "DESC" if sort_desc else "ASC",
            filter_dict,
        )
        if err:
            raise HTTPException(500, err)
        if reader.is_all:
            _enforce_menu_federation_bound(count, "variants")
        rows = df_to_json(df)
        identity = menu_identity_payload(
            conn,
            rows,
            item_field=None,
            variant_field="variant_id",
            variant_only=True,
        ) if reader.is_all else None
        return {
            "data": identity["rows"] if identity else rows,
            "identity": identity["identity"] if identity else None,
            "total": count,
        }

    def reduce(pairs):
        # Variants combine on their durable dimensions: name, unit, and value.
        identity_pairs = [
            (profile, {"rows": value["data"], "identity": value.get("identity") or {}})
            for profile, value in pairs
        ]
        rows, coverage = group_menu_identity_rows(
            identity_pairs,
            legacy_group_by=("variant_name", "unit", "value"),
            spec={"description": First(), "is_verified": AllTrue()},
            sort_by=sort_by,
            descending=sort_desc,
            contributor_fields=("variant_id",),
            item_id_field=None,
            variant_id_field="variant_id",
            canonical_variant_name_field="variant_name",
            identity_kind="variant",
        )
        start = (page - 1) * page_size
        return identity_aware_federated_data(
            {"data": rows[start:start + page_size], "total": len(rows)}, coverage
        )

    result = reader.read(query, reduce)
    if reader.is_all:
        result["data"].update({"page": page, "page_size": page_size})
        return result
    return {**result, "page": page, "page_size": page_size}


@router.get("/matrix")
def get_menu_matrix(reader: ScopedReader = Depends(get_reader)):
    """Full menu matrix for client-side pagination"""

    def query(conn, _profile):
        rows = df_to_json(menu_queries.fetch_menu_matrix(conn))
        return (
            menu_identity_payload(
                conn,
                rows,
                item_field="menu_item_id",
                variant_field="variant_id",
            )
            if reader.is_all
            else rows
        )

    def reduce(pairs):
        rows, coverage = group_menu_identity_rows(
            pairs,
            legacy_group_by=("name", "type", "variant_name"),
            spec={
                "mapping_count": Sum(),
                "order_count": Sum(),
                # Stores can price the same variant differently, so there is no
                # single "the" price. Show the lowest and keep every store's own
                # price in `contributors` rather than letting sort order decide.
                "price": Min(),
                "is_active": AnyTrue(),
                "addon_eligible": AnyTrue(),
                "delivery_eligible": AnyTrue(),
                "is_verified": AllTrue(),
            },
            sort_by="name",
            descending=False,
            contributor_fields=("menu_item_id", "variant_id", "price"),
            item_id_field="menu_item_id",
            variant_id_field="variant_id",
            canonical_item_name_field="name",
            canonical_item_type_field="type",
            canonical_variant_name_field="variant_name",
        )
        return identity_aware_federated_data(rows, coverage)

    return reader.read(query, reduce)


# --- Dropdown List Endpoints ---

@router.get("/list")
def get_menu_list(reader: ScopedReader = Depends(get_reader)):
    """Lightweight list of all items for dropdowns"""

    def reduce(pairs):
        # Menu edits are physical-store-only, so this list stays a plain
        # attributed union rather than a merged catalog.
        return union_rows(pairs, key_fields=("menu_item_id",), sort_by="name", descending=False)

    return reader.read(_menu_list_rows, reduce)


def _menu_list_rows(conn, _profile=None):
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
def get_variants_list(reader: ScopedReader = Depends(get_reader)):
    """Lightweight list of all variants for dropdowns"""

    def query(conn, _profile):
        cursor = conn.cursor()
        cursor.execute("SELECT variant_id, variant_name FROM variants ORDER BY variant_name")
        data = [{"variant_id": row[0], "name": row[1]} for row in cursor.fetchall()]
        cursor.close()
        return data

    def reduce(pairs):
        return union_rows(pairs, key_fields=("variant_id",), sort_by="name", descending=False)

    return reader.read(query, reduce)


@router.post("/variants/create")
def create_variant_type_endpoint(req: CreateVariantTypeRequest, conn=Depends(get_authorized_db)):
    """Create a new variant type; uses the clustering pipeline's deterministic ID scheme."""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.create_variant_type(
        conn,
        req.variant_name,
        req.description,
        req.unit,
        req.value,
    )
    return _finalize_menu_edit_response(res)


# --- Merge Logic ---

@router.get("/merge/history")
def get_merge_history(limit: int = 20, offset: int = 0, conn=Depends(get_db)):
    """Get paginated merge/resolution history"""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    from src.core.global_menu_schema import resolve_global_menu_capability

    global_capability = resolve_global_menu_capability(conn)
    if global_capability is not None and global_capability.active:
        from src.core.global_menu_history import list_cached_global_menu_history

        return list_cached_global_menu_history(
            conn,
            capability=global_capability,
            limit=limit,
            offset=offset,
        )
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
    target_variant_id: Optional[str] = None,
    conn=Depends(get_db),
):
    """Preview the impact of merging a source menu item into a target."""
    res = menu_utils.preview_merge_menu_items(conn, source_id, target_id, source_variant_id)
    if res['status'] == 'error':
        raise HTTPException(400, res['message'])
    if _global_menu_active(conn):
        from src.core.global_menu_mutation import (
            build_global_action_from_local,
            preview_global_mutation,
        )

        try:
            global_preview = preview_global_mutation(
                conn,
                action=build_global_action_from_local(
                    conn,
                    mutation_type="variant_merge" if source_variant_id else "menu_merge",
                    source_local_menu_item_id=source_id,
                    source_local_variant_id=source_variant_id,
                    target_local_menu_item_id=target_id,
                    target_local_variant_id=target_variant_id,
                ),
            )
        except Exception as exc:
            _raise_global_menu_error(exc)
        res["global_menu"] = global_preview
    return res


@router.post("/merge")
def execute_merge(req: MergeRequest, conn=Depends(get_authorized_db)):
    """Merge source menu item into target"""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
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


@router.post("/retype")
def retype_menu_item_endpoint(req: RetypeMenuItemRequest, conn=Depends(get_authorized_db)):
    """Change a menu item's type; relinks all history into the retyped item."""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.retype_menu_item(conn, req.menu_item_id, req.new_type)
    return _finalize_menu_edit_response(res)


@router.post("/merge/undo")
def undo_merge(req: UndoMergeRequest, conn=Depends(get_authorized_db)):
    """Undo a previous merge operation"""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.undo_merge(conn, req.merge_id)
    return _finalize_menu_edit_response(res)


@router.post("/merge/pull-from-cloud")
def pull_menu_merges_from_cloud(limit: int = 100, conn=Depends(get_authorized_db)):
    """Manually pull menu merge events from cloud and replay them locally."""
    _ensure_legacy_menu_pull_allowed(conn)
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
def pull_menu_mapping_verifications_from_cloud(
    limit: int = 100, conn=Depends(get_authorized_db)
):
    """Pull menu mapping verification events from cloud and apply locally."""
    _ensure_legacy_menu_pull_allowed(conn)
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
    from src.core.global_menu_schema import list_global_menu_quarantine

    global_conflicts = list_global_menu_quarantine(
        conn, include_resolved=include_resolved
    )
    global_unresolved_count = sum(
        1 for conflict in global_conflicts if not conflict.get("resolved_at")
    )
    notices = list_supersede_notices(conn, include_acknowledged=include_resolved)
    open_notices = sum(1 for notice in notices if not notice.get("acknowledged_at"))
    return {
        "count": unresolved_count + global_unresolved_count + open_notices,
        "conflicts": conflicts,
        "global_menu_conflicts": global_conflicts,
        "supersede_notices": notices,
    }


@router.post("/sync-conflicts/notices/{notice_id}/acknowledge")
def acknowledge_supersede_notice_endpoint(
    notice_id: int, conn=Depends(get_authorized_db)
):
    """Acknowledge a 'your resolution was superseded' notice."""
    from src.core.menu_assignment_apply import acknowledge_supersede_notice

    acknowledged = acknowledge_supersede_notice(conn, notice_id)
    if not acknowledged:
        raise HTTPException(status_code=404, detail="Open supersede notice not found")
    conn.commit()
    return {"status": "ok", "notice_id": notice_id}


@router.post("/sync-conflicts/{remote_event_id}/dismiss")
def dismiss_sync_conflict_endpoint(
    remote_event_id: str, conn=Depends(get_authorized_db)
):
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
    conn=Depends(get_authorized_db),
):
    """Manually pull the latest menu bootstrap snapshot from cloud and apply it locally."""
    _ensure_legacy_menu_pull_allowed(conn)
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
def execute_remap(req: RemapRequest, conn=Depends(get_authorized_db)):
    """Remap an order item to a different menu item/variant"""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.remap_order_item_cluster(conn, req.order_item_id, req.new_menu_item_id, req.new_variant_id)
    if res.get("status") == "success":
        # A remap resolves any open silent-reuse anomaly for this id.
        try:
            from src.core.mapping_anomalies import close_anomalies_for_order_item

            close_anomalies_for_order_item(conn, req.order_item_id, resolution="remapped")
        except Exception:
            pass
    return _finalize_menu_edit_response(res)


@router.post("/variant-mapping/update")
def update_variant_mapping(
    req: UpdateVariantMappingRequest, conn=Depends(get_authorized_db)
):
    """Update an existing menu item + variant mapping to a different variant everywhere it is used."""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
    _ensure_menu_edit_allowed(conn)
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
    try:
        from src.core.global_menu_schema import resolve_global_menu_capability

        include_global_identity_gaps = resolve_global_menu_capability(
            conn
        ).resolution_ready
    except Exception:
        include_global_identity_gaps = False
    df = menu_queries.fetch_unverified_items(
        conn, include_global_identity_gaps=include_global_identity_gaps
    )
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
def resolve_variant_endpoint(
    req: ResolveVariantRequest, conn=Depends(get_authorized_db)
):
    """Resolve a single unresolved menu item + variant pair."""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
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
def verify_item_endpoint(req: VerifyRequest, conn=Depends(get_authorized_db)):
    """Verify a menu item, optionally renaming it"""
    if _global_menu_request_active(conn, req):
        return _commit_global_request(conn, req)
    _ensure_menu_edit_allowed(conn)
    res = menu_utils.verify_item(conn, req.menu_item_id, req.new_name, req.new_type, req.new_variant_id)
    return _finalize_menu_edit_response(res)


@router.post("/resolutions/verify-assignment")
def verify_assignment_endpoint(
    req: VerifyAssignmentRequest, conn=Depends(get_authorized_db)
):
    """Verify exact assignment rows through the central verification stream."""
    res = menu_utils.verify_menu_mapping_assignments(
        conn,
        req.assignment_order_item_ids,
        expected_global_menu_item_id=req.expected_global_menu_item_id,
        expected_global_variant_id=req.expected_global_variant_id,
        mutation_id=req.mutation_id,
    )
    return _finalize_menu_edit_response(res)


# --- Suspect mappings (silent-reuse guard) ---

@router.get("/resolutions/suspect-mappings")
def get_suspect_mappings(conn=Depends(get_db)):
    """
    Open silent-reuse anomalies: PetPooja ids whose incoming name resolves to a
    different product core than the id's verified mapping. Human triages each —
    dismiss (benign relabel) or remap (real reuse) via POST /menu/remap.
    """
    return list_open_anomalies(conn)


@router.post("/resolutions/suspect-mappings/{anomaly_id}/dismiss")
def dismiss_suspect_mapping(anomaly_id: int, conn=Depends(get_authorized_db)):
    """Mark a suspect mapping as a benign relabel; the core is remembered so it won't re-fire."""
    res = dismiss_anomaly(conn, anomaly_id)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message"))
    return res
