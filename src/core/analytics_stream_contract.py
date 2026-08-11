"""Revision-1.2 validators for the four raw analytics streams."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


STREAM_NAMES = {"orders", "order-items", "addons", "discounts"}


class AnalyticsStreamContractError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyticsStreamPage:
    data: List[Dict[str, Any]]
    next_cursor: Optional[int]
    total: int


def _integer_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AnalyticsStreamContractError("Stream cursor must be an integer or null") from exc


def parse_stream_page(payload: Any, stream: str) -> AnalyticsStreamPage:
    if stream not in STREAM_NAMES:
        raise AnalyticsStreamContractError(f"Unknown analytics stream: {stream}")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise AnalyticsStreamContractError("Stream response must contain a data array")
    rows: List[Dict[str, Any]] = []
    for raw in payload["data"]:
        if not isinstance(raw, dict):
            raise AnalyticsStreamContractError("Stream rows must be objects")
        row = dict(raw)
        if "stream_id" not in row:
            raise AnalyticsStreamContractError("Stream row is missing stream_id")
        if stream == "orders":
            event = row.get("raw_event")
            if not isinstance(event, dict) or not isinstance(event.get("raw_payload"), dict):
                raise AnalyticsStreamContractError("Order row is missing raw_event.raw_payload")
        else:
            if not row.get("event_id"):
                raise AnalyticsStreamContractError(f"{stream} row is missing event_id")
            if "order_pk" in row or "order_item_pk" in row:
                raise AnalyticsStreamContractError("Retired central parent keys are present")
        if stream == "addons" and not row.get("parent_order_line_key"):
            raise AnalyticsStreamContractError("Addon row is missing parent_order_line_key")
        rows.append(row)

    cursor_block = payload.get("cursor")
    cursor_value = cursor_block.get("cursor") if isinstance(cursor_block, dict) else None
    if cursor_value is None and payload.get("next_cursor") is not None:
        cursor_value = payload.get("next_cursor")
    total_raw = payload.get("total", payload.get("count", len(rows)))
    try:
        total = int(total_raw or 0)
    except (TypeError, ValueError):
        total = len(rows)
    return AnalyticsStreamPage(rows, _integer_or_none(cursor_value), total)


def parse_allowed_restaurants(payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("restaurants"), list):
        raise AnalyticsStreamContractError("Allowed-restaurants response must contain restaurants")
    result: List[Dict[str, Any]] = []
    for raw in payload["restaurants"]:
        if not isinstance(raw, dict):
            raise AnalyticsStreamContractError("Restaurant entries must be objects")
        restaurant_id = str(raw.get("restaurant_id") or "").strip()
        display_name = str(raw.get("display_name") or "").strip()
        timezone = str(raw.get("timezone") or "").strip()
        if not restaurant_id or not display_name or not timezone:
            raise AnalyticsStreamContractError("Restaurant entry is missing a required field")
        raw_group_id = raw.get("menu_group_id")
        menu_group_id = str(raw_group_id or "").strip() or None
        raw_capabilities = raw.get("menu_capabilities", [])
        if raw_capabilities is None:
            raw_capabilities = []
        if not isinstance(raw_capabilities, list) or any(
            not isinstance(value, str) for value in raw_capabilities
        ):
            raise AnalyticsStreamContractError("menu_capabilities must be an array of strings")
        menu_capabilities = sorted(
            {value.strip() for value in raw_capabilities if value.strip()}
        )
        if "global_menu_v1" in menu_capabilities and not menu_group_id:
            raise AnalyticsStreamContractError(
                "global_menu_v1 requires a non-blank menu_group_id"
            )
        result.append(
            {
                "restaurant_id": restaurant_id,
                "display_name": display_name,
                "timezone": timezone,
                "menu_group_id": menu_group_id,
                "menu_capabilities": menu_capabilities,
            }
        )
    return result


def parse_error_envelope(payload: Any) -> Mapping[str, str]:
    if not isinstance(payload, dict) or not payload.get("error"):
        raise AnalyticsStreamContractError("Central error must contain error")
    code = payload.get("code")
    if not code and "invalid api key" in str(payload["error"]).lower():
        code = "invalid_api_key"
    if not code:
        raise AnalyticsStreamContractError("Central error must contain a machine-readable code")
    return {"error": str(payload["error"]), "code": str(code)}
