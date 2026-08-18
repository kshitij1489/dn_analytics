"""Fetch, validate, and transactionally apply dormant global-menu projections."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.global_menu_identity import (
    GlobalMenuIdentityError,
    apply_local_projection_plan,
    ensure_item_projection_owner,
    ensure_no_variant_sentinel,
    ensure_variant_projection_owner,
    normalize_locator,
    plan_local_projection,
    resolve_redirect_chain,
    unique_local_variant_name,
    validate_redirect_graph,
)
from src.core.global_menu_schema import (
    GLOBAL_MENU_SCHEMA_VERSION,
    GLOBAL_MENU_MODE,
    GlobalMenuCapabilityStatus,
    global_menu_cache_needs_rebuild,
    is_catalog_hex_id,
    quarantine_global_menu_payload,
    require_global_menu_capability,
    resolve_global_menu_quarantine_page,
    resolve_global_menu_capability,
    update_global_menu_state,
    wipe_global_menu_projection,
)
from utils.id_generator import generate_deterministic_id


GLOBAL_MENU_PAGE_LIMIT = 500
GLOBAL_MENU_MAX_PAGES = 1000
GLOBAL_MENU_FETCH_ATTEMPTS = 2
GLOBAL_MENU_SNAPSHOT_SECTIONS = ("items", "variants", "redirects", "rules")
_LOCATOR_TYPE_TO_LOCAL = {
    "pos_item": "pos-item",
    "pos_addon": "pos-addon",
    "itemcode": "itemcode",
}
_POS_LOCATOR_KINDS = frozenset({"pos-item", "pos-addon"})
_GROUP_LOCATOR_KINDS = frozenset({"itemcode"})


class GlobalMenuSyncError(RuntimeError):
    code = "global_menu_sync_failed"


def _sync_error(message: str, code: str) -> GlobalMenuSyncError:
    error = GlobalMenuSyncError(message)
    error.code = code
    return error


def get_global_menu_snapshot_endpoint(conn) -> Optional[str]:
    base_url, _ = get_cloud_sync_config(conn)
    return f"{base_url}/desktop-analytics-sync/global-menu/snapshot" if base_url else None


def get_global_menu_events_endpoint(conn) -> Optional[str]:
    base_url, _ = get_cloud_sync_config(conn)
    return f"{base_url}/desktop-analytics-sync/global-menu/events" if base_url else None


def get_global_menu_status_endpoint(conn) -> Optional[str]:
    base_url, _ = get_cloud_sync_config(conn)
    return f"{base_url}/desktop-analytics-sync/global-menu/status" if base_url else None


def get_global_menu_assignment_endpoint(conn) -> Optional[str]:
    # Phase 2 extends the existing restaurant-filtered assignment snapshot with
    # global IDs. No unadvertised endpoint is called during Phase 1.
    base_url, _ = get_cloud_sync_config(conn)
    return f"{base_url}/desktop-analytics-sync/menu-assignments/snapshot" if base_url else None


def _payload_key(stream: str, payload: Any, cursor: Optional[str]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{stream}:{cursor or 'root'}:{digest}"


def _nonblank(row: Dict[str, Any], key: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise GlobalMenuSyncError(f"Global menu row is missing {key}")
    return value


def _revision(value: Any, field: str = "server_revision") -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GlobalMenuSyncError(f"{field} must be a non-negative integer") from exc
    if result < 0:
        raise GlobalMenuSyncError(f"{field} must be a non-negative integer")
    return result


def _validate_group(payload: Dict[str, Any], capability: GlobalMenuCapabilityStatus) -> str:
    group_id = _nonblank(payload, "menu_group_id")
    if group_id != capability.menu_group_id:
        error = GlobalMenuSyncError(
            f"Cross-group page rejected: expected {capability.menu_group_id}, received {group_id}"
        )
        error.code = "global_menu_group_mismatch"
        raise error
    return group_id


def _validate_schema_version(payload: Dict[str, Any]) -> None:
    version = _revision(payload.get("schema_version"), "schema_version")
    if version != GLOBAL_MENU_SCHEMA_VERSION:
        error = GlobalMenuSyncError(
            f"Unsupported global menu schema version: {version}"
        )
        error.code = "global_menu_schema_unsupported"
        raise error


def _assert_existing_group(
    conn, table: str, id_column: str, entity_id: str, group_id: str
) -> None:
    row = conn.execute(
        f"SELECT menu_group_id FROM {table} WHERE {id_column}=?", (entity_id,)
    ).fetchone()
    if row and str(row[0]) != group_id:
        raise GlobalMenuSyncError(
            f"{table} row {entity_id} is already owned by another menu group"
        )


def _iter_dicts(value: Any, field: str) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise GlobalMenuSyncError(f"{field} must be an array of objects")
    return [dict(row) for row in value]


def _contract_snapshot_watermark(payload: Dict[str, Any]) -> tuple[int, int]:
    watermark = payload.get("snapshot_watermark")
    if not isinstance(watermark, dict):
        raise GlobalMenuSyncError("Global menu snapshot omitted its watermark")
    revision = _revision(
        watermark.get("menu_group_revision"),
        "snapshot_watermark.menu_group_revision",
    )
    page_revision = _revision(
        payload.get("menu_group_revision"), "menu_group_revision"
    )
    if revision != page_revision:
        raise GlobalMenuSyncError(
            "Global menu snapshot watermark revision does not match the page"
        )
    if revision < 1:
        raise GlobalMenuSyncError(
            "Global menu snapshot watermark revision must be at least genesis (1)"
        )
    event_seq = _revision(
        watermark.get("event_seq"), "snapshot_watermark.event_seq"
    )
    if event_seq < 1:
        raise GlobalMenuSyncError(
            "Global menu snapshot watermark event_seq must be at least genesis (1)"
        )
    return event_seq, revision


def _contract_snapshot_page_cursor(payload: Dict[str, Any]) -> tuple[Optional[str], bool]:
    has_more = payload.get("has_more")
    if not isinstance(has_more, bool):
        raise GlobalMenuSyncError("has_more must be a boolean")
    next_cursor = payload.get("next_cursor")
    if next_cursor is None:
        if has_more:
            raise GlobalMenuSyncError("next_cursor and has_more are inconsistent")
        return None, False
    if isinstance(next_cursor, bool) or not isinstance(next_cursor, str):
        raise GlobalMenuSyncError(
            "Snapshot next_cursor must be a 32-character lowercase hex id"
        )
    text = next_cursor.strip()
    if not is_catalog_hex_id(text):
        raise GlobalMenuSyncError(
            "Snapshot next_cursor must be a 32-character lowercase hex id"
        )
    if not has_more:
        raise GlobalMenuSyncError("next_cursor and has_more are inconsistent")
    return text, True


def _contract_snapshot_row_id(section: str, row: Dict[str, Any]) -> str:
    key = {
        "items": "global_item_id",
        "variants": "global_variant_id",
        "redirects": "source_global_id",
        "rules": "rule_id",
    }[section]
    row_id = _nonblank(row, key)
    if not is_catalog_hex_id(row_id):
        raise GlobalMenuSyncError(
            f"Snapshot {key} must be a 32-character lowercase hex id"
        )
    return row_id


def _validate_contract_snapshot_order(
    *,
    section: str,
    rows: Sequence[Dict[str, Any]],
    next_cursor: Optional[str],
    has_more: bool,
) -> None:
    row_ids = [_contract_snapshot_row_id(section, row) for row in rows]
    if any(left >= right for left, right in zip(row_ids, row_ids[1:])):
        raise GlobalMenuSyncError(
            f"Snapshot {section} rows must be strictly ordered by id"
        )
    if has_more and (not row_ids or next_cursor != row_ids[-1]):
        raise GlobalMenuSyncError(
            "Snapshot next_cursor must match the final row id on a non-final page"
        )


def _normalize_contract_snapshot_page(
    payload: Dict[str, Any], *, validate_paging: bool = True
) -> Dict[str, Any]:
    """Translate the frozen §25.5 sectioned payload into the local projection shape."""
    _validate_schema_version(payload)
    section = str(payload.get("section") or "").strip()
    if section not in GLOBAL_MENU_SNAPSHOT_SECTIONS:
        raise GlobalMenuSyncError(f"Invalid global menu snapshot section: {section or '<blank>'}")
    group_id = _nonblank(payload, "menu_group_id")
    revision = _revision(payload.get("menu_group_revision"), "menu_group_revision")
    watermark_event_seq, watermark_revision = _contract_snapshot_watermark(payload)
    next_cursor, has_more = _contract_snapshot_page_cursor(payload)
    rows = _iter_dicts(payload.get("rows"), "rows")
    if validate_paging:
        _validate_contract_snapshot_order(
            section=section,
            rows=rows,
            next_cursor=next_cursor,
            has_more=has_more,
        )
    else:
        for row in rows:
            _contract_snapshot_row_id(section, row)
    normalized: Dict[str, Any] = {
        "schema_version": GLOBAL_MENU_SCHEMA_VERSION,
        "menu_group_id": group_id,
        "catalog_revision": revision,
        "mutation_revision": revision,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "_snapshot_section": section,
        "_snapshot_watermark_event_seq": watermark_event_seq,
        "_snapshot_watermark_revision": watermark_revision,
    }
    if section == "items":
        normalized["items"] = [
            {
                **row,
                "global_menu_item_id": row.get("global_item_id"),
                "server_revision": revision,
            }
            for row in rows
        ]
    elif section == "variants":
        normalized["variants"] = [
            {**row, "server_revision": revision} for row in rows
        ]
    elif section == "redirects":
        redirects = []
        for row in rows:
            entity_type = str(row.get("entity_type") or "").strip()
            source = str(row.get("source_global_id") or "").strip()
            target = str(row.get("target_global_id") or "").strip()
            redirect = {
                "redirect_id": f"{entity_type}:{source}",
                "entity_type": entity_type,
                "server_revision": _revision(
                    row.get("menu_group_revision", revision), "menu_group_revision"
                ),
            }
            if entity_type == "item":
                redirect.update(
                    {
                        "source_global_menu_item_id": source,
                        "target_global_menu_item_id": target,
                    }
                )
            elif entity_type == "variant":
                redirect.update(
                    {
                        "source_global_variant_id": source,
                        "target_global_variant_id": target,
                    }
                )
            else:
                raise GlobalMenuSyncError(f"Invalid redirect entity_type: {entity_type}")
            redirects.append(redirect)
        normalized["redirects"] = redirects
    else:
        rules = []
        for row in rows:
            locator_type = str(row.get("locator_type") or "").strip()
            local_kind = _LOCATOR_TYPE_TO_LOCAL.get(locator_type)
            if local_kind is None:
                raise GlobalMenuSyncError(f"Invalid global locator_type: {locator_type}")
            rule_id = _nonblank(row, "rule_id")
            rules.append(
                {
                    "rule_id": rule_id,
                    "locator_scope": row.get("rule_scope"),
                    "restaurant_id": row.get("restaurant_id") or None,
                    "locator_kind": local_kind,
                    "locator_value": row.get("locator_value"),
                    "target_global_menu_item_id": row.get("global_item_id"),
                    "target_global_variant_id": (
                        None
                        if locator_type == "itemcode"
                        else row.get("global_variant_id") or None
                    ),
                    "provenance": row.get("provenance") or "server-rule",
                    "is_verified": True,
                    "lifecycle_state": "active",
                    "server_revision": _revision(
                        row.get("menu_group_revision", revision), "menu_group_revision"
                    ),
                }
            )
        normalized["mapping_rules"] = rules
    return normalized


def _normalize_contract_event_page(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate the frozen §25.6 semantic event envelope."""
    _validate_schema_version(payload)
    group_id = _nonblank(payload, "menu_group_id")
    page_revision = _revision(
        payload.get("menu_group_revision"), "menu_group_revision"
    )
    next_cursor = _revision(payload.get("next_cursor"), "next_cursor")
    has_more = payload.get("has_more")
    if not isinstance(has_more, bool):
        raise GlobalMenuSyncError("has_more must be a boolean")
    raw_event_head = payload.get("event_head_seq")
    event_head = (
        None
        if raw_event_head is None
        else _revision(raw_event_head, "event_head_seq")
    )
    if event_head is not None and next_cursor > event_head:
        raise GlobalMenuSyncError("Global menu event cursor exceeds the event head")
    rows = _iter_dicts(payload.get("events"), "events")
    events = []
    previous_event_seq: Optional[int] = None
    for row in rows:
        event_seq = _revision(row.get("event_seq"), "event_seq")
        if previous_event_seq is not None and event_seq <= previous_event_seq:
            raise GlobalMenuSyncError(
                "Global menu event sequences must be strictly increasing"
            )
        previous_event_seq = event_seq
        event_revision = _revision(
            row.get("menu_group_revision"), "menu_group_revision"
        )
        if event_revision > page_revision:
            raise GlobalMenuSyncError(
                "Global menu event revision exceeds the page revision"
            )
        body = row.get("payload")
        if not isinstance(body, dict):
            raise GlobalMenuSyncError("Global menu event payload must be an object")
        required_semantic_fields = {
            "action",
            "items",
            "variants",
            "redirects",
            "mapping_rules",
            "tombstones",
            "assignment_snapshot_required",
            "assignment_restaurants",
            "menu_group_revision",
        }
        missing_fields = sorted(required_semantic_fields - set(body))
        if missing_fields:
            raise GlobalMenuSyncError(
                "Global menu event omitted semantic field(s): "
                + ", ".join(missing_fields)
            )
        if not isinstance(body.get("action"), dict):
            raise GlobalMenuSyncError("Global menu event action must be an object")
        if not isinstance(body.get("assignment_snapshot_required"), bool):
            raise GlobalMenuSyncError(
                "assignment_snapshot_required must be a boolean"
            )
        assignment_restaurants = body.get("assignment_restaurants")
        if not isinstance(assignment_restaurants, list) or any(
            not str(value).strip() for value in assignment_restaurants
        ):
            raise GlobalMenuSyncError(
                "assignment_restaurants must contain non-blank restaurant IDs"
            )
        body_revision = _revision(
            body.get("menu_group_revision"), "payload.menu_group_revision"
        )
        if body_revision != event_revision:
            raise GlobalMenuSyncError(
                "Global menu event payload revision does not match the event"
            )
        normalized_body: Dict[str, Any] = {
            "action": body["action"],
            "tombstones": body.get("tombstones"),
            "assignment_snapshot_required": body["assignment_snapshot_required"],
            "assignment_restaurants": [
                str(value).strip() for value in assignment_restaurants
            ],
            "menu_group_revision": body_revision,
        }
        section_keys = {
            "items": "items",
            "variants": "variants",
            "redirects": "redirects",
            "rules": "mapping_rules",
        }
        for section, normalized_key in section_keys.items():
            section_page = _normalize_contract_snapshot_page(
                {
                    "schema_version": GLOBAL_MENU_SCHEMA_VERSION,
                    "menu_group_id": group_id,
                    "menu_group_revision": event_revision,
                    "snapshot_watermark": {
                        "event_seq": event_seq,
                        "menu_group_revision": event_revision,
                    },
                    "section": section,
                    "rows": body.get(normalized_key),
                    "next_cursor": None,
                    "has_more": False,
                },
                validate_paging=False,
            )
            normalized_body[normalized_key] = section_page.get(normalized_key, [])
        events.append(
            {
                "event_id": f"global-event:{event_seq}",
                "mutation_id": row.get("mutation_id"),
                "event_type": row.get("event_type"),
                "catalog_revision": event_revision,
                "menu_group_id": group_id,
                "origin_restaurant_id": row.get("origin_restaurant_id"),
                "occurred_at": row.get("occurred_at"),
                "payload": normalized_body,
            }
        )
    if rows and next_cursor != _revision(rows[-1].get("event_seq"), "event_seq"):
        raise GlobalMenuSyncError("Global menu event cursor does not match the last event")
    if rows and event_head is None:
        raise GlobalMenuSyncError("Global menu event page omitted its event head")
    if has_more and not rows:
        raise GlobalMenuSyncError("Global menu event page cannot be empty with has_more")
    final_revision = (
        int(events[-1]["catalog_revision"])
        if events
        else page_revision
    )
    return {
        "schema_version": GLOBAL_MENU_SCHEMA_VERSION,
        "menu_group_id": group_id,
        "catalog_revision": final_revision,
        "mutation_revision": final_revision,
        "events": events,
        "next_cursor": str(next_cursor),
        "has_more": has_more,
    }


def _upsert_items(conn, rows: Sequence[Dict[str, Any]], group_id: str, page_revision: int) -> None:
    for row in rows:
        item_id = _nonblank(row, "global_menu_item_id")
        _assert_existing_group(
            conn, "global_menu_items", "global_menu_item_id", item_id, group_id
        )
        row_group = str(row.get("menu_group_id") or group_id).strip()
        if row_group != group_id:
            raise GlobalMenuSyncError(f"Item {item_id} belongs to another menu group")
        lifecycle = str(row.get("lifecycle_state") or "active")
        if lifecycle not in {"active", "redirected", "tombstoned"}:
            raise GlobalMenuSyncError(f"Item {item_id} has invalid lifecycle_state")
        name = str(row.get("canonical_name") or "").strip()
        item_type = str(row.get("canonical_type") or row.get("type") or "").strip()
        if not name:
            raise GlobalMenuSyncError(f"Item {item_id} is missing canonical name")
        revision = _revision(row.get("server_revision", page_revision))
        conn.execute(
            """
            INSERT INTO global_menu_items (
                global_menu_item_id, menu_group_id, canonical_name, canonical_type,
                is_verified, lifecycle_state, server_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(global_menu_item_id) DO UPDATE SET
                canonical_name=excluded.canonical_name,
                canonical_type=excluded.canonical_type,
                is_verified=excluded.is_verified,
                lifecycle_state=excluded.lifecycle_state,
                server_revision=excluded.server_revision,
                updated_at=CURRENT_TIMESTAMP
            WHERE excluded.server_revision >= global_menu_items.server_revision
            """,
            (
                item_id,
                group_id,
                name,
                item_type,
                1 if row.get("is_verified") else 0,
                lifecycle,
                revision,
                row.get("created_at"),
            ),
        )


def _upsert_variants(conn, rows: Sequence[Dict[str, Any]], group_id: str, page_revision: int) -> None:
    for row in rows:
        variant_id = _nonblank(row, "global_variant_id")
        _assert_existing_group(
            conn, "global_variants", "global_variant_id", variant_id, group_id
        )
        row_group = str(row.get("menu_group_id") or group_id).strip()
        if row_group != group_id:
            raise GlobalMenuSyncError(f"Variant {variant_id} belongs to another menu group")
        lifecycle = str(row.get("lifecycle_state") or "active")
        if lifecycle not in {"active", "redirected", "tombstoned"}:
            raise GlobalMenuSyncError(f"Variant {variant_id} has invalid lifecycle_state")
        name = str(row.get("canonical_name") or row.get("variant_name") or "").strip()
        if not name:
            raise GlobalMenuSyncError(f"Variant {variant_id} is missing canonical_name")
        revision = _revision(row.get("server_revision", page_revision))
        conn.execute(
            """
            INSERT INTO global_variants (
                global_variant_id, menu_group_id, canonical_name, description,
                unit, value, is_verified, lifecycle_state, server_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(global_variant_id) DO UPDATE SET
                canonical_name=excluded.canonical_name,
                description=excluded.description,
                unit=excluded.unit,
                value=excluded.value,
                is_verified=excluded.is_verified,
                lifecycle_state=excluded.lifecycle_state,
                server_revision=excluded.server_revision,
                updated_at=CURRENT_TIMESTAMP
            WHERE excluded.server_revision >= global_variants.server_revision
            """,
            (
                variant_id,
                group_id,
                name,
                row.get("description"),
                row.get("unit"),
                row.get("value"),
                1 if row.get("is_verified") else 0,
                lifecycle,
                revision,
                row.get("created_at"),
            ),
        )


def _upsert_redirects(conn, rows: Sequence[Dict[str, Any]], group_id: str, page_revision: int) -> None:
    validate_redirect_graph(conn, rows)
    for row in rows:
        redirect_id = _nonblank(row, "redirect_id")
        _assert_existing_group(
            conn, "global_menu_redirects", "redirect_id", redirect_id, group_id
        )
        entity_type = str(row.get("entity_type") or "")
        revision = _revision(row.get("server_revision", page_revision))
        values = {
            "source_global_menu_item_id": row.get("source_global_menu_item_id"),
            "target_global_menu_item_id": row.get("target_global_menu_item_id"),
            "source_global_variant_id": row.get("source_global_variant_id"),
            "target_global_variant_id": row.get("target_global_variant_id"),
        }
        if entity_type == "item":
            for key in ("source_global_menu_item_id", "target_global_menu_item_id"):
                entity_id = str(values.get(key) or "").strip()
                if entity_id:
                    _assert_existing_group(
                        conn,
                        "global_menu_items",
                        "global_menu_item_id",
                        entity_id,
                        group_id,
                    )
        elif entity_type == "variant":
            for key in ("source_global_variant_id", "target_global_variant_id"):
                entity_id = str(values.get(key) or "").strip()
                if entity_id:
                    _assert_existing_group(
                        conn,
                        "global_variants",
                        "global_variant_id",
                        entity_id,
                        group_id,
                    )
        conn.execute(
            """
            INSERT INTO global_menu_redirects (
                redirect_id, menu_group_id, entity_type,
                source_global_menu_item_id, target_global_menu_item_id,
                source_global_variant_id, target_global_variant_id,
                server_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(redirect_id) DO UPDATE SET
                source_global_menu_item_id=excluded.source_global_menu_item_id,
                target_global_menu_item_id=excluded.target_global_menu_item_id,
                source_global_variant_id=excluded.source_global_variant_id,
                target_global_variant_id=excluded.target_global_variant_id,
                server_revision=excluded.server_revision,
                updated_at=CURRENT_TIMESTAMP
            WHERE excluded.server_revision >= global_menu_redirects.server_revision
            """,
            (
                redirect_id,
                group_id,
                entity_type,
                values["source_global_menu_item_id"],
                values["target_global_menu_item_id"],
                values["source_global_variant_id"],
                values["target_global_variant_id"],
                revision,
                row.get("created_at"),
            ),
        )


def _active_rule_target(
    conn,
    *,
    entity_type: str,
    entity_id: Optional[str],
    group_id: str,
) -> Optional[str]:
    if entity_id is None:
        return None
    canonical_id = resolve_redirect_chain(conn, entity_type, entity_id)
    if not canonical_id:
        raise _sync_error(
            f"Global {entity_type} target {entity_id} has no redirect survivor",
            "global_menu_target_unresolved",
        )
    if entity_type == "item":
        table, id_column = "global_menu_items", "global_menu_item_id"
    else:
        table, id_column = "global_variants", "global_variant_id"
    row = conn.execute(
        f"SELECT menu_group_id, lifecycle_state FROM {table} WHERE {id_column}=?",
        (canonical_id,),
    ).fetchone()
    if row is None:
        raise _sync_error(
            f"Global {entity_type} target {entity_id} resolves to missing {canonical_id}",
            "global_menu_target_missing",
        )
    if str(row[0]) != group_id:
        raise _sync_error(
            f"Global {entity_type} target {canonical_id} belongs to another menu group",
            "global_menu_group_mismatch",
        )
    if str(row[1]) != "active":
        raise _sync_error(
            f"Global {entity_type} target {entity_id} resolves to non-active {canonical_id}",
            "global_menu_target_tombstoned",
        )
    return canonical_id


def _rule_price_and_policy(
    row: Dict[str, Any],
    *,
    rule_id: str,
    scope: str,
    kind: str,
    capability: GlobalMenuCapabilityStatus,
) -> None:
    """POS locators are restaurant-scoped; mapping rules never carry a price."""
    del capability
    price = row.get("price")
    if price is not None:
        raise _sync_error(
            f"Rule {rule_id} must not carry a price; prices stay restaurant-owned",
            "global_menu_price_invalid",
        )
    if kind in _GROUP_LOCATOR_KINDS:
        if scope != "group":
            raise _sync_error(
                f"Rule {rule_id} has invalid restaurant scope for {kind}",
                "global_menu_rule_scope_invalid",
            )
        return None
    if kind in _POS_LOCATOR_KINDS and scope != "restaurant":
        raise _sync_error(
            f"POS rule {rule_id} must be restaurant-scoped",
            "global_menu_rule_scope_invalid",
        )
    return None



def _upsert_rules(
    conn,
    rows: Sequence[Dict[str, Any]],
    group_id: str,
    page_revision: int,
    capability: GlobalMenuCapabilityStatus,
) -> None:
    for row in rows:
        rule_id = _nonblank(row, "rule_id")
        _assert_existing_group(
            conn, "global_menu_mapping_rules", "rule_id", rule_id, group_id
        )
        scope = str(row.get("locator_scope") or "")
        kind = str(row.get("locator_kind") or "")
        locator_value = _nonblank(row, "locator_value")
        normalized = normalize_locator(kind, row.get("normalized_locator", locator_value))
        restaurant_id = str(row.get("restaurant_id") or "").strip() or None
        if scope == "restaurant" and not restaurant_id:
            raise GlobalMenuSyncError(f"Restaurant rule {rule_id} lacks restaurant_id")
        if scope == "group" and restaurant_id is not None:
            raise GlobalMenuSyncError(f"Group rule {rule_id} must not carry restaurant_id")
        if scope not in {"restaurant", "group"} or kind not in {
            "pos-item",
            "pos-addon",
            "itemcode",
        }:
            raise GlobalMenuSyncError(f"Rule {rule_id} has invalid scope/kind")
        lifecycle = str(row.get("lifecycle_state") or "active")
        if lifecycle not in {"active", "tombstoned"}:
            raise GlobalMenuSyncError(f"Rule {rule_id} has invalid lifecycle_state")
        _rule_price_and_policy(
            row,
            rule_id=rule_id,
            scope=scope,
            kind=kind,
            capability=capability,
        )
        revision = _revision(row.get("server_revision", page_revision))
        target_item_id = _active_rule_target(
            conn,
            entity_type="item",
            entity_id=_nonblank(row, "target_global_menu_item_id"),
            group_id=group_id,
        )
        target_variant_id = (
            str(row.get("target_global_variant_id") or "").strip() or None
        )
        if kind == "itemcode":
            target_variant_id = None
        if target_variant_id:
            target_variant_id = _active_rule_target(
                conn,
                entity_type="variant",
                entity_id=target_variant_id,
                group_id=group_id,
            )
        conn.execute(
            """
            INSERT INTO global_menu_mapping_rules (
                rule_id, menu_group_id, locator_scope, restaurant_id,
                locator_kind, locator_value, normalized_locator,
                target_global_menu_item_id, target_global_variant_id,
                provenance, is_verified, lifecycle_state, server_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id) DO UPDATE SET
                locator_scope=excluded.locator_scope,
                restaurant_id=excluded.restaurant_id,
                locator_kind=excluded.locator_kind,
                locator_value=excluded.locator_value,
                normalized_locator=excluded.normalized_locator,
                target_global_menu_item_id=excluded.target_global_menu_item_id,
                target_global_variant_id=excluded.target_global_variant_id,
                provenance=excluded.provenance,
                is_verified=excluded.is_verified,
                lifecycle_state=excluded.lifecycle_state,
                server_revision=excluded.server_revision,
                updated_at=CURRENT_TIMESTAMP
            WHERE excluded.server_revision >= global_menu_mapping_rules.server_revision
            """,
            (
                rule_id,
                group_id,
                scope,
                restaurant_id,
                kind,
                locator_value,
                normalized,
                target_item_id,
                target_variant_id,
                str(row.get("provenance") or "server-rule"),
                1 if row.get("is_verified", True) else 0,
                lifecycle,
                revision,
                row.get("created_at"),
            ),
        )


def _upsert_links(
    conn, payload: Dict[str, Any], page_revision: int, group_id: str
) -> None:
    links = payload.get("links") or {}
    if not isinstance(links, dict):
        raise GlobalMenuSyncError("links must be an object")
    for row in _iter_dicts(links.get("menu_items"), "links.menu_items"):
        local_id = _nonblank(row, "local_menu_item_id")
        global_id = _nonblank(row, "global_menu_item_id")
        _assert_existing_group(
            conn,
            "global_menu_items",
            "global_menu_item_id",
            global_id,
            group_id,
        )
        owner = ensure_item_projection_owner(conn, global_id)
        exists = conn.execute(
            "SELECT 1 FROM menu_items WHERE menu_item_id=?", (local_id,)
        ).fetchone()
        local_id = local_id if exists else owner
        conn.execute(
            """
            INSERT INTO menu_item_global_links (
                local_menu_item_id, global_menu_item_id, provenance,
                server_revision, is_projection_owner
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(local_menu_item_id) DO UPDATE SET
                global_menu_item_id=excluded.global_menu_item_id,
                provenance=excluded.provenance,
                server_revision=excluded.server_revision,
                is_projection_owner=excluded.is_projection_owner,
                linked_at=CURRENT_TIMESTAMP
            WHERE excluded.server_revision >= menu_item_global_links.server_revision
            """,
            (
                local_id,
                global_id,
                str(row.get("provenance") or "server-link"),
                _revision(row.get("server_revision", page_revision)),
                1 if local_id == owner else 0,
            ),
        )
    for row in _iter_dicts(links.get("variants"), "links.variants"):
        local_id = _nonblank(row, "local_variant_id")
        global_id = _nonblank(row, "global_variant_id")
        _assert_existing_group(
            conn,
            "global_variants",
            "global_variant_id",
            global_id,
            group_id,
        )
        owner = ensure_variant_projection_owner(conn, global_id)
        exists = conn.execute(
            "SELECT 1 FROM variants WHERE variant_id=?", (local_id,)
        ).fetchone()
        local_id = local_id if exists else owner
        conn.execute(
            """
            INSERT INTO variant_global_links (
                local_variant_id, global_variant_id, provenance,
                server_revision, is_projection_owner
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(local_variant_id) DO UPDATE SET
                global_variant_id=excluded.global_variant_id,
                provenance=excluded.provenance,
                server_revision=excluded.server_revision,
                is_projection_owner=excluded.is_projection_owner,
                linked_at=CURRENT_TIMESTAMP
            WHERE excluded.server_revision >= variant_global_links.server_revision
            """,
            (
                local_id,
                global_id,
                str(row.get("provenance") or "server-link"),
                _revision(row.get("server_revision", page_revision)),
                1 if local_id == owner else 0,
            ),
        )


def _apply_event_tombstones(
    conn, tombstones: Any, *, group_id: str
) -> List[Tuple[str, str]]:
    if not isinstance(tombstones, dict):
        raise GlobalMenuSyncError("Event tombstones must be an object")
    removed_pos_locators: List[Tuple[str, str]] = []
    for row in _iter_dicts(tombstones.get("redirects"), "tombstones.redirects"):
        entity_type = _nonblank(row, "entity_type")
        if entity_type not in {"item", "variant"}:
            raise GlobalMenuSyncError("Redirect tombstone has an invalid entity_type")
        redirect_id = f"{entity_type}:{_nonblank(row, 'source_global_id')}"
        conn.execute(
            "DELETE FROM global_menu_redirects WHERE menu_group_id=? AND redirect_id=?",
            (group_id, redirect_id),
        )
    for row in _iter_dicts(
        tombstones.get("mapping_rules"), "tombstones.mapping_rules"
    ):
        rule_id = _nonblank(row, "rule_id")
        existing = conn.execute(
            """
            SELECT locator_kind, locator_value
            FROM global_menu_mapping_rules
            WHERE menu_group_id=? AND rule_id=?
            """,
            (group_id, rule_id),
        ).fetchone()
        if existing is not None and str(existing[0]) in _POS_LOCATOR_KINDS:
            removed_pos_locators.append((str(existing[0]), str(existing[1])))
        elif existing is None:
            raw_kind = str(
                row.get("locator_kind")
                or _LOCATOR_TYPE_TO_LOCAL.get(str(row.get("locator_type") or ""))
                or ""
            )
            locator_value = str(row.get("locator_value") or "").strip()
            if raw_kind in _POS_LOCATOR_KINDS and locator_value:
                removed_pos_locators.append((raw_kind, locator_value))
        conn.execute(
            "DELETE FROM global_menu_mapping_rules WHERE menu_group_id=? AND rule_id=?",
            (group_id, rule_id),
        )
    return removed_pos_locators


def apply_global_menu_payload_page(
    conn,
    payload: Dict[str, Any],
    *,
    stream: str,
    capability: Optional[GlobalMenuCapabilityStatus] = None,
) -> Dict[str, Any]:
    """Apply one snapshot/event page in a savepoint; caller owns final commit."""
    if not isinstance(payload, dict):
        raise GlobalMenuSyncError("Global menu page must be an object")
    if "section" in payload and "rows" in payload:
        payload = _normalize_contract_snapshot_page(payload)
    elif stream == "events" and "event_head_seq" in payload:
        payload = _normalize_contract_event_page(payload)
    capability = capability or require_global_menu_capability(conn)
    _validate_schema_version(payload)
    group_id = _validate_group(payload, capability)
    page_revision = _revision(
        payload.get("catalog_revision", payload.get("menu_revision", 0)),
        "catalog_revision",
    )
    current_revision = int(capability.catalog_revision or 0)
    if page_revision < current_revision:
        return {"status": "stale", "catalog_revision": current_revision, "rows_applied": 0}

    conn.execute("SAVEPOINT global_menu_page")
    touched_menu_item_ids: set[str] = set()
    projection_stats = {"rows_materialized": 0, "rows_deactivated": 0}
    try:
        if stream == "events":
            events = _iter_dicts(payload.get("events"), "events")
            previous = current_revision
            page_previous: Optional[int] = None
            rows_applied = 0
            for event in events:
                event_group = str(event.get("menu_group_id") or group_id).strip()
                if event_group != group_id:
                    raise GlobalMenuSyncError("Event belongs to another menu group")
                event_revision = _revision(event.get("catalog_revision", event.get("revision")))
                event_id = _nonblank(event, "event_id")
                mutation_id = _nonblank(event, "mutation_id")
                known = conn.execute(
                    """
                    SELECT mutation_id, catalog_revision FROM global_menu_events
                    WHERE event_id=?
                    """,
                    (event_id,),
                ).fetchone()
                if known:
                    if str(known[0]) != mutation_id or int(known[1]) != event_revision:
                        raise GlobalMenuSyncError(
                            f"Event {event_id} was replayed with different identity or revision"
                        )
                    previous = max(previous, event_revision)
                    page_previous = event_revision
                    continue
                if page_previous is None and event_revision <= previous:
                    raise GlobalMenuSyncError(
                        "Global menu event does not advance the current revision"
                    )
                if page_previous is not None and event_revision <= page_previous:
                    raise GlobalMenuSyncError("Global menu event revisions are not strictly increasing")
                body = event.get("payload") if isinstance(event.get("payload"), dict) else event
                _apply_event_tombstones(
                    conn, body.get("tombstones"), group_id=group_id
                )
                _upsert_items(conn, _iter_dicts(body.get("items"), "items"), group_id, event_revision)
                _upsert_variants(
                    conn, _iter_dicts(body.get("variants"), "variants"), group_id, event_revision
                )
                _upsert_redirects(
                    conn, _iter_dicts(body.get("redirects"), "redirects"), group_id, event_revision
                )
                _upsert_rules(
                    conn,
                    _iter_dicts(body.get("mapping_rules"), "mapping_rules"),
                    group_id,
                    event_revision,
                    capability,
                )
                _upsert_links(conn, body, event_revision, group_id)
                apply_local_projection_plan(conn, plan_local_projection(conn))
                if body.get("assignments"):
                    assignment_result = apply_global_assignment_rows(
                        conn,
                        body["assignments"],
                        run_epilogue=False,
                        capability=capability,
                    )
                    touched_menu_item_ids |= set(
                        assignment_result.get("touched_menu_item_ids") or ()
                    )
                conn.execute(
                    """
                    INSERT INTO global_menu_events (
                        event_id, menu_group_id, mutation_id, event_type,
                        catalog_revision, payload, origin_restaurant_id, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (
                        event_id,
                        group_id,
                        mutation_id,
                        str(event.get("event_type") or "global_menu.changed"),
                        event_revision,
                        json.dumps(body, sort_keys=True, default=str),
                        event.get("origin_restaurant_id"),
                        event.get("occurred_at"),
                    ),
                )
                previous = event_revision
                page_previous = event_revision
                rows_applied += 1
            page_revision = max(current_revision, previous)
        else:
            items = _iter_dicts(payload.get("items"), "items")
            variants = _iter_dicts(payload.get("variants"), "variants")
            _upsert_items(conn, items, group_id, page_revision)
            _upsert_variants(conn, variants, group_id, page_revision)
            _upsert_redirects(
                conn, _iter_dicts(payload.get("redirects"), "redirects"), group_id, page_revision
            )
            _upsert_rules(
                conn,
                _iter_dicts(payload.get("mapping_rules"), "mapping_rules"),
                group_id,
                page_revision,
                capability,
            )
            # The plan is sorted by immutable global ID and never moves usage
            # without a versioned server assignment.
            _upsert_links(conn, payload, page_revision, group_id)
            apply_local_projection_plan(conn, plan_local_projection(conn))
            rows_applied = len(items) + len(variants)

        coverage = payload.get("coverage") or {}
        linked = _revision(coverage.get("linked", capability.coverage_linked), "coverage.linked")
        total = _revision(coverage.get("total", capability.coverage_total), "coverage.total")
        if linked > total:
            raise GlobalMenuSyncError("Global menu coverage linked exceeds total")
        state_values: Dict[str, Any] = {
            "mode": GLOBAL_MENU_MODE,
            "menu_group_id": group_id,
            "catalog_revision": page_revision,
            "mutation_revision": _revision(
                payload.get("mutation_revision", capability.mutation_revision),
                "mutation_revision",
            ),
            "coverage_linked": linked,
            "coverage_total": total,
            "last_error": None,
        }
        cursor_field = "event_cursor" if stream == "events" else "snapshot_cursor"
        if "next_cursor" in payload:
            state_values[cursor_field] = payload.get("next_cursor")
        if stream == "snapshot" and (
            "event_cursor" in payload or "event_tail_cursor" in payload
        ):
            state_values["event_cursor"] = payload.get(
                "event_cursor", payload.get("event_tail_cursor")
            )
        update_global_menu_state(conn, **state_values)
        conn.execute("RELEASE SAVEPOINT global_menu_page")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT global_menu_page")
        conn.execute("RELEASE SAVEPOINT global_menu_page")
        raise
    if touched_menu_item_ids:
        from src.core.menu_merge_sync import _run_assignment_batch_epilogue

        _run_assignment_batch_epilogue(conn, touched_menu_item_ids)
    return {
        "status": "applied",
        "catalog_revision": page_revision,
        "rows_applied": rows_applied,
        "next_cursor": payload.get("next_cursor"),
        "has_more": bool(payload.get("has_more")),
        **projection_stats,
    }


def _unique_store_item_name(conn, name: str, item_type: str, identity_key: str) -> str:
    base = str(name or "").strip() or "UNKNOWN"
    row = conn.execute(
        "SELECT menu_item_id FROM menu_items WHERE name=? AND type=?",
        (base, item_type),
    ).fetchone()
    if row is None:
        return base
    candidate = f"{base} (Store)"
    row = conn.execute(
        "SELECT menu_item_id FROM menu_items WHERE name=? AND type=?",
        (candidate, item_type),
    ).fetchone()
    if row is None:
        return candidate
    digest = hashlib.sha256(str(identity_key).encode("utf-8")).hexdigest()[:8]
    return f"{candidate} [{digest}]"


def _store_qualified_item_target(
    conn, requested_id: str, fallback_id: str
) -> Optional[str]:
    row = conn.execute(
        """
        SELECT m.name, m.type, m.is_active, m.is_verified,
               l.global_menu_item_id
        FROM menu_items m
        LEFT JOIN menu_item_global_links l ON l.local_menu_item_id=m.menu_item_id
        WHERE m.menu_item_id=?
        """,
        (requested_id,),
    ).fetchone()
    source_id = requested_id
    if row is None:
        row = conn.execute(
            """
            SELECT m.name, m.type, m.is_active, m.is_verified,
                   l.global_menu_item_id
            FROM menu_items m
            LEFT JOIN menu_item_global_links l ON l.local_menu_item_id=m.menu_item_id
            WHERE m.menu_item_id=?
            """,
            (fallback_id,),
        ).fetchone()
        source_id = fallback_id
    if row is None:
        return None
    if source_id == requested_id and row[4] is None:
        return requested_id
    local_id = generate_deterministic_id("store-qualified-item", requested_id)
    exists = conn.execute(
        "SELECT 1 FROM menu_items WHERE menu_item_id=?", (local_id,)
    ).fetchone()
    if exists is None:
        local_name = _unique_store_item_name(
            conn, str(row[0]), str(row[1]), requested_id
        )
        conn.execute(
            """
            INSERT INTO menu_items (
                menu_item_id, name, type, is_active, is_verified
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (local_id, local_name, row[1], int(row[2] or 0), int(row[3] or 0)),
        )
    return local_id


def _store_qualified_variant_target(
    conn, requested_id: Optional[str], fallback_id: Optional[str]
) -> Optional[str]:
    variant_id = str(requested_id or "").strip() or generate_deterministic_id("UNKNOWN")
    row = conn.execute(
        """
        SELECT v.variant_name, v.description, v.unit, v.value, v.is_verified,
               l.global_variant_id
        FROM variants v
        LEFT JOIN variant_global_links l ON l.local_variant_id=v.variant_id
        WHERE v.variant_id=?
        """,
        (variant_id,),
    ).fetchone()
    source_id = variant_id
    if row is None and fallback_id:
        row = conn.execute(
            """
            SELECT v.variant_name, v.description, v.unit, v.value, v.is_verified,
                   l.global_variant_id
            FROM variants v
            LEFT JOIN variant_global_links l ON l.local_variant_id=v.variant_id
            WHERE v.variant_id=?
            """,
            (str(fallback_id),),
        ).fetchone()
        source_id = str(fallback_id)
    if row is None:
        local_name = unique_local_variant_name(
            conn,
            "UNKNOWN",
            identity_key=f"store:{variant_id}",
            exclude_variant_id=variant_id,
        )
        conn.execute(
            """
            INSERT INTO variants (variant_id, variant_name, is_verified)
            VALUES (?, ?, 0)
            """,
            (variant_id, local_name),
        )
        return variant_id
    if source_id == variant_id and row[5] is None:
        return variant_id
    local_id = generate_deterministic_id("store-qualified-variant", variant_id)
    exists = conn.execute(
        "SELECT 1 FROM variants WHERE variant_id=?", (local_id,)
    ).fetchone()
    if exists is None:
        local_name = unique_local_variant_name(
            conn,
            str(row[0]),
            unit=row[2],
            value=row[3],
            identity_key=f"store:{variant_id}",
            exclude_variant_id=local_id,
        )
        conn.execute(
            """
            INSERT INTO variants (
                variant_id, variant_name, description, unit, value, is_verified
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (local_id, local_name, row[1], row[2], row[3], int(row[4] or 0)),
        )
    return local_id


def _projection_owner_matches(
    conn,
    *,
    local_item_id: str,
    local_variant_id: str,
    global_item_id: str,
    global_variant_id: str,
) -> bool:
    """True when this mapping already points at both requested projection owners."""
    try:
        canonical_item_id = resolve_redirect_chain(conn, "item", global_item_id)
        canonical_variant_id = resolve_redirect_chain(
            conn, "variant", global_variant_id
        )
    except GlobalMenuIdentityError:
        # Preserve the legacy stale guard for an invalid identity. A row that
        # actually needs projection will still validate/fail in the normal
        # authoritative apply path below.
        return False
    item_link = conn.execute(
        """
        SELECT 1 FROM menu_item_global_links
        WHERE local_menu_item_id=? AND global_menu_item_id=?
          AND is_projection_owner=1
        """,
        (local_item_id, canonical_item_id),
    ).fetchone()
    variant_link = conn.execute(
        """
        SELECT 1 FROM variant_global_links
        WHERE local_variant_id=? AND global_variant_id=?
          AND is_projection_owner=1
        """,
        (local_variant_id, canonical_variant_id),
    ).fetchone()
    return item_link is not None and variant_link is not None


def _can_apply_reviewed_locator_projection(
    conn,
    *,
    capability: GlobalMenuCapabilityStatus,
    order_item_id: str,
    local: Any,
    global_item_id: Optional[str],
    global_variant_id: Optional[str],
    server_revision: int,
) -> bool:
    """Allow a newer reviewed POS rule to move an acknowledged global projection."""
    if (
        not global_item_id
        or not global_variant_id
        or int(local[4] or 0)
        or server_revision <= 0
    ):
        return False
    current = conn.execute(
        """
        SELECT item_link.global_menu_item_id, variant_link.global_variant_id
        FROM menu_item_global_links item_link
        JOIN variant_global_links variant_link
          ON variant_link.local_variant_id=?
         AND variant_link.is_projection_owner=1
        WHERE item_link.local_menu_item_id=?
          AND item_link.is_projection_owner=1
        """,
        (str(local[1]), str(local[0])),
    ).fetchone()
    if current is None or tuple(current) == (global_item_id, global_variant_id):
        return False
    return conn.execute(
        """
        SELECT 1
        FROM global_menu_mapping_rules
        WHERE menu_group_id=?
          AND locator_scope='restaurant'
          AND restaurant_id=?
          AND locator_kind IN ('pos-item', 'pos-addon')
          AND locator_value=?
          AND target_global_menu_item_id=?
          AND target_global_variant_id=?
          AND lifecycle_state='active'
          AND server_revision <= ?
        """,
        (
            capability.menu_group_id,
            capability.restaurant_id,
            order_item_id,
            global_item_id,
            global_variant_id,
            server_revision,
        ),
    ).fetchone() is not None


def apply_global_assignment_rows(
    conn,
    rows: Any,
    *,
    run_epilogue: bool = True,
    server_revision: Optional[int] = None,
    capability: Optional[GlobalMenuCapabilityStatus] = None,
) -> Dict[str, Any]:
    """Project assignment global IDs while preserving the legacy sequence guard.

    The frozen assignment endpoint carries the existing ``last_seq`` guard inside
    a revision-1.4 menu-group envelope. Linked rows move to immutable canonical
    projection owners; rows whose global IDs are omitted move to deterministic
    store-qualified targets. Every move preserves assignment sequence and
    pending-local state.
    """
    from src.core.menu_assignment_apply import apply_assignments
    from src.core.menu_assignment_schema import ensure_assignment_sync_schema
    from src.core.menu_merge_sync import _run_assignment_batch_epilogue

    ensure_assignment_sync_schema(conn)
    capability = capability or resolve_global_menu_capability(conn)
    assignments = _iter_dicts(rows, "assignments")
    revision = _revision(server_revision or 0, "menu_group_revision")
    applied = missing = stale = unlinked = 0
    recovered_reviewed_locator = 0
    touched: set[str] = set()
    for row in assignments:
        order_item_id = _nonblank(row, "order_item_id")
        local = conn.execute(
            """
            SELECT menu_item_id, variant_id, assignment_seq, verification_seq,
                   pending_local
            FROM menu_item_variants WHERE order_item_id=?
            """,
            (order_item_id,),
        ).fetchone()
        if local is None:
            missing += 1
            continue
        global_item_id = str(row.get("global_menu_item_id") or "").strip() or None
        global_variant_id = str(row.get("global_variant_id") or "").strip() or None
        last_seq_raw = row.get("last_seq", row.get("assignment_seq", row.get("server_seq")))
        if last_seq_raw is not None and local[2] is not None:
            last_seq = _revision(last_seq_raw, "last_seq")
            if int(local[2]) > last_seq:
                already_projected = bool(
                    global_item_id
                    and global_variant_id
                    and not int(local[4] or 0)
                    and _projection_owner_matches(
                        conn,
                        local_item_id=str(local[0]),
                        local_variant_id=str(local[1]),
                        global_item_id=global_item_id,
                        global_variant_id=global_variant_id,
                    )
                )
                recover_reviewed_locator = _can_apply_reviewed_locator_projection(
                    conn,
                    capability=capability,
                    order_item_id=order_item_id,
                    local=local,
                    global_item_id=global_item_id,
                    global_variant_id=global_variant_id,
                    server_revision=revision,
                )
                if recover_reviewed_locator:
                    recovered_reviewed_locator += 1
                elif not already_projected:
                    stale += 1
                    continue
        local_item_id = str(local[0])
        if global_item_id is not None:
            item_owner = ensure_item_projection_owner(conn, global_item_id)
        else:
            unlinked += 1
            requested_item_id = _nonblank(row, "menu_item_id")
            item_owner = _store_qualified_item_target(
                conn, requested_item_id, local_item_id
            )
            if item_owner is None:
                missing += 1
                continue

        if global_variant_id:
            variant_owner = ensure_variant_projection_owner(conn, global_variant_id)
        else:
            requested_variant_id = str(row.get("variant_id") or "").strip() or None
            variant_owner = _store_qualified_variant_target(
                conn,
                requested_variant_id,
                str(local[1]) if local[1] is not None else None,
            )
            if variant_owner is None:
                missing += 1
                continue

        target_variant_id = variant_owner
        changes = local_item_id != item_owner or str(local[1]) != target_variant_id
        if changes:
            original_seq = local[2]
            original_pending = int(local[4] or 0)
            guard_candidates = [0]
            if original_seq is not None:
                guard_candidates.append(int(original_seq))
            if last_seq_raw is not None:
                guard_candidates.append(_revision(last_seq_raw, "last_seq"))
            assignment = {
                "order_item_id": order_item_id,
                "menu_item_id": item_owner,
                "variant_id": target_variant_id,
            }
            move = apply_assignments(
                conn,
                [assignment],
                max(guard_candidates) + 1,
                event={},
                detect_supersede=False,
                write_is_verified=False,
            )
            if int(move.get("rows_applied") or 0):
                conn.execute(
                    """
                    UPDATE menu_item_variants
                    SET assignment_seq=?, pending_local=?
                    WHERE order_item_id=?
                    """,
                    (original_seq, original_pending, order_item_id),
                )
                touched |= set(move.get("touched_menu_item_ids") or ())
        if "is_verified" in row:
            incoming_verification_raw = row.get("last_verification_seq")
            incoming_verification_seq = (
                _revision(incoming_verification_raw, "last_verification_seq")
                if incoming_verification_raw is not None
                else None
            )
            if (
                local[3] is None
                or (
                    incoming_verification_seq is not None
                    and int(local[3]) <= incoming_verification_seq
                )
            ):
                conn.execute(
                    """
                    UPDATE menu_item_variants
                    SET is_verified=?, verification_seq=COALESCE(?, verification_seq)
                    WHERE order_item_id=?
                    """,
                    (
                        1 if row.get("is_verified") else 0,
                        incoming_verification_seq,
                        order_item_id,
                    ),
                )
        applied += 1
        touched.add(local_item_id)
    if touched and run_epilogue:
        _run_assignment_batch_epilogue(conn, touched)
    return {
        "rows_applied": applied,
        "rows_missing": missing,
        "rows_stale": stale,
        "rows_recovered_reviewed_locator": recovered_reviewed_locator,
        "rows_unlinked": unlinked,
        "touched_menu_item_ids": touched,
    }


def _fetch_page(
    conn,
    endpoint: str,
    *,
    auth: Optional[str],
    cursor: Optional[str],
    limit: Optional[int],
    cursor_param: str = "after",
    extra_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from src.core.central_api import response_error_text, scoped_headers

    params = dict(extra_params or {})
    if limit is not None:
        params["limit"] = str(limit)
    if cursor not in (None, ""):
        params[cursor_param] = cursor
    last_error = "unknown transport error"
    for attempt in range(GLOBAL_MENU_FETCH_ATTEMPTS):
        try:
            import requests

            response = requests.get(
                endpoint,
                headers=scoped_headers(conn, auth_kind="sync", credential=auth),
                params=params,
                timeout=60,
            )
            if response.status_code >= 500:
                raise RuntimeError(response_error_text(response, conn=conn))
            if response.status_code >= 400:
                return {"error": response_error_text(response, conn=conn)}
            body = response.json()
            if not isinstance(body, dict):
                return {"error": "Global menu response must be an object"}
            return {"error": None, **body}
        except Exception as exc:
            last_error = str(exc)
            if attempt + 1 >= GLOBAL_MENU_FETCH_ATTEMPTS:
                break
    return {"error": last_error}


def _decode_snapshot_cursor(raw: Optional[str]) -> Dict[str, Any]:
    default = {
        "section_index": 0,
        "after": None,
        "watermark_event_seq": None,
        "watermark_revision": None,
        "seen": [],
    }
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    if not isinstance(value, dict):
        return default
    try:
        section_index = int(value.get("section_index", 0))
    except (TypeError, ValueError):
        section_index = 0
    if section_index < 0 or section_index >= len(GLOBAL_MENU_SNAPSHOT_SECTIONS):
        return default
    return {
        "section_index": section_index,
        "after": str(value.get("after") or "") or None,
        "watermark_event_seq": value.get("watermark_event_seq"),
        "watermark_revision": value.get("watermark_revision"),
        "seen": [str(item) for item in value.get("seen", []) if str(item).strip()]
        if isinstance(value.get("seen"), list)
        else [],
    }


def _snapshot_row_identity(section: str, row: Dict[str, Any], group_id: str) -> str:
    del group_id
    if section == "items":
        return _nonblank(row, "global_item_id")
    if section == "variants":
        return _nonblank(row, "global_variant_id")
    if section == "redirects":
        return f"{_nonblank(row, 'entity_type')}:{_nonblank(row, 'source_global_id')}"
    return _nonblank(row, "rule_id")


def _reconcile_complete_snapshot_section(
    conn, *, section: str, group_id: str, seen: Sequence[str]
) -> List[Tuple[str, str]]:
    """Remove rules/redirects absent from a completed authoritative section.

    Catalog rows use explicit lifecycle states and are never omission-deleted.
    Redirect/rule removals happen only through revisioned undo events; the event
    tail triggers this complete-section resnapshot before reconciliation.
    """
    if section == "redirects":
        table, id_column = "global_menu_redirects", "redirect_id"
    elif section == "rules":
        table, id_column = "global_menu_mapping_rules", "rule_id"
    else:
        return []
    current = conn.execute(
        f"SELECT {id_column} FROM {table} WHERE menu_group_id=?", (group_id,)
    ).fetchall()
    keep = set(seen)
    removed_pos_locators: List[Tuple[str, str]] = []
    for row in current:
        row_id = str(row[0])
        if row_id not in keep:
            if table == "global_menu_mapping_rules":
                locator = conn.execute(
                    """
                    SELECT locator_kind, locator_value
                    FROM global_menu_mapping_rules WHERE rule_id=?
                    """,
                    (row_id,),
                ).fetchone()
                if locator is not None and str(locator[0]) in _POS_LOCATOR_KINDS:
                    removed_pos_locators.append((str(locator[0]), str(locator[1])))
            conn.execute(f"DELETE FROM {table} WHERE {id_column}=?", (row_id,))
    return removed_pos_locators


def _apply_global_menu_status(
    conn, payload: Dict[str, Any], capability: GlobalMenuCapabilityStatus
) -> Dict[str, Any]:
    _validate_schema_version(payload)
    group_id = _validate_group(payload, capability)
    advertised = {
        str(value).strip()
        for value in (payload.get("menu_capabilities") or [])
        if str(value).strip()
    }
    if not advertised.issubset(set(capability.capabilities)):
        raise GlobalMenuSyncError("Global menu status advertises unregistered capabilities")
    restaurant_rows = _iter_dicts(payload.get("restaurants"), "restaurants")
    own = next(
        (
            row
            for row in restaurant_rows
            if str(row.get("restaurant_id") or "") == str(capability.restaurant_id or "")
        ),
        None,
    )
    if own is None:
        raise GlobalMenuSyncError("Global menu status omitted the selected restaurant")
    linked = _revision(
        own.get("verified_assignments_linked", 0), "verified_assignments_linked"
    )
    total = _revision(own.get("verified_assignments", 0), "verified_assignments")
    if linked > total:
        raise GlobalMenuSyncError("Global menu verified coverage linked exceeds total")
    update_global_menu_state(
        conn,
        menu_group_id=group_id,
        coverage_linked=linked,
        coverage_total=total,
        last_error=None,
    )
    return {
        "status": "applied",
        "menu_group_revision": _revision(
            payload.get("menu_group_revision"), "menu_group_revision"
        ),
        "latest_event_seq": (
            _revision(payload.get("latest_event_seq"), "latest_event_seq")
            if payload.get("latest_event_seq") is not None
            else None
        ),
        "coverage_linked": linked,
        "coverage_total": total,
    }


def pull_global_menu_status(
    conn,
    *,
    auth: Optional[str] = None,
    allow_profile_sync: bool = False,
) -> Dict[str, Any]:
    capability = require_global_menu_capability(
        conn, allow_profile_sync=allow_profile_sync
    )
    endpoint = get_global_menu_status_endpoint(conn)
    if auth is None:
        _, auth = get_cloud_sync_config(conn)
    if not endpoint or not auth:
        return {"status": "error", "error": "Global menu status is not configured"}
    page = _fetch_page(conn, endpoint, auth=auth, cursor=None, limit=None)
    if page.get("error"):
        return {"status": "error", "error": page["error"]}
    try:
        result = _apply_global_menu_status(conn, page, capability)
        conn.commit()
        return result
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "error": str(exc)}


def _quarantine_failure(
    conn,
    *,
    stream: str,
    payload: Any,
    cursor: Optional[str],
    error: BaseException,
) -> None:
    quarantine_global_menu_payload(
        conn,
        payload_key=_payload_key(stream, payload, cursor),
        stream=stream,
        payload=payload,
        error_code=str(getattr(error, "code", "global_menu_payload_invalid")),
        error_message=str(error),
        menu_group_id=(payload.get("menu_group_id") if isinstance(payload, dict) else None),
        page_cursor=cursor,
        server_revision=(
            payload.get("menu_group_revision", payload.get("catalog_revision"))
            if isinstance(payload, dict)
            else None
        ),
    )
    values: Dict[str, Any] = {"last_error": str(error)}
    if stream == "snapshot":
        values["bootstrap_status"] = "error"
        if getattr(error, "code", "") == "global_menu_snapshot_moved":
            values["snapshot_cursor"] = None
    update_global_menu_state(conn, **values)
    conn.commit()


def pull_global_menu_state(
    conn,
    *,
    auth: Optional[str] = None,
    page_limit: int = GLOBAL_MENU_PAGE_LIMIT,
    allow_profile_sync: bool = False,
) -> Dict[str, Any]:
    capability = require_global_menu_capability(
        conn, allow_profile_sync=allow_profile_sync
    )
    if auth is None:
        _, auth = get_cloud_sync_config(conn)
    snapshot_endpoint = get_global_menu_snapshot_endpoint(conn)
    events_endpoint = get_global_menu_events_endpoint(conn)
    if not snapshot_endpoint or not events_endpoint or not auth:
        return {"status": "error", "error": "Global menu cloud sync is not configured"}

    status_result = pull_global_menu_status(
        conn, auth=auth, allow_profile_sync=allow_profile_sync
    )
    if status_result.get("error") or status_result.get("status") == "error":
        return {
            "status": "error",
            "error": status_result.get("error") or "Global menu status failed",
        }
    latest_event_seq = status_result.get("latest_event_seq")
    if global_menu_cache_needs_rebuild(conn, latest_event_seq=latest_event_seq):
        wipe_global_menu_projection(conn)
        conn.commit()
    state = resolve_global_menu_capability(
        conn, allow_profile_sync=allow_profile_sync
    )
    stream = "snapshot" if state.bootstrap_status != "complete" else "events"
    pages = rows_applied = 0
    if stream == "snapshot":
        progress = _decode_snapshot_cursor(state.snapshot_cursor)
        update_global_menu_state(conn, bootstrap_status="in_progress")
        conn.commit()
        for _page_number in range(GLOBAL_MENU_MAX_PAGES):
            section_index = int(progress["section_index"])
            section = GLOBAL_MENU_SNAPSHOT_SECTIONS[section_index]
            cursor = progress.get("after")
            page = _fetch_page(
                conn,
                snapshot_endpoint,
                auth=auth,
                cursor=cursor,
                limit=page_limit,
                extra_params={"section": section},
            )
            if page.get("error"):
                error = GlobalMenuSyncError(str(page["error"]))
                conn.rollback()
                _quarantine_failure(
                    conn,
                    stream="snapshot",
                    payload=page,
                    cursor=state.snapshot_cursor,
                    error=error,
                )
                return {"status": "error", "error": str(error), "pages": pages}
            try:
                current_capability = resolve_global_menu_capability(
                    conn, allow_profile_sync=allow_profile_sync
                )
                _validate_schema_version(page)
                group_id = _validate_group(page, current_capability)
                if str(page.get("section") or "") != section:
                    raise GlobalMenuSyncError("Global menu snapshot returned the wrong section")
                watermark_event_seq, watermark_revision = (
                    _contract_snapshot_watermark(page)
                )
                pinned_event_seq = progress.get("watermark_event_seq")
                pinned_revision = progress.get("watermark_revision")
                if pinned_revision is None:
                    progress["watermark_event_seq"] = watermark_event_seq
                    progress["watermark_revision"] = watermark_revision
                elif (
                    watermark_event_seq != pinned_event_seq
                    or watermark_revision != int(pinned_revision)
                ):
                    error = GlobalMenuSyncError(
                        "Global menu group changed during snapshot paging"
                    )
                    error.code = "global_menu_snapshot_moved"
                    raise error
                raw_rows = _iter_dicts(page.get("rows"), "rows")
                seen = set(progress.get("seen") or ())
                seen.update(
                    _snapshot_row_identity(section, row, group_id) for row in raw_rows
                )
                result = apply_global_menu_payload_page(
                    conn,
                    page,
                    stream="snapshot",
                    capability=resolve_global_menu_capability(
                        conn, allow_profile_sync=allow_profile_sync
                    ),
                )
                next_cursor = result.get("next_cursor")
                if result.get("has_more"):
                    progress.update(
                        {"after": str(next_cursor), "seen": sorted(seen)}
                    )
                else:
                    removed_pos_locators = _reconcile_complete_snapshot_section(
                        conn, section=section, group_id=group_id, seen=sorted(seen)
                    )
                    if section_index + 1 < len(GLOBAL_MENU_SNAPSHOT_SECTIONS):
                        progress.update(
                            {"section_index": section_index + 1, "after": None, "seen": []}
                        )
                    else:
                        result.update({"rows_materialized": 0, "rows_deactivated": 0})
                        event_cursor = str(progress["watermark_event_seq"])
                        update_global_menu_state(
                            conn,
                            bootstrap_status="complete",
                            snapshot_cursor=None,
                            event_cursor=event_cursor,
                            last_error=None,
                        )
                        resolve_global_menu_quarantine_page(
                            conn, stream="snapshot", page_cursor=state.snapshot_cursor
                        )
                        conn.commit()
                        # Drain events after the oldest watermark captured across
                        # the four independently paged snapshot sections.
                        return pull_global_menu_state(
                            conn,
                            auth=auth,
                            page_limit=page_limit,
                            allow_profile_sync=allow_profile_sync,
                        )
                encoded_progress = json.dumps(
                    progress, sort_keys=True, separators=(",", ":")
                )
                update_global_menu_state(conn, snapshot_cursor=encoded_progress)
                resolve_global_menu_quarantine_page(
                    conn, stream="snapshot", page_cursor=state.snapshot_cursor
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                _quarantine_failure(
                    conn,
                    stream="snapshot",
                    payload=page,
                    cursor=state.snapshot_cursor,
                    error=exc,
                )
                return {"status": "error", "error": str(exc), "pages": pages}
            pages += 1
            rows_applied += int(result.get("rows_applied") or 0)
        cursor = json.dumps(progress, sort_keys=True, separators=(",", ":"))
    else:
        cursor = state.event_cursor or "0"
        for _page_number in range(GLOBAL_MENU_MAX_PAGES):
            page = _fetch_page(
                conn,
                events_endpoint,
                auth=auth,
                cursor=cursor,
                limit=page_limit,
            )
            if page.get("error"):
                error = GlobalMenuSyncError(str(page["error"]))
                _quarantine_failure(
                    conn,
                    stream="events",
                    payload=page,
                    cursor=cursor,
                    error=error,
                )
                return {"status": "error", "error": str(error), "pages": pages}
            try:
                result = apply_global_menu_payload_page(
                    conn,
                    page,
                    stream="events",
                    capability=resolve_global_menu_capability(
                        conn, allow_profile_sync=allow_profile_sync
                    ),
                )
                next_cursor = str(result.get("next_cursor", cursor))
                update_global_menu_state(conn, event_cursor=next_cursor, last_error=None)
                resolve_global_menu_quarantine_page(
                    conn, stream="events", page_cursor=cursor
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                _quarantine_failure(
                    conn,
                    stream="events",
                    payload=page,
                    cursor=cursor,
                    error=exc,
                )
                return {"status": "error", "error": str(exc), "pages": pages}
            pages += 1
            rows_applied += int(result.get("rows_applied") or 0)
            cursor = next_cursor
            if result.get("has_more"):
                continue
            return {
                "status": "applied",
                "stream": "events",
                "pages": pages,
                "rows_applied": rows_applied,
                "catalog_revision": result.get("catalog_revision"),
            }

    error = GlobalMenuSyncError("Global menu pull exceeded the page safety limit")
    conn.rollback()
    _quarantine_failure(
        conn,
        stream=stream,
        payload={},
        cursor=cursor,
        error=error,
    )
    return {"status": "error", "error": str(error), "pages": pages}


def pull_global_assignment_snapshot(
    conn,
    *,
    auth: Optional[str] = None,
    page_limit: int = GLOBAL_MENU_PAGE_LIMIT,
    allow_profile_sync: bool = False,
) -> Dict[str, Any]:
    capability = require_global_menu_capability(
        conn, allow_profile_sync=allow_profile_sync
    )
    endpoint = get_global_menu_assignment_endpoint(conn)
    if auth is None:
        _, auth = get_cloud_sync_config(conn)
    if not endpoint or not auth:
        return {"status": "error", "error": "Global assignment pull is not configured"}
    cursor = capability.assignment_cursor
    pages = applied = missing = stale = 0
    recovered_reviewed_locator = 0
    for _page_number in range(GLOBAL_MENU_MAX_PAGES):
        page = _fetch_page(conn, endpoint, auth=auth, cursor=cursor, limit=page_limit)
        if page.get("error"):
            return {"status": "error", "error": page["error"], "pages": pages}
        try:
            current_capability = resolve_global_menu_capability(
                conn, allow_profile_sync=allow_profile_sync
            )
            _validate_schema_version(page)
            _validate_group(page, current_capability)
            page_revision = _revision(
                page.get("menu_group_revision"), "menu_group_revision"
            )
            if page_revision != int(current_capability.mutation_revision):
                raise GlobalMenuSyncError(
                    "Global assignment snapshot revision does not match the "
                    "applied global menu revision"
                )
            result = apply_global_assignment_rows(
                conn,
                page.get("assignments"),
                run_epilogue=True,
                server_revision=page_revision,
                capability=current_capability,
            )
            # This is the existing §17 assignment snapshot with an additive
            # global envelope. Its restaurant-scoped menu_revision remains the
            # OCC token for §19 verification commits even while canonical menu
            # mutations are group-owned.
            if page.get("menu_revision") is not None:
                from src.core.sync_identity import set_menu_state_revision

                set_menu_state_revision(
                    conn, _revision(page.get("menu_revision"), "menu_revision")
                )
            next_cursor = page.get("next_page")
            resolve_global_menu_quarantine_page(
                conn, stream="assignments", page_cursor=cursor
            )
            update_global_menu_state(conn, assignment_cursor=next_cursor, last_error=None)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            _quarantine_failure(
                conn, stream="assignments", payload=page, cursor=cursor, error=exc
            )
            return {"status": "error", "error": str(exc), "pages": pages}
        pages += 1
        applied += int(result.get("rows_applied") or 0)
        missing += int(result.get("rows_missing") or 0)
        stale += int(result.get("rows_stale") or 0)
        recovered_reviewed_locator += int(
            result.get("rows_recovered_reviewed_locator") or 0
        )
        cursor = next_cursor
        if next_cursor is None:
            return {
                "status": "applied",
                "pages": pages,
                "rows_applied": applied,
                "rows_missing": missing,
                "rows_stale": stale,
                "rows_recovered_reviewed_locator": recovered_reviewed_locator,
                "menu_revision": page.get("menu_revision"),
            }
    return {"status": "error", "error": "Global assignment pull exceeded page limit"}
