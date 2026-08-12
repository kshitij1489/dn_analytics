"""Scoped revision-1.8 alias queue, decision and reconciliation transport."""

from __future__ import annotations

import re
import uuid
import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from src.core.central_api import (
    CentralAPIError,
    error_from_response,
    scoped_headers,
    validate_no_retired_query_params,
)
from src.core.config.cloud_sync_config import (
    get_cloud_sync_config,
    get_global_menu_editor_key,
)
from src.core.global_menu_schema import require_global_menu_capability

ALIAS_SCHEMA_VERSION = 1
ALIAS_ROUTE = "/desktop-analytics-sync/global-menu/alias-resolutions"
ALIAS_RECONCILIATION_ROUTE = (
    "/desktop-analytics-sync/global-menu/alias-reconciliation"
)
ALIAS_QUEUE_STATES = frozenset(
    {"all", "pending", "approved", "stale", "applied", "quarantined"}
)
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DECIMAL_PATTERN = re.compile(r"^\d+\.\d{2}$")
ALIAS_LOCATOR_TYPES = frozenset({"pos_item", "pos_addon"})
ALIAS_CANDIDATE_REASONS = frozenset(
    {"exact_existing_alias", "unique_itemcode", "exact_identity", "manual"}
)
ALIAS_CONFLICT_CODES = frozenset(
    {
        "ambiguous_itemcode",
        "ambiguous_variant",
        "locator_kind_collision",
        "corrupt_redirect_chain",
        "target_outside_group",
        "missing_canonical_target",
        "stale_alias_observation",
        "unknown_group_pos_alias",
        "no_deterministic_candidate",
        "price_review_required",
        "unobserved_canonical_entry",
    }
)


@dataclass
class AliasResolutionError(CentralAPIError):
    payload: Optional[Dict[str, Any]] = None


def _connection(conn) -> tuple[str, str]:
    base_url, api_key = get_cloud_sync_config(conn)
    if not base_url or not api_key:
        raise AliasResolutionError(
            "Alias resolution requires a cloud connection",
            code="cloud_sync_not_configured",
        )
    return base_url, api_key


def _headers(conn, api_key: str, *, editor_required: bool) -> Dict[str, str]:
    headers = scoped_headers(
        conn,
        auth_kind="sync",
        credential=api_key,
        content_type="application/json",
    )
    if editor_required:
        editor_key = get_global_menu_editor_key(conn)
        if not editor_key:
            raise AliasResolutionError(
                "Alias resolution requires a configured global-menu editor credential",
                code="global_menu_editor_required",
                status_code=403,
            )
        headers["X-Global-Menu-Key"] = editor_key
    return headers


def _request(
    conn,
    method: str,
    route: str,
    *,
    editor_required: bool,
    params: Optional[Mapping[str, Any]] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    import requests

    validate_no_retired_query_params(params)
    base_url, api_key = _connection(conn)
    headers = _headers(conn, api_key, editor_required=editor_required)
    url = f"{base_url}{route}"
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, params=params, timeout=60)
        else:
            response = requests.post(
                url,
                headers=headers,
                json=dict(payload or {}),
                timeout=60,
            )
    except requests.RequestException as exc:
        raise AliasResolutionError(
            str(exc),
            code="global_menu_alias_transport_error",
            retryable=True,
        ) from exc
    try:
        body = response.json() if response.content else {}
    except Exception as exc:
        raise AliasResolutionError(
            "Alias response was not valid JSON",
            code="global_menu_alias_response_invalid",
            status_code=response.status_code,
        ) from exc
    if not isinstance(body, dict):
        raise AliasResolutionError(
            "Alias response must be a JSON object",
            code="global_menu_alias_response_invalid",
            status_code=response.status_code,
        )
    if response.status_code >= 400:
        central_error = error_from_response(response, conn=conn)
        raise AliasResolutionError(
            central_error.message,
            code=central_error.code,
            status_code=central_error.status_code,
            retryable=central_error.retryable,
            payload=body,
        )
    return body


def _require_digest(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not DIGEST_PATTERN.fullmatch(normalized):
        raise AliasResolutionError(
            f"{field} must be a lowercase SHA-256 digest",
            code="global_menu_alias_response_invalid",
        )
    return normalized


def _require_alias_capability(conn):
    capability = require_global_menu_capability(conn)
    if not capability.resolution_advertised:
        raise AliasResolutionError(
            "Global menu resolution is not enabled for this restaurant",
            code="global_menu_resolution_disabled",
            status_code=403,
        )
    return capability


def _validate_base(body: Mapping[str, Any], capability) -> Dict[str, Any]:
    if body.get("schema_version") != ALIAS_SCHEMA_VERSION:
        raise AliasResolutionError(
            "Unsupported alias schema version",
            code="global_menu_alias_response_invalid",
        )
    if str(body.get("menu_group_id") or "") != capability.menu_group_id:
        raise AliasResolutionError(
            "Alias response belongs to another menu group",
            code="global_menu_alias_response_invalid",
        )
    if not isinstance(body.get("menu_group_revision"), int):
        raise AliasResolutionError(
            "Alias response has an invalid group revision",
            code="global_menu_alias_response_invalid",
        )
    return dict(body)


def _validate_conflicts(value: Any) -> None:
    if not isinstance(value, list):
        raise AliasResolutionError(
            "Alias conflicts must be an array",
            code="global_menu_alias_response_invalid",
        )
    for conflict in value:
        if (
            not isinstance(conflict, dict)
            or conflict.get("code") not in ALIAS_CONFLICT_CODES
            or not isinstance(conflict.get("message"), str)
        ):
            raise AliasResolutionError(
                "Alias response contains an unknown conflict",
                code="global_menu_alias_response_invalid",
            )


def _validate_decimal(value: Any, field: str, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
        raise AliasResolutionError(
            f"{field} must be an exact two-decimal string",
            code="global_menu_alias_response_invalid",
        )


def _validate_cursor(value: Any, capability) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise AliasResolutionError(
            "Alias cursor must be a string or null",
            code="global_menu_alias_response_invalid",
        )
    try:
        padded = value + "=" * (-len(value) % 4)
        cursor = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:
        raise AliasResolutionError(
            "Alias cursor is malformed",
            code="global_menu_alias_response_invalid",
        ) from exc
    if (
        not isinstance(cursor, dict)
        or set(cursor) != {"v", "g", "locator_type", "locator_value"}
        or cursor.get("v") != ALIAS_SCHEMA_VERSION
        or cursor.get("g") != capability.menu_group_id
        or cursor.get("locator_type") not in ALIAS_LOCATOR_TYPES
        or not isinstance(cursor.get("locator_value"), str)
        or not cursor["locator_value"]
    ):
        raise AliasResolutionError(
            "Alias cursor does not match the selected group",
            code="global_menu_alias_response_invalid",
        )


def fetch_alias_queue(
    conn,
    *,
    status: str = "all",
    after: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    capability = _require_alias_capability(conn)
    if status not in ALIAS_QUEUE_STATES or not 1 <= int(limit) <= 500:
        raise AliasResolutionError(
            "Invalid alias queue filter or limit",
            code="invalid_page_parameter",
            status_code=400,
        )
    params: Dict[str, Any] = {"status": status, "limit": int(limit)}
    if after:
        params["after"] = str(after)
    body = _validate_base(
        _request(
            conn,
            "GET",
            ALIAS_ROUTE,
            editor_required=True,
            params=params,
        ),
        capability,
    )
    _require_digest(body.get("observation_digest"), "observation_digest")
    rows = body.get("rows")
    if not isinstance(rows, list) or not isinstance(body.get("has_more"), bool):
        raise AliasResolutionError(
            "Alias queue page has an invalid shape",
            code="global_menu_alias_response_invalid",
        )
    for row in rows:
        if not isinstance(row, dict):
            raise AliasResolutionError(
                "Alias queue row must be an object",
                code="global_menu_alias_response_invalid",
            )
        if (
            row.get("locator_type") not in ALIAS_LOCATOR_TYPES
            or not isinstance(row.get("locator_value"), str)
            or not row["locator_value"]
            or row.get("resolution_state") not in ALIAS_QUEUE_STATES - {"all"}
        ):
            raise AliasResolutionError(
                "Alias queue row has an unknown state",
                code="global_menu_alias_response_invalid",
            )
        _require_digest(row.get("observation_digest"), "row.observation_digest")
        if not isinstance(row.get("evidence"), list):
            raise AliasResolutionError(
                "Alias queue evidence/conflicts must be arrays",
                code="global_menu_alias_response_invalid",
            )
        _validate_conflicts(row.get("conflicts"))
        for evidence in row["evidence"]:
            if not isinstance(evidence, dict):
                raise AliasResolutionError(
                    "Alias evidence row must be an object",
                    code="global_menu_alias_response_invalid",
                )
            _validate_decimal(evidence.get("price"), "evidence.price")
            if evidence.get("variant_value") is not None:
                _validate_decimal(evidence.get("variant_value"), "evidence.variant_value")
        candidate = row.get("candidate")
        if candidate is not None:
            if (
                not isinstance(candidate, dict)
                or candidate.get("reason") not in ALIAS_CANDIDATE_REASONS
            ):
                raise AliasResolutionError(
                    "Alias candidate has an unknown reason",
                    code="global_menu_alias_response_invalid",
                )
            _validate_decimal(
                candidate.get("canonical_price"),
                "candidate.canonical_price",
                nullable=True,
            )
    _validate_cursor(body.get("next_cursor"), capability)
    if body["has_more"] != bool(body.get("next_cursor")):
        raise AliasResolutionError(
            "Alias queue cursor and has_more disagree",
            code="global_menu_alias_response_invalid",
        )
    return body


def preview_alias_decision(conn, payload: Mapping[str, Any]) -> Dict[str, Any]:
    capability = _require_alias_capability(conn)
    body = _validate_base(
        _request(
            conn,
            "POST",
            f"{ALIAS_ROUTE}/preview",
            editor_required=True,
            payload=payload,
        ),
        capability,
    )
    if body.get("status") != "preview" or not isinstance(
        body.get("commit_allowed"), bool
    ):
        raise AliasResolutionError(
            "Alias preview response has an invalid shape",
            code="global_menu_alias_response_invalid",
        )
    _require_digest(body.get("preview_digest"), "preview_digest")
    _require_digest(body.get("observation_digest"), "observation_digest")
    _validate_conflicts(body.get("conflicts"))
    return body


def alias_decision_status(conn, mutation_id: str) -> Dict[str, Any]:
    try:
        normalized = str(uuid.UUID(str(mutation_id)))
    except (ValueError, AttributeError) as exc:
        raise AliasResolutionError(
            "Alias decision mutation id is not a UUID",
            code="global_menu_alias_mutation_invalid",
            status_code=400,
        ) from exc
    capability = _require_alias_capability(conn)
    return _validate_base(
        _request(
            conn,
            "GET",
            f"{ALIAS_ROUTE}/{normalized}",
            editor_required=True,
        ),
        capability,
    )


def commit_alias_decision(conn, payload: Mapping[str, Any]) -> Dict[str, Any]:
    capability = _require_alias_capability(conn)
    mutation_id = str(payload.get("mutation_id") or "").strip()
    try:
        body = _request(
            conn,
            "POST",
            f"{ALIAS_ROUTE}/commit",
            editor_required=True,
            payload=payload,
        )
    except AliasResolutionError as exc:
        if exc.code != "global_menu_alias_transport_error" or not mutation_id:
            raise
        return alias_decision_status(conn, mutation_id)
    body = _validate_base(body, capability)
    if body.get("status") != "applied" or str(body.get("mutation_id")) != mutation_id:
        raise AliasResolutionError(
            "Alias commit response has an invalid shape",
            code="global_menu_alias_response_invalid",
        )
    return body


def fetch_alias_reconciliation_plan(conn) -> Dict[str, Any]:
    capability = _require_alias_capability(conn)
    body = _validate_base(
        _request(
            conn,
            "GET",
            f"{ALIAS_RECONCILIATION_ROUTE}/plan",
            editor_required=False,
        ),
        capability,
    )
    for field in ("catalog_digest", "redirect_digest", "plan_digest"):
        _require_digest(body.get(field), field)
    if not isinstance(body.get("execution_ready"), bool) or not isinstance(
        body.get("conflicts"), list
    ):
        raise AliasResolutionError(
            "Alias plan response has an invalid shape",
            code="global_menu_alias_response_invalid",
        )
    return body


def fetch_alias_reconciliation_status(conn) -> Dict[str, Any]:
    capability = _require_alias_capability(conn)
    body = _validate_base(
        _request(
            conn,
            "GET",
            f"{ALIAS_RECONCILIATION_ROUTE}/status",
            editor_required=False,
        ),
        capability,
    )
    required_objects = ("policy", "initial_reconciliation", "alias_counts")
    if any(not isinstance(body.get(field), dict) for field in required_objects):
        raise AliasResolutionError(
            "Alias status response has an invalid shape",
            code="global_menu_alias_response_invalid",
        )
    if not isinstance(body.get("restaurants"), list):
        raise AliasResolutionError(
            "Alias status restaurants must be an array",
            code="global_menu_alias_response_invalid",
        )
    return body


__all__ = [
    "AliasResolutionError",
    "alias_decision_status",
    "commit_alias_decision",
    "fetch_alias_queue",
    "fetch_alias_reconciliation_plan",
    "fetch_alias_reconciliation_status",
    "preview_alias_decision",
]
