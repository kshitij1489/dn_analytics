import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.config.cloud_sync_config import get_cloud_sync_config
from src.core.customer_merge_sync_events import (
    EVENT_TYPE_APPLIED,
    EVENT_TYPE_UNDONE,
    SYNC_ORIGIN_CLOUD_PULL,
)
from src.core.queries.customer_merge_helpers import (
    copy_customer_addresses_to_target,
    fetch_customer_mergeable_fields,
    recompute_customer_aggregates,
)
from src.core.queries.customer_query_utils import (
    format_customer_address,
    json_loads_maybe,
    normalize_phone,
    normalize_text,
)

logger = logging.getLogger(__name__)

CUSTOMER_MERGE_PULL_CURSOR_KEY = "customer_merge_pull_cursor"
DEFAULT_PULL_LIMIT = 100


def get_customer_merge_pull_endpoint(conn) -> Optional[str]:
    """
    Resolve the customer merge pull endpoint URL.
    Uses system_config cloud_sync_url if set, else CLIENT_LEARNING_CUSTOMER_MERGE_PULL_URL.
    """
    from src.core.config.client_learning_config import CLIENT_LEARNING_CUSTOMER_MERGE_PULL_URL

    base_url, _ = get_cloud_sync_config(conn)
    if base_url:
        return f"{base_url}/desktop-analytics-sync/customer-merges"
    return CLIENT_LEARNING_CUSTOMER_MERGE_PULL_URL or None


def ensure_customer_merge_pull_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_merge_remote_events (
            remote_event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            reverts_remote_event_id TEXT,
            local_merge_id INTEGER REFERENCES customer_merge_history(merge_id),
            payload TEXT NOT NULL,
            remote_cursor TEXT,
            occurred_at TEXT,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK (event_type IN ('customer_merge.applied', 'customer_merge.undone'))
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_customer_merge_remote_events_reverts
        ON customer_merge_remote_events(reverts_remote_event_id);
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_customer_merge_remote_events_local_merge_id
        ON customer_merge_remote_events(local_merge_id);
        """
    )


def get_customer_merge_pull_cursor(conn) -> Optional[str]:
    ensure_customer_merge_pull_tables(conn)
    row = conn.execute(
        "SELECT value FROM system_config WHERE key = ? LIMIT 1",
        (CUSTOMER_MERGE_PULL_CURSOR_KEY,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def set_customer_merge_pull_cursor(conn, cursor: Optional[str]) -> None:
    ensure_customer_merge_pull_tables(conn)
    if cursor is None:
        conn.execute(
            "DELETE FROM system_config WHERE key = ?",
            (CUSTOMER_MERGE_PULL_CURSOR_KEY,),
        )
        return
    conn.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (CUSTOMER_MERGE_PULL_CURSOR_KEY, cursor),
    )


def _hash_value(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fetch_customer_locator_candidates(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        WITH primary_address AS (
            SELECT
                ca.customer_id,
                ca.address_line_1,
                ca.address_line_2,
                ca.city,
                ca.state,
                ca.postal_code,
                ca.country,
                ROW_NUMBER() OVER (
                    PARTITION BY ca.customer_id
                    ORDER BY ca.is_default DESC, ca.address_id ASC
                ) as rn
            FROM customer_addresses ca
        )
        SELECT
            c.customer_id,
            c.customer_identity_key,
            c.name,
            c.phone,
            c.address as legacy_address,
            CASE WHEN EXISTS (
                SELECT 1
                FROM customer_merge_history cmh
                WHERE cmh.source_customer_id = c.customer_id
                  AND cmh.undone_at IS NULL
            ) THEN 1 ELSE 0 END as is_merged_source,
            pa.address_line_1,
            pa.address_line_2,
            pa.city,
            pa.state,
            pa.postal_code,
            pa.country
        FROM customers c
        LEFT JOIN primary_address pa ON pa.customer_id = c.customer_id AND pa.rn = 1
        """
    ).fetchall()

    candidates: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        address_summary = format_customer_address(record) or record.get("legacy_address") or None
        phone_norm = normalize_phone(record.get("phone"))
        name_norm = normalize_text(record.get("name"))
        address_norm = normalize_text(address_summary)
        candidates.append(
            {
                "customer_id": int(record["customer_id"]),
                "customer_identity_key": record.get("customer_identity_key"),
                "is_merged_source": bool(record.get("is_merged_source")),
                "name_norm": name_norm,
                "address_norm": address_norm,
                "phone_hash": _hash_value(phone_norm) if phone_norm else None,
                "name_address_hash": _hash_value(f"{name_norm}|{address_norm}") if name_norm and address_norm else None,
                "address_hash": _hash_value(address_norm) if address_norm else None,
            }
        )
    return candidates


def _pick_unique_customer_id(candidates: List[Dict[str, Any]]) -> Optional[int]:
    deduped = {candidate["customer_id"]: candidate for candidate in candidates}.values()
    deduped_list = list(deduped)
    if len(deduped_list) == 1:
        return int(deduped_list[0]["customer_id"])

    active_only = [candidate for candidate in deduped_list if not candidate["is_merged_source"]]
    if len(active_only) == 1:
        return int(active_only[0]["customer_id"])
    return None


def _resolve_customer_id(conn, descriptor: Dict[str, Any]) -> Optional[int]:
    if not isinstance(descriptor, dict):
        return None

    snapshot = descriptor.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    locators = descriptor.get("portable_locators")
    if not isinstance(locators, dict):
        locators = {}

    candidates = _fetch_customer_locator_candidates(conn)

    customer_identity_key = locators.get("customer_identity_key")
    if customer_identity_key:
        resolved = _pick_unique_customer_id(
            [candidate for candidate in candidates if candidate.get("customer_identity_key") == customer_identity_key]
        )
        if resolved is not None:
            return resolved

    phone_hash = locators.get("phone_hash")
    if phone_hash:
        resolved = _pick_unique_customer_id(
            [candidate for candidate in candidates if candidate.get("phone_hash") == phone_hash]
        )
        if resolved is not None:
            return resolved

    name_address_hash = locators.get("name_address_hash")
    if name_address_hash:
        resolved = _pick_unique_customer_id(
            [candidate for candidate in candidates if candidate.get("name_address_hash") == name_address_hash]
        )
        if resolved is not None:
            return resolved

    name_norm = locators.get("name_normalized") or normalize_text(snapshot.get("name"))
    address_norm = locators.get("address_normalized") or normalize_text(snapshot.get("address"))
    if name_norm and address_norm:
        resolved = _pick_unique_customer_id(
            [
                candidate
                for candidate in candidates
                if candidate.get("name_norm") == name_norm and candidate.get("address_norm") == address_norm
            ]
        )
        if resolved is not None:
            return resolved

    address_book_hashes = locators.get("address_book_hashes")
    if isinstance(address_book_hashes, list) and address_book_hashes:
        address_hashes = {str(value) for value in address_book_hashes if value}
        resolved = _pick_unique_customer_id(
            [
                candidate
                for candidate in candidates
                if candidate.get("address_hash") in address_hashes
                and (not name_norm or candidate.get("name_norm") == name_norm)
            ]
        )
        if resolved is not None:
            return resolved

    if name_norm and len(name_norm) >= 3:
        return _pick_unique_customer_id(
            [candidate for candidate in candidates if candidate.get("name_norm") == name_norm]
        )

    return None


def _normalize_snapshot(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(descriptor, dict):
        return {}
    snapshot = descriptor.get("snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _normalize_merge_metadata(event: Dict[str, Any]) -> Dict[str, Any]:
    merge_metadata = event.get("merge_metadata")
    return merge_metadata if isinstance(merge_metadata, dict) else {}


def _normalize_reasons(merge_metadata: Dict[str, Any]) -> List[str]:
    reasons = merge_metadata.get("reasons", [])
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons if reason]


def _normalize_order_ids(raw_value: Any) -> List[int]:
    values = json_loads_maybe(raw_value, [])
    if not isinstance(values, list):
        return []
    order_ids: List[int] = []
    for value in values:
        try:
            order_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return order_ids


def _remote_event_exists(conn, remote_event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM customer_merge_remote_events WHERE remote_event_id = ? LIMIT 1",
        (remote_event_id,),
    ).fetchone()
    return row is not None


def _lookup_remote_local_merge_id(conn, remote_event_id: str) -> Optional[int]:
    row = conn.execute(
        """
        SELECT local_merge_id
        FROM customer_merge_remote_events
        WHERE remote_event_id = ?
        LIMIT 1
        """,
        (remote_event_id,),
    ).fetchone()
    if not row or row["local_merge_id"] is None:
        return None
    return int(row["local_merge_id"])


def _lookup_local_sync_event_merge_id(conn, remote_event_id: str, event_type: str) -> Optional[int]:
    sync_table = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'customer_merge_sync_events'
        LIMIT 1
        """
    ).fetchone()
    if sync_table is None:
        return None

    row = conn.execute(
        """
        SELECT merge_id
        FROM customer_merge_sync_events
        WHERE event_id = ? AND event_type = ?
        LIMIT 1
        """,
        (remote_event_id, event_type),
    ).fetchone()
    if not row or row["merge_id"] is None:
        return None
    return int(row["merge_id"])


def _record_remote_event(
    conn,
    remote_event_id: str,
    event_type: str,
    local_merge_id: Optional[int],
    payload: Dict[str, Any],
    remote_cursor: Optional[str],
    occurred_at: Optional[str],
    reverts_remote_event_id: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO customer_merge_remote_events (
            remote_event_id,
            event_type,
            reverts_remote_event_id,
            local_merge_id,
            payload,
            remote_cursor,
            occurred_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            remote_event_id,
            event_type,
            reverts_remote_event_id,
            local_merge_id,
            json.dumps(payload, sort_keys=True, default=str),
            remote_cursor,
            occurred_at,
        ),
    )


def _find_active_merge_id(conn, source_customer_id: int, target_customer_id: int) -> Optional[int]:
    row = conn.execute(
        """
        SELECT merge_id
        FROM customer_merge_history
        WHERE source_customer_id = ?
          AND target_customer_id = ?
          AND undone_at IS NULL
        ORDER BY merge_id DESC
        LIMIT 1
        """,
        (source_customer_id, target_customer_id),
    ).fetchone()
    if not row:
        return None
    return int(row["merge_id"])


def _find_latest_merge_id(conn, source_customer_id: int, target_customer_id: int) -> Optional[int]:
    row = conn.execute(
        """
        SELECT merge_id
        FROM customer_merge_history
        WHERE source_customer_id = ?
          AND target_customer_id = ?
        ORDER BY merge_id DESC
        LIMIT 1
        """,
        (source_customer_id, target_customer_id),
    ).fetchone()
    if not row:
        return None
    return int(row["merge_id"])


def _current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _apply_remote_merge_event(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    if not remote_event_id:
        raise ValueError("Merge event is missing remote_event_id")
    if _remote_event_exists(conn, remote_event_id):
        return {"status": "duplicate", "local_merge_id": _lookup_remote_local_merge_id(conn, remote_event_id)}
    local_sync_merge_id = _lookup_local_sync_event_merge_id(conn, remote_event_id, EVENT_TYPE_APPLIED)
    if local_sync_merge_id is not None:
        _record_remote_event(
            conn,
            remote_event_id=remote_event_id,
            event_type=EVENT_TYPE_APPLIED,
            local_merge_id=local_sync_merge_id,
            payload=event,
            remote_cursor=remote_cursor,
            occurred_at=str(event.get("occurred_at") or ""),
        )
        return {"status": "duplicate", "local_merge_id": local_sync_merge_id}

    source_customer_id = _resolve_customer_id(conn, event.get("source_customer", {}))
    target_customer_id = _resolve_customer_id(conn, event.get("target_customer", {}))
    if source_customer_id is None:
        raise ValueError(f"Could not resolve source customer for remote event {remote_event_id}")
    if target_customer_id is None:
        raise ValueError(f"Could not resolve target customer for remote event {remote_event_id}")
    if source_customer_id == target_customer_id:
        raise ValueError(f"Remote event {remote_event_id} resolved source and target to the same local customer")

    existing_merge_id = _find_active_merge_id(conn, source_customer_id, target_customer_id)
    if existing_merge_id is not None:
        _record_remote_event(
            conn,
            remote_event_id=remote_event_id,
            event_type=EVENT_TYPE_APPLIED,
            local_merge_id=existing_merge_id,
            payload=event,
            remote_cursor=remote_cursor,
            occurred_at=str(event.get("occurred_at") or ""),
        )
        return {"status": "duplicate", "local_merge_id": existing_merge_id}

    moved_order_ids = [
        int(row[0])
        for row in conn.execute(
            "SELECT order_id FROM orders WHERE customer_id = ? ORDER BY order_id ASC",
            (source_customer_id,),
        ).fetchall()
    ]
    address_copy_result = copy_customer_addresses_to_target(conn, str(source_customer_id), str(target_customer_id))
    target_before_fields = fetch_customer_mergeable_fields(conn, str(target_customer_id))
    merge_metadata = _normalize_merge_metadata(event)
    reasons = _normalize_reasons(merge_metadata)

    target_is_verified_after_merge = bool(target_before_fields.get("is_verified"))
    if merge_metadata.get("target_is_verified_after_merge") or merge_metadata.get("mark_target_verified"):
        target_is_verified_after_merge = True

    conn.execute(
        """
        UPDATE customers
        SET phone = COALESCE(phone, (SELECT phone FROM customers WHERE customer_id = ?)),
            address = COALESCE(address, (SELECT address FROM customers WHERE customer_id = ?)),
            gstin = COALESCE(gstin, (SELECT gstin FROM customers WHERE customer_id = ?)),
            is_verified = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE customer_id = ?
        """,
        (
            source_customer_id,
            source_customer_id,
            source_customer_id,
            1 if target_is_verified_after_merge else 0,
            target_customer_id,
        ),
    )
    conn.execute(
        """
        UPDATE orders
        SET customer_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE customer_id = ?
        """,
        (target_customer_id, source_customer_id),
    )

    merge_id = conn.execute(
        """
        INSERT INTO customer_merge_history (
            source_customer_id,
            target_customer_id,
            similarity_score,
            model_name,
            suggestion_context,
            source_snapshot,
            target_snapshot,
            moved_order_ids,
            copied_address_count,
            merged_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING merge_id
        """,
        (
            source_customer_id,
            target_customer_id,
            merge_metadata.get("similarity_score"),
            merge_metadata.get("model_name"),
            json.dumps(
                {
                    "reasons": reasons,
                    "target_before_fields": target_before_fields,
                    "inserted_target_address_ids": address_copy_result["inserted_address_ids"],
                    "target_is_verified_after_merge": target_is_verified_after_merge,
                    "remote_event_id": remote_event_id,
                    "remote_cursor": remote_cursor,
                    "sync_origin": SYNC_ORIGIN_CLOUD_PULL,
                }
            ),
            json.dumps(_normalize_snapshot(event.get("source_customer", {}))),
            json.dumps(_normalize_snapshot(event.get("target_customer", {}))),
            json.dumps(moved_order_ids),
            address_copy_result["copied_count"],
            str(event.get("occurred_at") or _current_timestamp()),
        ),
    ).fetchone()[0]

    recompute_customer_aggregates(conn, str(source_customer_id))
    recompute_customer_aggregates(conn, str(target_customer_id))
    _record_remote_event(
        conn,
        remote_event_id=remote_event_id,
        event_type=EVENT_TYPE_APPLIED,
        local_merge_id=int(merge_id),
        payload=event,
        remote_cursor=remote_cursor,
        occurred_at=str(event.get("occurred_at") or ""),
    )
    return {"status": "applied", "local_merge_id": int(merge_id)}


def _undo_local_merge_from_remote(conn, merge_row: Dict[str, Any], event: Dict[str, Any], remote_cursor: Optional[str]) -> None:
    moved_order_ids = _normalize_order_ids(merge_row.get("moved_order_ids"))
    suggestion_context = json_loads_maybe(merge_row.get("suggestion_context"), {})
    if not isinstance(suggestion_context, dict):
        suggestion_context = {}
    inserted_target_address_ids = suggestion_context.get("inserted_target_address_ids", [])
    target_before_fields = suggestion_context.get("target_before_fields", {})
    if not isinstance(inserted_target_address_ids, list):
        inserted_target_address_ids = []
    if not isinstance(target_before_fields, dict):
        target_before_fields = {}

    if moved_order_ids:
        placeholders = ",".join("?" for _ in moved_order_ids)
        conn.execute(
            f"""
            UPDATE orders
            SET customer_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE customer_id = ? AND order_id IN ({placeholders})
            """,
            [merge_row["source_customer_id"], merge_row["target_customer_id"], *moved_order_ids],
        )
    if inserted_target_address_ids:
        placeholders = ",".join("?" for _ in inserted_target_address_ids)
        conn.execute(
            f"DELETE FROM customer_addresses WHERE customer_id = ? AND address_id IN ({placeholders})",
            [merge_row["target_customer_id"], *inserted_target_address_ids],
        )
    if target_before_fields:
        conn.execute(
            """
            UPDATE customers
            SET phone = ?, address = ?, gstin = ?, is_verified = ?, updated_at = CURRENT_TIMESTAMP
            WHERE customer_id = ?
            """,
            (
                target_before_fields.get("phone"),
                target_before_fields.get("address"),
                target_before_fields.get("gstin"),
                1 if target_before_fields.get("is_verified") else 0,
                merge_row["target_customer_id"],
            ),
        )

    conn.execute(
        """
        UPDATE customer_merge_history
        SET undone_at = ?, undo_context = ?
        WHERE merge_id = ?
        """,
        (
            str(event.get("occurred_at") or _current_timestamp()),
            json.dumps(
                {
                    "restored_order_count": len(moved_order_ids),
                    "removed_target_address_ids": inserted_target_address_ids,
                    "restored_target_fields": sorted(target_before_fields.keys()),
                    "remote_event_id": str(event.get("remote_event_id") or ""),
                    "remote_cursor": remote_cursor,
                    "sync_origin": SYNC_ORIGIN_CLOUD_PULL,
                }
            ),
            merge_row["merge_id"],
        ),
    )
    recompute_customer_aggregates(conn, str(merge_row["source_customer_id"]))
    recompute_customer_aggregates(conn, str(merge_row["target_customer_id"]))


def _apply_remote_undo_event(conn, event: Dict[str, Any], remote_cursor: Optional[str]) -> Dict[str, Any]:
    remote_event_id = str(event.get("remote_event_id") or "").strip()
    if not remote_event_id:
        raise ValueError("Undo event is missing remote_event_id")
    if _remote_event_exists(conn, remote_event_id):
        return {"status": "duplicate", "local_merge_id": _lookup_remote_local_merge_id(conn, remote_event_id)}

    reverted_remote_event_id = str(event.get("reverts_remote_event_id") or "").strip()
    if not reverted_remote_event_id:
        raise ValueError(f"Undo event {remote_event_id} is missing reverts_remote_event_id")
    local_sync_merge_id = _lookup_local_sync_event_merge_id(conn, remote_event_id, EVENT_TYPE_UNDONE)
    if local_sync_merge_id is not None:
        _record_remote_event(
            conn,
            remote_event_id=remote_event_id,
            event_type=EVENT_TYPE_UNDONE,
            local_merge_id=local_sync_merge_id,
            payload=event,
            remote_cursor=remote_cursor,
            occurred_at=str(event.get("occurred_at") or ""),
            reverts_remote_event_id=reverted_remote_event_id,
        )
        return {"status": "duplicate", "local_merge_id": local_sync_merge_id}

    local_merge_id = _lookup_remote_local_merge_id(conn, reverted_remote_event_id)
    if local_merge_id is None:
        source_customer_id = _resolve_customer_id(conn, event.get("source_customer", {}))
        target_customer_id = _resolve_customer_id(conn, event.get("target_customer", {}))
        if source_customer_id is not None and target_customer_id is not None:
            local_merge_id = _find_latest_merge_id(conn, source_customer_id, target_customer_id)

    if local_merge_id is None:
        raise ValueError(
            f"Undo event {remote_event_id} references unknown remote merge event {reverted_remote_event_id}"
        )

    row = conn.execute(
        "SELECT * FROM customer_merge_history WHERE merge_id = ?",
        (local_merge_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Undo event {remote_event_id} references missing local merge {local_merge_id}")

    merge_row = dict(row)
    if merge_row.get("undone_at") is None:
        _undo_local_merge_from_remote(conn, merge_row, event, remote_cursor)
        status = "applied"
    else:
        status = "duplicate"

    _record_remote_event(
        conn,
        remote_event_id=remote_event_id,
        event_type=EVENT_TYPE_UNDONE,
        local_merge_id=int(local_merge_id),
        payload=event,
        remote_cursor=remote_cursor,
        occurred_at=str(event.get("occurred_at") or ""),
        reverts_remote_event_id=reverted_remote_event_id,
    )
    return {"status": status, "local_merge_id": int(local_merge_id)}


def _fetch_remote_events(
    endpoint: str,
    auth: Optional[str],
    cursor: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"

    params: Dict[str, str] = {}
    if cursor:
        params["cursor"] = cursor
    if limit > 0:
        params["limit"] = str(limit)

    try:
        import requests

        response = requests.get(endpoint, headers=headers, params=params or None, timeout=60)
        if response.status_code >= 400:
            return {"events": [], "next_cursor": cursor, "error": f"HTTP {response.status_code}"}
        data = response.json()
    except Exception as exc:
        return {"events": [], "next_cursor": cursor, "error": str(exc)}

    if not isinstance(data, dict):
        return {"events": [], "next_cursor": cursor, "error": "Invalid response payload"}

    events = data.get("events")
    if not isinstance(events, list):
        events = data.get("items")
    if not isinstance(events, list):
        events = []

    next_cursor = data.get("next_cursor")
    if next_cursor is None:
        next_cursor = data.get("cursor_after")
    if next_cursor is None and events:
        next_cursor = data.get("cursor")

    return {"events": events, "next_cursor": next_cursor, "error": None}


def pull_and_apply_customer_merge_events(
    conn,
    endpoint: str,
    auth: Optional[str] = None,
    limit: int = DEFAULT_PULL_LIMIT,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pull remote customer merge events from cloud and deterministically replay them locally.

    Returns:
        {
            "events_fetched": int,
            "merge_events_applied": int,
            "undo_events_applied": int,
            "events_skipped": int,
            "cursor_before": str | None,
            "cursor_after": str | None,
            "error": str | None,
        }
    """
    ensure_customer_merge_pull_tables(conn)

    cursor_before = cursor if cursor is not None else get_customer_merge_pull_cursor(conn)
    fetch_result = _fetch_remote_events(endpoint, auth=auth, cursor=cursor_before, limit=limit)
    if fetch_result.get("error"):
        return {
            "events_fetched": 0,
            "merge_events_applied": 0,
            "undo_events_applied": 0,
            "events_skipped": 0,
            "cursor_before": cursor_before,
            "cursor_after": cursor_before,
            "error": fetch_result["error"],
        }

    events = fetch_result["events"]
    next_cursor = fetch_result.get("next_cursor")
    stats = {
        "events_fetched": len(events),
        "merge_events_applied": 0,
        "undo_events_applied": 0,
        "events_skipped": 0,
        "cursor_before": cursor_before,
        "cursor_after": cursor_before,
        "error": None,
    }

    for event in events:
        if not isinstance(event, dict):
            stats["error"] = "Invalid merge event in response payload"
            return stats

        event_type = str(event.get("event_type") or "").strip()
        try:
            if event_type == EVENT_TYPE_APPLIED:
                result = _apply_remote_merge_event(conn, event, next_cursor)
                if result["status"] == "applied":
                    stats["merge_events_applied"] += 1
                else:
                    stats["events_skipped"] += 1
            elif event_type == EVENT_TYPE_UNDONE:
                result = _apply_remote_undo_event(conn, event, next_cursor)
                if result["status"] == "applied":
                    stats["undo_events_applied"] += 1
                else:
                    stats["events_skipped"] += 1
            else:
                raise ValueError(f"Unsupported remote event_type '{event_type}'")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("Customer merge pull failed for event %s: %s", event.get("remote_event_id"), exc)
            stats["error"] = str(exc)
            return stats

    cursor_after = cursor_before if next_cursor is None else str(next_cursor)
    try:
        set_customer_merge_pull_cursor(conn, cursor_after)
        conn.commit()
        stats["cursor_after"] = cursor_after
    except Exception as exc:
        conn.rollback()
        stats["error"] = str(exc)
        return stats

    return stats
