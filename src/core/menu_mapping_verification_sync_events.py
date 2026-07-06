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


VERIFICATION_EVENT_TYPES = (EVENT_VERIFIED, EVENT_BULK_VERIFIED, EVENT_REOPENED)


def extract_verification_entries(event: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Normalize a mapping-verification event to the (order_item_id, is_verified)
    rows it targets — the single derivation shared by the emitter, both client
    apply paths (pull + deferred retry), and the server, pinned by
    contracts/menu_mapping_verification_event_fixtures.json so the wire shape
    cannot drift across repos.

    Returns None when the event is not a verification event (unknown/blank
    event_type), else the list of {"order_item_id", "is_verified"} rows (0/1),
    dropping entries with a blank order_item_id or an unparseable is_verified.
    The default flag is 0 for mapping.reopened and 1 for verified / bulk. The
    list is empty when nothing is derivable (e.g. a single event with no
    order_item_id, or a bulk event with no usable mappings); callers decide
    whether an empty result is an error.
    """
    if not isinstance(event, dict):
        return None
    event_type = str(event.get("event_type") or "").strip()
    if event_type not in VERIFICATION_EVENT_TYPES:
        return None

    def _row(raw: Dict[str, Any], default_verified: int) -> Optional[Dict[str, Any]]:
        order_item_id = str(raw.get("order_item_id") or "").strip()
        if not order_item_id:
            return None
        try:
            is_verified = 1 if int(raw.get("is_verified", default_verified)) else 0
        except (TypeError, ValueError):
            return None
        return {"order_item_id": order_item_id, "is_verified": is_verified}

    if event_type == EVENT_BULK_VERIFIED:
        mappings = event.get("mappings")
        if not isinstance(mappings, list):
            return []
        out: List[Dict[str, Any]] = []
        for raw in mappings:
            if not isinstance(raw, dict):
                continue
            row = _row(raw, 1)
            if row is not None:
                out.append(row)
        return out

    default_verified = 0 if event_type == EVENT_REOPENED else 1
    row = _row(event, default_verified)
    return [row] if row is not None else []


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


def build_menu_mapping_verification_event_payloads(
    conn,
    rows: List[Dict[str, Any]],
    *,
    occurred_at: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Build mapping-verification event payloads without writing to the outbox.

    Returns one payload per chunk (single mapping.verified or bulk mapping.bulk_verified).
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
        return []

    payloads: List[Dict[str, Any]] = []
    for chunk in chunk_mapping_verification_rows(normalized, 500):
        ts = occurred_at or datetime.now(timezone.utc).isoformat()
        remote_event_id = _make_event_id()
        attribution = get_sync_attribution(conn)
        if len(chunk) == 1:
            row = chunk[0]
            payloads.append(
                {
                    "remote_event_id": remote_event_id,
                    "schema_version": SCHEMA_VERSION,
                    "event_type": EVENT_VERIFIED,
                    "occurred_at": ts,
                    "attribution": attribution,
                    "order_item_id": row["order_item_id"],
                    "menu_item_id": row["menu_item_id"],
                    "variant_id": row["variant_id"],
                    "is_verified": row["is_verified"],
                }
            )
            continue
        payloads.append(
            {
                "remote_event_id": remote_event_id,
                "schema_version": SCHEMA_VERSION,
                "event_type": EVENT_BULK_VERIFIED,
                "occurred_at": ts,
                "attribution": attribution,
                "mappings": chunk,
            }
        )
    return payloads


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
