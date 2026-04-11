import json
import re
from typing import Optional


def active_customer_filter(alias: str = "c") -> str:
    return f"""
        NOT EXISTS (
            SELECT 1
            FROM customer_merge_history cmh
            WHERE cmh.source_customer_id = {alias}.customer_id
              AND cmh.undone_at IS NULL
        )
    """


def normalize_phone(value: Optional[str]) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    return digits[-10:] if len(digits) > 10 else digits


def normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.lower().strip().split())


def json_loads_maybe(raw_value, fallback):
    if not raw_value:
        return fallback
    if isinstance(raw_value, (dict, list)):
        return raw_value
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return fallback


def format_customer_address(address: dict) -> str:
    parts = [
        address.get("address_line_1"),
        address.get("address_line_2"),
        address.get("city"),
        address.get("state"),
        address.get("postal_code"),
        address.get("country"),
    ]
    return ", ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
