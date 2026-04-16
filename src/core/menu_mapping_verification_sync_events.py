"""
Append-only sync events for menu_item_variants verification (mapping.verified / bulk / reopened).
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.sync_identity import get_sync_attribution

SCHEMA_VERSION = 1
EVENT_VERIFIED = "mapping.verified"
EVENT_BULK_VERIFIED = "mapping.bulk_verified"
EVENT_REOPENED = "mapping.reopened"


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_menu_mapping_verification_sync_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS menu_mapping_verification_sync_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            upload_attempted_at TEXT,
            uploaded_at TEXT,
            last_error TEXT,
            CHECK (event_type IN ('mapping.verified', 'mapping.bulk_verified', 'mapping.reopened'))
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_menu_mapping_verification_sync_events_pending
        ON menu_mapping_verification_sync_events(uploaded_at, occurred_at, created_at);
        """
    )


def has_menu_mapping_verification_sync_table(conn) -> bool:
    if conn is None:
        return False
    return _table_exists(conn, "menu_mapping_verification_sync_events")


def _make_event_id() -> str:
    return uuid.uuid4().hex


def _normalize_row(
    order_item_id: str,
    menu_item_id: str,
    variant_id: Any,
    is_verified: int,
) -> Dict[str, Any]:
    return {
        "order_item_id": str(order_item_id or "").strip(),
        "menu_item_id": str(menu_item_id or "").strip(),
        "variant_id": None if variant_id in (None, "None") else str(variant_id),
        "is_verified": 1 if int(is_verified or 0) else 0,
    }


def _insert_event(conn, event_type: str, occurred_at: str, payload: Dict[str, Any]) -> str:
    ensure_menu_mapping_verification_sync_tables(conn)
    event_id = str(payload["remote_event_id"])
    conn.execute(
        """
        INSERT INTO menu_mapping_verification_sync_events (
            event_id,
            event_type,
            payload,
            occurred_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            json.dumps(payload, sort_keys=True, default=str),
            occurred_at,
        ),
    )
    return event_id


def record_menu_mapping_verification_events(
    conn,
    rows: List[Dict[str, Any]],
    *,
    occurred_at: Optional[str] = None,
) -> Optional[str]:
    """
    Queue one cloud sync event for the given mapping rows (order_item_id + target menu_item_id / variant_id).

    rows: dicts with keys order_item_id, menu_item_id, variant_id (optional), is_verified (default 1).
    Uses mapping.bulk_verified when len(rows) > 1, else mapping.verified.
    """
    normalized: List[Dict[str, Any]] = []
    for raw in rows:
        oid = str(raw.get("order_item_id") or "").strip()
        mid = str(raw.get("menu_item_id") or "").strip()
        if not oid or not mid:
            continue
        normalized.append(
            _normalize_row(
                oid,
                mid,
                raw.get("variant_id"),
                int(raw.get("is_verified", 1)),
            )
        )
    if not normalized:
        return None

    ts = occurred_at or datetime.now(timezone.utc).isoformat()
    remote_event_id = _make_event_id()
    attribution = get_sync_attribution(conn)

    if len(normalized) == 1:
        r = normalized[0]
        payload: Dict[str, Any] = {
            "remote_event_id": remote_event_id,
            "schema_version": SCHEMA_VERSION,
            "event_type": EVENT_VERIFIED,
            "occurred_at": ts,
            "attribution": attribution,
            "order_item_id": r["order_item_id"],
            "menu_item_id": r["menu_item_id"],
            "variant_id": r["variant_id"],
            "is_verified": r["is_verified"],
        }
        return _insert_event(conn, EVENT_VERIFIED, ts, payload)

    payload = {
        "remote_event_id": remote_event_id,
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_BULK_VERIFIED,
        "occurred_at": ts,
        "attribution": attribution,
        "mappings": normalized,
    }
    return _insert_event(conn, EVENT_BULK_VERIFIED, ts, payload)


def chunk_mapping_verification_rows(
    rows: List[Dict[str, Any]], chunk_size: int = 500
) -> List[List[Dict[str, Any]]]:
    if chunk_size < 1:
        chunk_size = 500
    return [rows[i : i + chunk_size] for i in range(0, len(rows), chunk_size)]


def record_menu_mapping_verification_events_chunked(
    conn,
    rows: List[Dict[str, Any]],
    *,
    occurred_at: Optional[str] = None,
) -> List[str]:
    """Record in chunks of up to 500 mappings per bulk event."""
    ids: List[str] = []
    for chunk in chunk_mapping_verification_rows(rows, 500):
        eid = record_menu_mapping_verification_events(conn, chunk, occurred_at=occurred_at)
        if eid:
            ids.append(eid)
    return ids
