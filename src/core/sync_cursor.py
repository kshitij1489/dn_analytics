"""
Opaque replay cursor ordering for menu merge / verification pull streams.

Server cursors are base64-encoded JSON tokens ordered by (ingested_at, id), not
lexicographically by the encoded string.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

CURSOR_VERSION_KEY = "v"
CURSOR_VERSION = 2
CURSOR_INGESTED_AT_KEY = "ingested_at"
CURSOR_ID_KEY = "id"


def _parse_cursor_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError("Invalid cursor datetime.")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def decode_pull_cursor(cursor: str) -> Tuple[datetime, int]:
    """Decode a v2 opaque cursor into (ingested_at, row_id)."""
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor.") from exc

    if not isinstance(payload, dict) or payload.get(CURSOR_VERSION_KEY) != CURSOR_VERSION:
        raise ValueError("Unsupported cursor version.")

    ingested_at = _parse_cursor_datetime(payload[CURSOR_INGESTED_AT_KEY])
    row_id = int(payload[CURSOR_ID_KEY])
    return ingested_at, row_id


def pull_cursor_is_ahead(new_cursor: Optional[str], current: Optional[str]) -> bool:
    """Return True when new_cursor is strictly ahead of current in replay order."""
    if not new_cursor:
        return False
    if current is None:
        return True
    if str(new_cursor) == str(current):
        return False

    try:
        return int(new_cursor) > int(current)
    except ValueError:
        pass

    try:
        new_ingested_at, new_row_id = decode_pull_cursor(str(new_cursor))
        current_ingested_at, current_row_id = decode_pull_cursor(str(current))
    except ValueError:
        return False

    if new_ingested_at > current_ingested_at:
        return True
    if new_ingested_at < current_ingested_at:
        return False
    return new_row_id > current_row_id
