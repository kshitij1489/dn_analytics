"""Typed, fail-closed transport helpers for Dachnona contract revision 1.2."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from src.core.profiles import ALL_STORES_TOKEN, ProfileSelectionRequired


USER_AGENT = "DachnonaAnalyticsDesktop/1.2"
logger = logging.getLogger(__name__)
NON_RETRYABLE_CODES = {
    "restaurant_selector_missing",
    "restaurant_selector_invalid",
    "restaurant_forbidden",
    "unknown_restaurant",
    "restaurant_disabled",
    "retired_parameter",
    "retired_field",
    "restaurant_selector_not_a_parameter",
    "invalid_page_parameter",
}


@dataclass
class CentralAPIError(RuntimeError):
    message: str
    code: str = "central_api_error"
    status_code: Optional[int] = None
    retryable: bool = False

    def __str__(self) -> str:
        prefix = f"{self.code}: " if self.code else ""
        return f"{prefix}{self.message}"


def restaurant_id_from_connection(conn) -> str:
    try:
        row = conn.execute(
            "SELECT restaurant_id FROM restaurant_profile_identity WHERE singleton_id=1"
        ).fetchone()
    except Exception as exc:
        raise ProfileSelectionRequired("Analytics database has no profile identity") from exc
    restaurant_id = str(row[0]).strip() if row and row[0] is not None else ""
    if not restaurant_id or restaurant_id == ALL_STORES_TOKEN:
        raise ProfileSelectionRequired("Select one physical restaurant")
    return restaurant_id


def scoped_headers(
    conn,
    *,
    auth_kind: str,
    credential: Optional[str],
    content_type: Optional[str] = None,
) -> Dict[str, str]:
    """Build headers only after a physical profile identity is proven locally."""
    restaurant_id = restaurant_id_from_connection(conn)
    from src.core.profiles import ProfileError, registered_profile_authorization

    if registered_profile_authorization(restaurant_id, conn) == "unauthorized":
        raise ProfileError(f"Restaurant profile is not authorized: {restaurant_id}")
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Restaurant-ID": restaurant_id,
    }
    token = str(credential or "").strip()
    if auth_kind == "analytics":
        if token:
            headers["X-API-Key"] = token
    elif auth_kind == "sync":
        if token:
            headers["Authorization"] = f"Bearer {token}"
    elif auth_kind == "manual_pull":
        if token:
            headers["X-API-Key"] = token
    else:
        raise ValueError(f"Unsupported central auth kind: {auth_kind}")
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def unscoped_analytics_headers(api_key: str) -> Dict[str, str]:
    key = str(api_key or "").strip()
    return {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-API-Key": key,
    }


def error_from_response(response, *, conn=None) -> CentralAPIError:
    body: Mapping[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        pass
    message = str(body.get("error") or body.get("detail") or f"HTTP {response.status_code}")
    inferred_code = "invalid_api_key" if "invalid api key" in message.lower() else "http_error"
    code = str(body.get("code") or inferred_code)
    retryable = response.status_code >= 500 and code not in NON_RETRYABLE_CODES
    if code == "restaurant_forbidden" and conn is not None:
        try:
            from src.core.profiles import (
                mark_profile_unauthorized,
                registered_profile_authorization,
            )

            restaurant_id = restaurant_id_from_connection(conn)
            if registered_profile_authorization(restaurant_id, conn) is not None:
                mark_profile_unauthorized(restaurant_id)
        except Exception:
            # Preserve the server error even if the control database cannot be
            # updated. The current call must still fail closed.
            logger.exception("Failed to mark centrally-forbidden restaurant unauthorized")
    return CentralAPIError(message, code=code, status_code=response.status_code, retryable=retryable)


def response_error_text(response, *, conn=None) -> str:
    error = error_from_response(response, conn=conn)
    return str(error)


def validate_no_retired_query_params(params: Optional[Mapping[str, Any]]) -> None:
    if not params:
        return
    retired = {"scope_key", "restaurant", "restaurant_id", "legacy"}.intersection(params)
    if retired:
        raise CentralAPIError(
            f"Retired query parameter(s): {', '.join(sorted(retired))}",
            code="retired_parameter",
        )
