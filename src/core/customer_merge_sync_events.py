import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from src.core.queries.customer_query_utils import (
    format_customer_address,
    json_loads_maybe,
    normalize_phone,
    normalize_text,
)

SCHEMA_VERSION = 1
EVENT_TYPE_APPLIED = "customer_merge.applied"
EVENT_TYPE_UNDONE = "customer_merge.undone"
SYNC_ORIGIN_CLOUD_PULL = "cloud_pull"


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


def has_customer_merge_sync_table(conn) -> bool:
    if conn is None:
        return False
    return _table_exists(conn, "customer_merge_sync_events")


def _make_event_id() -> str:
    return uuid.uuid4().hex


def _hash_value(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_order_ids(raw_value: Any) -> List[int]:
    values = json_loads_maybe(raw_value, [])
    if not isinstance(values, list):
        return []
    normalized: List[int] = []
    for value in values:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    return normalized


def _select_customer_row(conn, customer_id: int) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            customer_id,
            customer_identity_key,
            name,
            name_normalized,
            phone,
            address,
            gstin,
            first_order_date,
            last_order_date,
            total_orders,
            total_spent,
            is_verified
        FROM customers
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    return dict(row) if row else {}


def _select_customer_addresses(conn, customer_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            label,
            address_line_1,
            address_line_2,
            city,
            state,
            postal_code,
            country,
            is_default
        FROM customer_addresses
        WHERE customer_id = ?
        ORDER BY is_default DESC, address_id ASC
        """,
        (customer_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _current_address_summary(customer_row: Dict[str, Any], addresses: List[Dict[str, Any]]) -> Optional[str]:
    if addresses:
        formatted = format_customer_address(addresses[0])
        if formatted:
            return formatted
    address = customer_row.get("address")
    return address if isinstance(address, str) and address.strip() else None


def _build_snapshot(history_snapshot: Dict[str, Any], current_row: Dict[str, Any], current_address: Optional[str]) -> Dict[str, Any]:
    return {
        "name": history_snapshot.get("name") or current_row.get("name"),
        "phone": history_snapshot.get("phone") or current_row.get("phone"),
        "address": history_snapshot.get("address") or current_address,
        "gstin": history_snapshot.get("gstin") or current_row.get("gstin"),
        "total_orders": int(history_snapshot.get("total_orders") or current_row.get("total_orders") or 0),
        "total_spent": float(history_snapshot.get("total_spent") or current_row.get("total_spent") or 0.0),
        "last_order_date": history_snapshot.get("last_order_date") or current_row.get("last_order_date"),
        "is_verified": bool(
            history_snapshot.get("is_verified")
            if history_snapshot.get("is_verified") is not None
            else current_row.get("is_verified")
        ),
    }


def _build_portable_locators(
    snapshot: Dict[str, Any],
    current_row: Dict[str, Any],
    current_addresses: List[Dict[str, Any]],
) -> Dict[str, Any]:
    identity_key = current_row.get("customer_identity_key")
    if isinstance(identity_key, str) and identity_key.startswith("anon:"):
        identity_key = None

    phone_norm = normalize_phone(snapshot.get("phone"))
    name_norm = normalize_text(snapshot.get("name"))
    address_norm = normalize_text(snapshot.get("address"))

    address_book_hashes = []
    for address in current_addresses:
        formatted = format_customer_address(address)
        normalized = normalize_text(formatted)
        if normalized:
            address_book_hashes.append(_hash_value(normalized))

    name_address_hash = None
    if name_norm and address_norm:
        name_address_hash = _hash_value(f"{name_norm}|{address_norm}")

    return {
        "customer_identity_key": identity_key,
        "phone_hash": _hash_value(phone_norm) if phone_norm else None,
        "name_address_hash": name_address_hash,
        "name_normalized": name_norm or None,
        "address_normalized": address_norm or None,
        "address_book_hashes": [value for value in dict.fromkeys(address_book_hashes) if value],
    }


def _build_customer_descriptor(conn, customer_id: int, history_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    current_row = _select_customer_row(conn, customer_id)
    current_addresses = _select_customer_addresses(conn, customer_id)
    current_address = _current_address_summary(current_row, current_addresses)
    snapshot = _build_snapshot(history_snapshot, current_row, current_address)
    return {
        "snapshot": snapshot,
        "portable_locators": _build_portable_locators(snapshot, current_row, current_addresses),
    }


def _select_order_refs(conn, local_order_ids: List[int]) -> List[Dict[str, Any]]:
    if not local_order_ids:
        return []
    placeholders = ",".join("?" for _ in local_order_ids)
    rows = conn.execute(
        f"""
        SELECT
            order_id,
            petpooja_order_id,
            stream_id,
            event_id,
            aggregate_id,
            created_on,
            total
        FROM orders
        WHERE order_id IN ({placeholders})
        ORDER BY order_id ASC
        """,
        local_order_ids,
    ).fetchall()

    refs: List[Dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        refs.append(
            {
                "petpooja_order_id": entry.get("petpooja_order_id"),
                "stream_id": entry.get("stream_id"),
                "event_id": entry.get("event_id"),
                "aggregate_id": entry.get("aggregate_id"),
                "created_on": entry.get("created_on"),
                "total": float(entry.get("total") or 0.0),
                "local_order_id": int(entry["order_id"]),
            }
        )
    return refs


def _build_merge_metadata(merge_row: Dict[str, Any]) -> Dict[str, Any]:
    suggestion_context = json_loads_maybe(merge_row.get("suggestion_context"), {})
    if not isinstance(suggestion_context, dict):
        suggestion_context = {}

    target_before_fields = suggestion_context.get("target_before_fields", {})
    if not isinstance(target_before_fields, dict):
        target_before_fields = {}

    reasons = suggestion_context.get("reasons", [])
    if not isinstance(reasons, list):
        reasons = []

    target_is_verified_after_merge = bool(suggestion_context.get("target_is_verified_after_merge"))
    target_was_verified = bool(target_before_fields.get("is_verified"))

    return {
        "similarity_score": merge_row.get("similarity_score"),
        "model_name": merge_row.get("model_name"),
        "reasons": reasons,
        "copied_address_count": int(merge_row.get("copied_address_count") or 0),
        "target_before_fields": target_before_fields,
        "target_is_verified_after_merge": target_is_verified_after_merge,
        "mark_target_verified": target_is_verified_after_merge and not target_was_verified,
    }


def _build_local_refs(merge_row: Dict[str, Any], moved_order_ids: List[int]) -> Dict[str, Any]:
    suggestion_context = json_loads_maybe(merge_row.get("suggestion_context"), {})
    if not isinstance(suggestion_context, dict):
        suggestion_context = {}
    undo_context = json_loads_maybe(merge_row.get("undo_context"), {})
    if not isinstance(undo_context, dict):
        undo_context = {}
    return {
        "merge_id": int(merge_row["merge_id"]),
        "source_customer_id": int(merge_row["source_customer_id"]),
        "target_customer_id": int(merge_row["target_customer_id"]),
        "moved_order_ids": moved_order_ids,
        "inserted_target_address_ids": suggestion_context.get("inserted_target_address_ids", []),
        "removed_target_address_ids": undo_context.get("removed_target_address_ids", []),
    }


def _get_merge_row(conn, merge_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM customer_merge_history WHERE merge_id = ?",
        (merge_id,),
    ).fetchone()
    return dict(row) if row else None


def _merge_suggestion_context(merge_row: Dict[str, Any]) -> Dict[str, Any]:
    suggestion_context = json_loads_maybe(merge_row.get("suggestion_context"), {})
    return suggestion_context if isinstance(suggestion_context, dict) else {}


def _merge_undo_context(merge_row: Dict[str, Any]) -> Dict[str, Any]:
    undo_context = json_loads_maybe(merge_row.get("undo_context"), {})
    return undo_context if isinstance(undo_context, dict) else {}


def _is_cloud_pull_origin(context: Dict[str, Any]) -> bool:
    return str(context.get("sync_origin") or "").strip() == SYNC_ORIGIN_CLOUD_PULL


def _lookup_event_id(conn, merge_id: int, event_type: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT event_id
        FROM customer_merge_sync_events
        WHERE merge_id = ? AND event_type = ?
        LIMIT 1
        """,
        (merge_id, event_type),
    ).fetchone()
    return row["event_id"] if row else None


def _insert_event(conn, merge_id: int, event_type: str, occurred_at: str, payload: Dict[str, Any]) -> str:
    existing_event_id = _lookup_event_id(conn, merge_id, event_type)
    if existing_event_id:
        return existing_event_id

    event_id = payload["remote_event_id"]
    conn.execute(
        """
        INSERT INTO customer_merge_sync_events (
            event_id,
            merge_id,
            event_type,
            payload,
            occurred_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, merge_id, event_type, json.dumps(payload, sort_keys=True, default=str), occurred_at),
    )
    return event_id


def record_merge_applied_event(conn, merge_id: int) -> Optional[str]:
    if not has_customer_merge_sync_table(conn):
        return None

    merge_row = _get_merge_row(conn, merge_id)
    if not merge_row or not merge_row.get("merged_at"):
        return None
    if _is_cloud_pull_origin(_merge_suggestion_context(merge_row)):
        return None

    source_snapshot = json_loads_maybe(merge_row.get("source_snapshot"), {})
    target_snapshot = json_loads_maybe(merge_row.get("target_snapshot"), {})
    if not isinstance(source_snapshot, dict):
        source_snapshot = {}
    if not isinstance(target_snapshot, dict):
        target_snapshot = {}
    moved_order_ids = _normalize_order_ids(merge_row.get("moved_order_ids"))
    event_id = _make_event_id()

    payload = {
        "remote_event_id": event_id,
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_TYPE_APPLIED,
        "occurred_at": merge_row["merged_at"],
        "source_customer": _build_customer_descriptor(conn, int(merge_row["source_customer_id"]), source_snapshot),
        "target_customer": _build_customer_descriptor(conn, int(merge_row["target_customer_id"]), target_snapshot),
        "merge_metadata": _build_merge_metadata(merge_row),
        "moved_orders": {
            "count": len(moved_order_ids),
            "portable_refs": _select_order_refs(conn, moved_order_ids),
        },
        "local_refs": _build_local_refs(merge_row, moved_order_ids),
    }
    return _insert_event(conn, merge_id, EVENT_TYPE_APPLIED, merge_row["merged_at"], payload)


def record_merge_undone_event(conn, merge_id: int) -> Optional[str]:
    if not has_customer_merge_sync_table(conn):
        return None

    merge_row = _get_merge_row(conn, merge_id)
    if not merge_row or not merge_row.get("undone_at"):
        return None
    suggestion_context = _merge_suggestion_context(merge_row)
    undo_context = _merge_undo_context(merge_row)
    if _is_cloud_pull_origin(undo_context):
        return None

    applied_event_id = _lookup_event_id(conn, merge_id, EVENT_TYPE_APPLIED)
    if not applied_event_id:
        remote_event_id = suggestion_context.get("remote_event_id")
        if remote_event_id:
            applied_event_id = str(remote_event_id)
        else:
            applied_event_id = record_merge_applied_event(conn, merge_id)

    source_snapshot = json_loads_maybe(merge_row.get("source_snapshot"), {})
    target_snapshot = json_loads_maybe(merge_row.get("target_snapshot"), {})
    if not isinstance(source_snapshot, dict):
        source_snapshot = {}
    if not isinstance(target_snapshot, dict):
        target_snapshot = {}
    moved_order_ids = _normalize_order_ids(merge_row.get("moved_order_ids"))
    undo_context = json_loads_maybe(merge_row.get("undo_context"), {})
    if not isinstance(undo_context, dict):
        undo_context = {}
    event_id = _make_event_id()

    payload = {
        "remote_event_id": event_id,
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_TYPE_UNDONE,
        "occurred_at": merge_row["undone_at"],
        "reverts_remote_event_id": applied_event_id,
        "source_customer": _build_customer_descriptor(conn, int(merge_row["source_customer_id"]), source_snapshot),
        "target_customer": _build_customer_descriptor(conn, int(merge_row["target_customer_id"]), target_snapshot),
        "merge_metadata": _build_merge_metadata(merge_row),
        "undo_metadata": {
            "restored_order_count": int(undo_context.get("restored_order_count") or len(moved_order_ids)),
            "restored_target_fields": undo_context.get("restored_target_fields", []),
            "original_merged_at": merge_row.get("merged_at"),
        },
        "moved_orders": {
            "count": len(moved_order_ids),
            "portable_refs": _select_order_refs(conn, moved_order_ids),
        },
        "local_refs": _build_local_refs(merge_row, moved_order_ids),
    }
    return _insert_event(conn, merge_id, EVENT_TYPE_UNDONE, merge_row["undone_at"], payload)


def backfill_customer_merge_sync_events(conn) -> Dict[str, int]:
    if not has_customer_merge_sync_table(conn):
        return {"applied": 0, "undone": 0}

    counts = {"applied": 0, "undone": 0}
    rows = conn.execute("SELECT merge_id FROM customer_merge_history ORDER BY merge_id ASC").fetchall()
    for row in rows:
        merge_id = int(row["merge_id"])
        merge_row = _get_merge_row(conn, merge_id)
        if not merge_row:
            continue
        suggestion_context = _merge_suggestion_context(merge_row)
        undo_context = _merge_undo_context(merge_row)
        if _lookup_event_id(conn, merge_id, EVENT_TYPE_APPLIED) is None:
            if not _is_cloud_pull_origin(suggestion_context) and record_merge_applied_event(conn, merge_id):
                counts["applied"] += 1
        if merge_row["undone_at"] and _lookup_event_id(conn, merge_id, EVENT_TYPE_UNDONE) is None:
            if not _is_cloud_pull_origin(undo_context) and record_merge_undone_event(conn, merge_id):
                counts["undone"] += 1
    return counts
