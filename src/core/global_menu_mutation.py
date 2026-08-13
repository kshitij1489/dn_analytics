"""Capability-gated global menu preview/commit/status transport.

Global mutations always name stable server IDs. A timeout is reconciled with a
status lookup; this module never asks callers to blindly repeat an uncertain
POST.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Tuple

from src.core.config.cloud_sync_config import (
    get_cloud_sync_config,
    get_global_menu_editor_key,
)
from src.core.global_menu_identity import global_ids_for_local
from src.core.global_menu_schema import (
    GlobalMenuCapabilityError,
    require_global_menu_capability,
)
from src.core.sync_identity import get_sync_attribution


logger = logging.getLogger(__name__)
GLOBAL_MUTATION_SCHEMA_VERSION = 1
MUTATION_GLOBAL_VARIANT_CREATE = "global_variant.create"
AUTHORITATIVE_LOCATOR_TYPES = frozenset({"pos_item", "pos_addon", "itemcode"})
_PRICE_QUANTUM = Decimal("0.01")
_PRICE_LIMIT = Decimal("100000000")
GLOBAL_MENU_RESOLUTION_MUTATION_TYPES = frozenset(
    {
        "global_item.create",
        "global_variant.create",
        "global_locator.map",
        "global_menu.undo",
    }
)
LOCAL_IDENTITY_KEYS = {
    "menu_item_id",
    "variant_id",
    "local_menu_item_id",
    "local_variant_id",
    "source_id",
    "target_id",
    "source_menu_item_id",
    "target_menu_item_id",
    "source_variant_id",
    "target_variant_id",
    "current_variant_id",
    "new_menu_item_id",
    "new_variant_id",
}


class GlobalMenuMutationError(RuntimeError):
    code = "global_menu_mutation_failed"


def is_global_menu_resolution_mutation(mutation_type: Any) -> bool:
    return str(mutation_type or "").strip() in GLOBAL_MENU_RESOLUTION_MUTATION_TYPES


def _require_write_capability(conn, mutation_type: str):
    return require_global_menu_capability(
        conn,
        for_write=True,
        allow_resolution_write=is_global_menu_resolution_mutation(mutation_type),
    )


def _normalize_price(value: Any, *, field: str = "payload.price") -> str:
    """Return the contract's exact two-decimal price without using floats."""
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise GlobalMenuMutationError(f"{field} is required")
    try:
        raw = Decimal(str(value).strip())
        normalized = raw.quantize(_PRICE_QUANTUM)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GlobalMenuMutationError(
            f"{field} must be a finite number with at most two decimals"
        ) from exc
    if (
        not raw.is_finite()
        or raw != normalized
        or normalized < 0
        or normalized >= _PRICE_LIMIT
    ):
        raise GlobalMenuMutationError(
            f"{field} must be between 0.00 and 99999999.99 with at most two decimals"
        )
    return format(normalized, ".2f")


def _normalize_mutation_payload(
    mutation_type: str,
    raw_payload: Mapping[str, Any],
    *,
    capability,
) -> Dict[str, Any]:
    """Force POS locators onto restaurant scope before the wire boundary."""
    payload = dict(raw_payload)
    if mutation_type == "global_locator.price_update":
        error = GlobalMenuMutationError(
            "Mapping rules do not carry price; restaurant prices stay restaurant-owned"
        )
        error.code = "global_menu_operation_unsupported"
        raise error
    if mutation_type != "global_locator.map":
        return payload
    if payload.get("price") is not None:
        raise GlobalMenuMutationError(
            "A mapping rule cannot carry a price; prices stay restaurant-owned"
        )
    locator_type = str(payload.get("locator_type") or "").strip()
    if locator_type not in AUTHORITATIVE_LOCATOR_TYPES:
        raise GlobalMenuMutationError(
            "Display-name aliases are suggestions and cannot become mapping rules"
        )
    if locator_type in {"pos_item", "pos_addon"}:
        payload["rule_scope"] = "restaurant"
        payload["restaurant_id"] = str(capability.restaurant_id or "").strip()
        payload["confirm_group_wide"] = False
        if not payload["restaurant_id"]:
            raise GlobalMenuMutationError(
                "A POS locator mapping requires the selected restaurant id"
            )
    else:
        if str(payload.get("global_variant_id") or "").strip():
            raise GlobalMenuMutationError(
                "An itemcode mapping targets the parent item; "
                "global_variant_id must be blank"
            )
        if str(payload.get("rule_scope") or "group").strip() != "group":
            raise GlobalMenuMutationError(
                "Global itemcode locators must use group scope"
            )
        if not bool(payload.get("confirm_group_wide")):
            raise GlobalMenuMutationError(
                "A group-wide itemcode mapping requires explicit confirmation"
            )
        payload["rule_scope"] = "group"
        payload["restaurant_id"] = ""
        payload["global_variant_id"] = ""
    return payload


def _validate_stable_action(value: Any, path: str = "action") -> None:
    """Reject local/transient identity keys from provisional global wire data."""
    if isinstance(value, dict):
        for key, child in value.items():
            text = str(key)
            if text in LOCAL_IDENTITY_KEYS:
                error = GlobalMenuMutationError(
                    f"{path}.{text} is a local identity and cannot be sent as global authority"
                )
                error.code = "global_menu_local_identity_rejected"
                raise error
            _validate_stable_action(child, f"{path}.{text}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_stable_action(child, f"{path}[{index}]")


def _urls(conn, mutation_id: Optional[str] = None) -> Tuple[str, str]:
    base_url, api_key = get_cloud_sync_config(conn)
    if not base_url or not api_key:
        raise GlobalMenuMutationError("Global menu changes require a cloud connection")
    route = "/desktop-analytics-sync/global-menu/mutations"
    if mutation_id:
        return f"{base_url}{route}/{mutation_id}", api_key
    return f"{base_url}{route}", api_key


def _headers(conn, api_key: str, *, for_write: bool = False) -> Dict[str, str]:
    from src.core.central_api import scoped_headers

    headers = scoped_headers(
        conn, auth_kind="sync", credential=api_key, content_type="application/json"
    )
    if for_write:
        editor_key = get_global_menu_editor_key(conn)
        if not editor_key:
            error = GlobalMenuMutationError(
                "Global menu editing requires a configured editor credential"
            )
            error.code = "global_menu_editor_required"
            raise error
        headers["X-Global-Menu-Key"] = editor_key
    return headers


def _request_json(method: str, url: str, *, headers: Dict[str, str], payload=None):
    import requests

    if method == "GET":
        response = requests.get(url, headers=headers, timeout=60)
    else:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
    try:
        body = response.json() if response.content else {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return response.status_code, body


def _stable_identity(
    conn,
    *,
    local_menu_item_id: Optional[str],
    local_variant_id: Optional[str],
    global_menu_item_id: Optional[str],
    global_variant_id: Optional[str],
) -> Dict[str, Optional[str]]:
    item_id = str(global_menu_item_id or "").strip() or None
    variant_id = str(global_variant_id or "").strip() or None
    if local_menu_item_id:
        linked_item, linked_variant = global_ids_for_local(
            conn, local_menu_item_id, local_variant_id
        )
        item_id = item_id or linked_item
        variant_id = variant_id or linked_variant
    if not item_id:
        error = GlobalMenuMutationError(
            "Global mutation source/target is unresolved; sync global menu state first"
        )
        error.code = "global_menu_identity_unresolved"
        raise error
    return {
        "global_item_id": item_id,
        "global_variant_id": variant_id,
    }


def _canonical_item_metadata(conn, global_item_id: str) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT canonical_name, canonical_type, is_verified
        FROM global_menu_items WHERE global_menu_item_id=?
        """,
        (global_item_id,),
    ).fetchone()
    if row is None:
        error = GlobalMenuMutationError(
            f"Global item {global_item_id} is missing from the local projection"
        )
        error.code = "global_menu_identity_unresolved"
        raise error
    return {
        "canonical_name": str(row[0]),
        "canonical_type": str(row[1]),
        "is_verified": bool(row[2]),
    }


def _unsupported_local_mutation(mutation_type: str) -> None:
    error = GlobalMenuMutationError(
        f"The frozen global-menu contract does not support local operation '{mutation_type}'"
    )
    error.code = "global_menu_operation_unsupported"
    raise error


def build_global_action_from_local(
    conn,
    *,
    mutation_type: str,
    source_local_menu_item_id: Optional[str] = None,
    source_local_variant_id: Optional[str] = None,
    target_local_menu_item_id: Optional[str] = None,
    target_local_variant_id: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    alias = str(mutation_type or "").strip()
    source = None
    target = None
    if source_local_menu_item_id:
        source = _stable_identity(
            conn,
            local_menu_item_id=source_local_menu_item_id,
            local_variant_id=source_local_variant_id,
            global_menu_item_id=None,
            global_variant_id=None,
        )
    if target_local_menu_item_id:
        target = _stable_identity(
            conn,
            local_menu_item_id=target_local_menu_item_id,
            local_variant_id=target_local_variant_id,
            global_menu_item_id=None,
            global_variant_id=None,
        )
    detail = dict(details or {})

    if alias in {"item_create", "global_item.create"}:
        canonical_name = str(detail.get("canonical_name") or "").strip()
        canonical_type = str(detail.get("canonical_type") or "").strip()
        if not canonical_name or not canonical_type:
            raise GlobalMenuMutationError(
                "Global item creation requires canonical name and type"
            )
        return {
            "mutation_type": "global_item.create",
            "payload": {
                "canonical_name": canonical_name,
                "canonical_type": canonical_type,
                "is_verified": bool(detail.get("is_verified", False)),
            },
        }

    if alias in {"variant_create", "global_variant.create"}:
        local_variant_id = source_local_variant_id or target_local_variant_id
        row = None
        if local_variant_id:
            row = conn.execute(
                "SELECT variant_name, unit, value FROM variants WHERE variant_id=?",
                (str(local_variant_id),),
            ).fetchone()
        canonical_name = str(
            (row[0] if row is not None else detail.get("canonical_name")) or ""
        ).strip()
        if not canonical_name:
            raise GlobalMenuMutationError(
                "Global variant creation requires a canonical name"
            )
        unit = row[1] if row is not None else detail.get("unit")
        value = row[2] if row is not None else detail.get("value")
        return {
            "mutation_type": "global_variant.create",
            "payload": {
                "canonical_name": canonical_name,
                "dimension": {"unit": str(unit or "").strip(), "value": value},
            },
        }

    if alias in {"merge", "menu_merge", "global_item.merge"}:
        if not source or not target:
            raise GlobalMenuMutationError("Global item merge requires source and target")
        reconciliation = detail.get("variant_reconciliation") or []
        if source.get("global_variant_id") and target.get("global_variant_id"):
            reconciliation = [
                {
                    "source_global_variant_id": source["global_variant_id"],
                    "target_global_variant_id": target["global_variant_id"],
                }
            ]
        return {
            "mutation_type": "global_item.merge",
            "payload": {
                "source_global_item_id": source["global_item_id"],
                "target_global_item_id": target["global_item_id"],
                "variant_reconciliation": reconciliation,
            },
        }

    if alias in {"variant_merge", "global_variant.merge"}:
        if not source or not target or not source.get("global_variant_id") or not target.get("global_variant_id"):
            raise GlobalMenuMutationError("Global variant merge requires linked source and target variants")
        if source["global_item_id"] != target["global_item_id"]:
            return build_global_action_from_local(
                conn,
                mutation_type="menu_merge",
                source_local_menu_item_id=source_local_menu_item_id,
                source_local_variant_id=source_local_variant_id,
                target_local_menu_item_id=target_local_menu_item_id,
                target_local_variant_id=target_local_variant_id,
            )
        return {
            "mutation_type": "global_variant.merge",
            "payload": {
                "source_global_variant_id": source["global_variant_id"],
                "target_global_variant_id": target["global_variant_id"],
                "reconciled_dimension": detail.get("reconciled_dimension"),
            },
        }

    if alias in {"rename", "retype", "global_item.rename"}:
        if not source:
            raise GlobalMenuMutationError("Global rename requires a linked item")
        if (
            target
            and source.get("global_variant_id")
            and target.get("global_variant_id")
            and source["global_variant_id"] != target["global_variant_id"]
        ):
            _unsupported_local_mutation("rename_with_variant_remap")
        current = _canonical_item_metadata(conn, source["global_item_id"])
        return {
            "mutation_type": "global_item.rename",
            "payload": {
                "global_item_id": source["global_item_id"],
                "canonical_name": str(detail.get("canonical_name") or current["canonical_name"]).strip(),
                "canonical_type": str(detail.get("canonical_type") or current["canonical_type"]).strip(),
            },
        }

    if alias in {"verify", "verify_or_rename", "global_item.verify"}:
        if not source:
            raise GlobalMenuMutationError("Global verification requires a linked item")
        current = _canonical_item_metadata(conn, source["global_item_id"])
        requested_name = str(detail.get("canonical_name") or current["canonical_name"]).strip()
        requested_type = str(detail.get("canonical_type") or current["canonical_type"]).strip()
        if requested_name != current["canonical_name"] or requested_type != current["canonical_type"]:
            _unsupported_local_mutation("combined_verify_and_rename")
        return {
            "mutation_type": "global_item.verify",
            "payload": {
                "global_item_id": source["global_item_id"],
                "is_verified": bool(detail.get("is_verified", True)),
            },
        }

    if alias in {"remap", "global_locator.map"}:
        if not target:
            raise GlobalMenuMutationError("Global locator mapping requires a linked target")
        locator_value = str(
            detail.get("restaurant_pos_assignment_key")
            or detail.get("locator_value")
            or ""
        ).strip()
        if not locator_value:
            raise GlobalMenuMutationError("Global locator mapping requires a POS assignment key")
        locator_type = str(detail.get("locator_type") or "pos_item").strip()
        if locator_type not in AUTHORITATIVE_LOCATOR_TYPES:
            raise GlobalMenuMutationError("Global locator mapping has an invalid locator type")
        capability = require_global_menu_capability(conn)
        is_pos = locator_type in {"pos_item", "pos_addon"}
        expected_scope = "restaurant" if is_pos else "group"
        rule_scope = str(detail.get("rule_scope") or expected_scope).strip()
        if rule_scope != expected_scope:
            raise GlobalMenuMutationError(
                f"Global {locator_type} locators must use {expected_scope} scope"
            )
        confirm_group_wide = bool(detail.get("confirm_group_wide"))
        if rule_scope == "group" and not confirm_group_wide:
            raise GlobalMenuMutationError(
                "A group-wide itemcode mapping requires explicit confirmation"
            )
        restaurant_id = str(capability.restaurant_id or "").strip() if is_pos else ""
        if is_pos and not restaurant_id:
            raise GlobalMenuMutationError(
                "A POS locator mapping requires the selected restaurant id"
            )
        return {
            "mutation_type": "global_locator.map",
            "payload": {
                "rule_scope": rule_scope,
                "restaurant_id": restaurant_id,
                "locator_type": locator_type,
                "locator_value": locator_value,
                "global_item_id": target["global_item_id"],
                "global_variant_id": (
                    ""
                    if locator_type == "itemcode"
                    else target.get("global_variant_id") or ""
                ),
                "confirm_group_wide": confirm_group_wide,
            },
        }

    if alias in {"price_update", "global_locator.price_update"}:
        _unsupported_local_mutation("global_locator.price_update")

    if alias in {"undo", "global_menu.undo"}:
        mutation_id = str(
            detail.get("reverts_mutation_id") or detail.get("undo_mutation_id") or ""
        ).strip()
        if not mutation_id:
            raise GlobalMenuMutationError("Global undo requires the accepted mutation ID")
        return {
            "mutation_type": "global_menu.undo",
            "payload": {"undo_mutation_id": mutation_id},
        }

    _unsupported_local_mutation(alias)
    raise AssertionError("unreachable")


def preview_global_mutation(
    conn,
    *,
    action: Mapping[str, Any],
    mutation_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_action = dict(action)
    mutation_type = str(normalized_action.get("mutation_type") or "").strip()
    mutation_payload = normalized_action.get("payload")
    if not mutation_type or not isinstance(mutation_payload, dict):
        raise GlobalMenuMutationError(
            "Global mutation preview requires mutation_type and payload"
        )
    capability = _require_write_capability(conn, mutation_type)
    mutation_payload = _normalize_mutation_payload(
        mutation_type,
        mutation_payload,
        capability=capability,
    )
    _validate_stable_action(mutation_payload, "payload")
    mutation_id = str(mutation_id or uuid.uuid4())
    root_url, api_key = _urls(conn)
    payload = {
        "schema_version": GLOBAL_MUTATION_SCHEMA_VERSION,
        "mutation_type": mutation_type,
        "expected_menu_group_revision": capability.mutation_revision,
        "payload": mutation_payload,
    }
    # Resolve the editor credential before the transport boundary. A missing or
    # forbidden write grant is an authorization result, not a network failure.
    headers = _headers(conn, api_key, for_write=True)
    try:
        status, body = _request_json(
            "POST",
            f"{root_url}/preview",
            headers=headers,
            payload=payload,
        )
    except Exception as exc:
        raise GlobalMenuMutationError("Global menu preview could not reach the server") from exc
    if status != 200:
        error = GlobalMenuMutationError(str(body.get("error") or f"Preview failed (HTTP {status})"))
        error.code = str(body.get("code") or error.code)
        raise error
    if (
        str(body.get("menu_group_id") or "") != capability.menu_group_id
        or not body.get("preview_digest")
        or body.get("menu_group_revision") is None
        or str(body.get("mutation_type") or "") != mutation_type
        or not isinstance(body.get("payload"), dict)
    ):
        raise GlobalMenuMutationError("Global menu preview response is incomplete or mismatched")
    write_ready = (
        capability.resolution_ready
        if is_global_menu_resolution_mutation(mutation_type)
        else capability.mutation_ready
    )
    return {
        **body,
        "status": "preview",
        "mutation_id": mutation_id,
        "coverage_complete": capability.coverage_complete,
        "affects_entire_menu_group": True,
        "commit_allowed": bool(
            write_ready
            and body.get("commit_allowed")
            and body.get("revision_current", True)
            and not (body.get("conflicts") or [])
        ),
    }


def _validate_preview_for_commit(conn, preview: Mapping[str, Any]) -> Any:
    mutation_type = str(preview.get("mutation_type") or "").strip()
    capability = _require_write_capability(conn, mutation_type)
    if str(preview.get("menu_group_id") or "") != capability.menu_group_id:
        raise GlobalMenuMutationError("Preview belongs to a different menu group")
    try:
        preview_revision = int(preview.get("menu_group_revision"))
    except (TypeError, ValueError) as exc:
        raise GlobalMenuMutationError("Preview has no valid menu-group revision") from exc
    if preview_revision != capability.mutation_revision:
        error = GlobalMenuMutationError("Global menu preview is stale; preview again")
        error.code = "global_menu_preview_stale"
        raise error
    if preview.get("conflicts"):
        error = GlobalMenuMutationError(
            "Global menu commit is blocked by unresolved preview conflicts"
        )
        error.code = "global_menu_preview_blocked"
        raise error
    if not preview.get("preview_digest") or not preview.get("mutation_id"):
        raise GlobalMenuMutationError("Global menu commit requires a server preview digest")
    if not preview.get("mutation_type") or not isinstance(preview.get("payload"), Mapping):
        raise GlobalMenuMutationError("Global menu preview omitted its mutation payload")
    _validate_stable_action(dict(preview["payload"]), "payload")
    return capability


def global_mutation_status(conn, mutation_id: str) -> Dict[str, Any]:
    capability = require_global_menu_capability(conn)
    url, api_key = _urls(conn, mutation_id)
    try:
        status, body = _request_json("GET", url, headers=_headers(conn, api_key))
    except Exception as exc:
        raise GlobalMenuMutationError("Global mutation status is unavailable") from exc
    if status == 404:
        return {"status": "not_found", "mutation_id": mutation_id}
    if status != 200:
        error = GlobalMenuMutationError(
            str(body.get("error") or f"Status failed (HTTP {status})")
        )
        error.code = str(body.get("code") or error.code)
        raise error
    if str(body.get("menu_group_id") or "") != capability.menu_group_id:
        raise GlobalMenuMutationError("Mutation status belongs to another menu group")
    return body


def _apply_accepted_projection(conn, body: Mapping[str, Any]) -> None:
    from src.core.global_menu_history import pull_global_menu_history
    from src.core.global_menu_sync import (
        pull_global_assignment_snapshot,
        pull_global_menu_state,
    )

    result = pull_global_menu_state(conn)
    if result.get("error") or result.get("status") == "error":
        error = GlobalMenuMutationError(
            "Server accepted the global menu change, but local projection refresh failed"
        )
        error.code = "global_menu_local_refresh_required"
        raise error
    assignment_result = pull_global_assignment_snapshot(conn)
    if assignment_result.get("error") or assignment_result.get("status") == "error":
        error = GlobalMenuMutationError(
            "Server accepted the global menu change, but local assignments could not refresh"
        )
        error.code = "global_menu_local_refresh_required"
        raise error
    history_result = pull_global_menu_history(conn)
    if history_result.get("error") or history_result.get("status") == "error":
        # History is a separate audit read model. A failed refresh must not
        # reinterpret an already accepted catalog mutation as uncommitted.
        logger.warning(
            "Global menu mutation accepted but unified history refresh failed: %s",
            history_result.get("error") or history_result,
        )


def global_resolution_context(
    conn,
    *,
    local_menu_item_id: str,
    local_variant_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return trusted locator evidence for one local menu/variant pair."""
    capability = require_global_menu_capability(conn)
    item = conn.execute(
        "SELECT name, type, is_verified FROM menu_items WHERE menu_item_id=?",
        (str(local_menu_item_id),),
    ).fetchone()
    if item is None:
        raise GlobalMenuMutationError("The local menu item no longer exists")
    variant = None
    if local_variant_id:
        variant = conn.execute(
            "SELECT variant_name, unit, value FROM variants WHERE variant_id=?",
            (str(local_variant_id),),
        ).fetchone()
        if variant is None:
            raise GlobalMenuMutationError("The local variant no longer exists")
    global_item_id, global_variant_id = global_ids_for_local(
        conn, local_menu_item_id, local_variant_id
    )
    params: list[Any] = [str(local_menu_item_id)]
    variant_filter = ""
    if local_variant_id:
        variant_filter = " AND variant_id=?"
        params.append(str(local_variant_id))
    assignment_rows = conn.execute(
        f"""
        SELECT order_item_id, price FROM menu_item_variants
        WHERE menu_item_id=?{variant_filter}
        ORDER BY order_item_id
        """,
        params,
    ).fetchall()
    locators: list[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def note(locator_type: str, locator_value: Any, price: Any = None) -> None:
        value = str(locator_value or "").strip()
        key = (locator_type, value)
        if not value or key in seen:
            return
        seen.add(key)
        group_wide = locator_type == "itemcode"
        locators.append(
            {
                "locator_type": locator_type,
                "locator_value": value,
                "rule_scope": "group" if group_wide else "restaurant",
                "restaurant_id": "" if group_wide else capability.restaurant_id,
                "confirm_group_wide": group_wide,
            }
        )

    for assignment_row in assignment_rows:
        assignment_key = str(assignment_row[0])
        assignment_price = assignment_row[1]
        order_rows = conn.execute(
            """
            SELECT itemcode, name_raw FROM order_items
            WHERE petpooja_itemid IS NOT NULL
              AND CAST(petpooja_itemid AS TEXT)=?
              AND menu_item_id=?
            """,
            (assignment_key, str(local_menu_item_id)),
        ).fetchall()
        addon_rows = conn.execute(
            """
            SELECT name_raw FROM order_item_addons
            WHERE petpooja_addonid IS NOT NULL
              AND TRIM(CAST(petpooja_addonid AS TEXT))=?
              AND menu_item_id=?
            """,
            (assignment_key, str(local_menu_item_id)),
        ).fetchall()
        if order_rows:
            note("pos_item", assignment_key, assignment_price)
            for order_row in order_rows:
                note("itemcode", order_row[0])
        if addon_rows:
            note("pos_addon", assignment_key, assignment_price)

    return {
        "local_menu_item_id": str(local_menu_item_id),
        "local_variant_id": str(local_variant_id) if local_variant_id else None,
        "global_item_id": global_item_id,
        "global_variant_id": global_variant_id,
        "canonical_name": str(item[0]),
        "canonical_type": str(item[1]),
        "is_verified": bool(item[2]),
        "variant": (
            {
                "canonical_name": str(variant[0]),
                "dimension": {
                    "unit": str(variant[1] or ""),
                    "value": variant[2],
                },
            }
            if variant is not None
            else None
        ),
        "locators": locators,
    }


def commit_global_mutation(
    conn,
    *,
    preview: Mapping[str, Any],
    already_locked: bool = False,
) -> Dict[str, Any]:
    """Commit and reconcile while serialized with every background cloud pull."""
    from src.core.services.cloud_pull_orchestrator import CLOUD_PULL_LOCK

    acquired = False
    if not already_locked:
        CLOUD_PULL_LOCK.acquire(blocking=True)
        acquired = True
    try:
        return _commit_global_mutation_locked(conn, preview=preview)
    finally:
        if acquired:
            CLOUD_PULL_LOCK.release()


def _commit_global_mutation_locked(
    conn, *, preview: Mapping[str, Any]
) -> Dict[str, Any]:
    _validate_preview_for_commit(conn, preview)
    mutation_id = str(preview["mutation_id"])
    root_url, api_key = _urls(conn)
    payload = {
        "schema_version": GLOBAL_MUTATION_SCHEMA_VERSION,
        "mutation_id": mutation_id,
        "mutation_type": str(preview["mutation_type"]),
        "expected_menu_group_revision": int(preview["menu_group_revision"]),
        "preview_digest": str(preview["preview_digest"]),
        "payload": _payload_for_central_commit(preview),
    }
    attribution = get_sync_attribution(conn)
    payload["uploaded_by"] = attribution.get("employee") or None
    payload["uploaded_from"] = attribution.get("device") or None
    # Do not reconcile a POST that was never attempted because this client has
    # no editor credential.
    headers = _headers(conn, api_key, for_write=True)
    try:
        status, body = _request_json(
            "POST",
            f"{root_url}/commit",
            headers=headers,
            payload=payload,
        )
    except Exception:
        # The POST outcome is uncertain. Reconcile only; never repeat blindly.
        reconciled = global_mutation_status(conn, mutation_id)
        if reconciled.get("status") not in {"accepted", "committed"}:
            error = GlobalMenuMutationError(
                "Global menu commit outcome is uncertain; retry status reconciliation"
            )
            error.code = "global_menu_commit_uncertain"
            raise error
        body = reconciled
        status = 200
    if status != 200:
        error = GlobalMenuMutationError(
            str(body.get("error") or f"Commit failed (HTTP {status})")
        )
        error.code = str(
            body.get("code")
            or ("global_menu_preview_stale" if status == 409 else error.code)
        )
        raise error
    if body.get("status") not in {"accepted", "committed"}:
        raise GlobalMenuMutationError(
            str(body.get("error") or "Global menu commit response was not accepted")
        )
    if str(body.get("mutation_id") or "") != mutation_id:
        raise GlobalMenuMutationError("Global mutation response ID does not match the request")
    _apply_accepted_projection(conn, body)
    return {**body, "status": "success", "affects_entire_menu_group": True}


def _payload_for_central_commit(preview: Mapping[str, Any]) -> Dict[str, Any]:
    """Restore the accepted request shape when a preview flattens a variant dimension."""
    payload = dict(preview["payload"])
    if (
        str(preview.get("mutation_type") or "").strip()
        != MUTATION_GLOBAL_VARIANT_CREATE
        or "dimension" in payload
        or not ({"unit", "value"} & payload.keys())
    ):
        return payload

    unit = payload.pop("unit", "")
    value = payload.pop("value", None)
    payload["dimension"] = {"unit": unit, "value": value}
    return payload


def preview_reference_from_request(request: Any) -> Dict[str, Any]:
    values = {
        "mutation_id": getattr(request, "global_mutation_id", None),
        "preview_digest": getattr(request, "global_preview_digest", None),
        "menu_group_id": getattr(request, "global_menu_group_id", None),
        "menu_group_revision": getattr(request, "global_preview_revision", None),
        "coverage_complete": getattr(request, "global_coverage_complete", None),
        "conflicts": getattr(request, "global_conflicts", None) or [],
        "mutation_type": getattr(request, "global_mutation_type", None),
        "payload": getattr(request, "global_mutation_payload", None),
    }
    if not all(values.get(key) is not None for key in (
        "mutation_id",
        "preview_digest",
        "menu_group_id",
        "menu_group_revision",
        "coverage_complete",
        "mutation_type",
        "payload",
    )):
        error = GlobalMenuMutationError(
            "This global menu change requires a fresh server impact preview"
        )
        error.code = "global_menu_preview_required"
        raise error
    return values
