"""Server-managed restaurant profile discovery and local selection API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.central_api import error_from_response, unscoped_analytics_headers
from src.core.db.control import get_global_config
from src.core.profiles import (
    ALL_STORES_TOKEN,
    ProfileError,
    bind_and_select_profile,
    federation_profiles,
    list_profiles,
    select_all_stores,
    selected_profile,
    selected_selection,
    upsert_allowed_restaurants,
)
from utils.api_client import normalize_integration_orders_base_url


router = APIRouter()


class ProfileSelection(BaseModel):
    restaurant_id: str = Field(..., min_length=1)
    confirm_existing_binding: bool = False


def _serialize(profile):
    return profile.to_dict()


@router.get("/stores")
def get_stores():
    return [_serialize(profile) for profile in list_profiles()]


def _all_stores_state():
    """What the selector needs to enable (or explain) the All Stores option."""
    members = federation_profiles()
    return {
        "available": len(members) >= 2,
        "member_count": len(members),
        "members": [profile.restaurant_id for profile in members],
    }


@router.get("/stores/selection")
def get_store_selection():
    selection = selected_selection()
    all_stores = _all_stores_state()
    if selection["selection_mode"] == "all":
        return {
            "profile": None,
            "selection_mode": "all",
            "all_stores": all_stores,
        }
    try:
        return {
            "profile": _serialize(selected_profile()),
            "selection_mode": "restaurant",
            "all_stores": all_stores,
        }
    except ProfileError as exc:
        return {
            "profile": None,
            "selection_mode": selection["selection_mode"],
            "all_stores": all_stores,
            "error": str(exc),
            "code": getattr(exc, "code", "profile_error"),
        }


@router.post("/stores/refresh")
def refresh_stores():
    config = get_global_config(("integration_orders_url", "integration_orders_key"))
    base_url = normalize_integration_orders_base_url(config.get("integration_orders_url") or "")
    api_key = config.get("integration_orders_key") or ""
    if not base_url or not api_key:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Configure the Orders Integration URL and API key first",
                "code": "restaurant_list_not_configured",
            },
        )
    try:
        import requests

        response = requests.get(
            f"{base_url}/restaurants/",
            headers=unscoped_analytics_headers(api_key),
            timeout=30,
        )
        if response.status_code >= 400:
            error = error_from_response(response)
            raise HTTPException(
                status_code=response.status_code,
                detail={"error": error.message, "code": error.code},
            )
        from src.core.analytics_stream_contract import parse_allowed_restaurants

        restaurants = parse_allowed_restaurants(response.json())
        return {"profiles": [_serialize(p) for p in upsert_allowed_restaurants(restaurants)]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": str(exc), "code": "restaurant_list_transport_error"},
        ) from exc


@router.post("/stores/select")
def select_store(selection: ProfileSelection):
    try:
        if selection.restaurant_id == ALL_STORES_TOKEN:
            # A local selection mode, not a profile: no identity row, no
            # database, and nothing that could reach the central server.
            select_all_stores()
            return {
                "profile": None,
                "selection_mode": "all",
                "all_stores": _all_stores_state(),
            }
        profile = bind_and_select_profile(
            selection.restaurant_id,
            confirm_existing_binding=selection.confirm_existing_binding,
        )
        return {
            "profile": _serialize(profile),
            "selection_mode": "restaurant",
            "all_stores": _all_stores_state(),
        }
    except ProfileError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": str(exc), "code": getattr(exc, "code", "profile_error")},
        ) from exc
