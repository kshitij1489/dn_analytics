"""Flush local machine-derived POS assignment rows through the strict commit path."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from src.core.menu_mutation_commit import (
    MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
    build_plan,
    commit_mutation,
    strict_mode_active,
)
from src.core.order_item_key import AssignmentKeyIndex, has_local_pos_backing
from utils.menu_item_variant_enforcement import (
    addon_seeded_mapping_order_item_id,
    catalog_stub_order_item_id,
)

logger = logging.getLogger(__name__)

MAX_DERIVED_ASSIGNMENTS_PER_MUTATION = 200
DERIVED_EVENT_KIND = "derived_assignment_v1"


def _variant_snapshot(row: Any) -> Dict[str, Any] | None:
    variant_id = row["variant_id"]
    if variant_id is None:
        return None
    return {
        "variant_id": str(variant_id),
        "variant_name": str(row["variant_name"] or variant_id),
        "unit": row["unit"],
        "value": row["value"],
        "is_verified": bool(row["variant_is_verified"]),
    }


def _is_synthetic_local_row(row: Any) -> bool:
    """
    True for local-only read-model rows that can never flush: 1_PIECE catalog
    stubs and addon-only backfill rows. Both use deterministic order_item_ids
    derived from their own menu_item_id/variant_id, so they are recognized
    without touching order_items.
    """
    order_item_id = str(row["order_item_id"] or "")
    menu_item_id = str(row["menu_item_id"] or "")
    if order_item_id == catalog_stub_order_item_id(menu_item_id):
        return True
    variant_id = row["variant_id"]
    return variant_id is not None and order_item_id == addon_seeded_mapping_order_item_id(
        menu_item_id, str(variant_id)
    )


def _candidate_rows(
    conn,
    *,
    limit: int,
    key_index: AssignmentKeyIndex | None = None,
) -> List[Dict[str, Any]]:
    """
    Collect up to ``limit`` POS-backed pending rows, paging past never-flushable
    ones (synthetic stubs/addon rows, keys with no local backing) so they cannot
    starve backed rows sorted behind them.
    """
    if key_index is None:
        key_index = AssignmentKeyIndex(conn)
    window = int(limit) * 4
    offset = 0
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    while len(candidates) < limit:
        rows = conn.execute(
            """
            SELECT
                mv.order_item_id,
                mv.menu_item_id,
                mv.variant_id,
                mi.name AS menu_item_name,
                mi.type AS menu_item_type,
                mi.is_verified AS menu_item_is_verified,
                v.variant_name,
                v.unit,
                v.value,
                v.is_verified AS variant_is_verified
            FROM menu_item_variants mv
            JOIN menu_items mi ON mi.menu_item_id = mv.menu_item_id
            LEFT JOIN variants v ON v.variant_id = mv.variant_id
            WHERE mv.assignment_seq IS NULL
              AND COALESCE(mv.pending_local, 0) = 0
            ORDER BY mv.updated_at ASC, mv.order_item_id ASC
            LIMIT ? OFFSET ?
            """,
            (window, offset),
        ).fetchall()
        if not rows:
            break
        offset += len(rows)
        for row in rows:
            order_item_id = str(row["order_item_id"] or "").strip()
            if not order_item_id or order_item_id in seen:
                continue
            if _is_synthetic_local_row(row):
                continue
            if not has_local_pos_backing(conn, order_item_id, key_index=key_index):
                continue
            seen.add(order_item_id)
            candidates.append(dict(row))
            if len(candidates) >= limit:
                break
    return candidates


def _chunked(rows: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _build_catalog_delta(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    items: dict[str, dict[str, Any]] = {}
    variants: dict[str, dict[str, Any]] = {}
    for row in rows:
        menu_item_id = str(row["menu_item_id"])
        items[menu_item_id] = {
            "menu_item_id": menu_item_id,
            "name": row["menu_item_name"],
            "type": row["menu_item_type"],
            "is_verified": bool(row["menu_item_is_verified"]),
        }
        variant = _variant_snapshot(row)
        if variant is not None:
            variants[variant["variant_id"]] = variant
    return {
        "items": list(items.values()),
        "variants": list(variants.values()),
    }


def _build_event(rows: List[Dict[str, Any]], catalog_delta: Dict[str, Any]) -> Dict[str, Any]:
    assignments = [
        {
            "order_item_id": str(row["order_item_id"]),
            "menu_item_id": str(row["menu_item_id"]),
            "variant_id": row["variant_id"],
            "is_verified": 0,
        }
        for row in rows
    ]
    return {
        "remote_event_id": str(uuid.uuid4()),
        "schema_version": 2,
        "event_type": "menu_merge.applied",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "merge_payload": {
            "kind": DERIVED_EVENT_KIND,
            "assignments": assignments,
            "item_snapshots": catalog_delta["items"],
            "variant_snapshots": catalog_delta["variants"],
        },
    }


def flush_pending_derived_assignments(
    conn,
    *,
    max_batches: int = 10,
    batch_size: int = MAX_DERIVED_ASSIGNMENTS_PER_MUTATION,
) -> Dict[str, Any]:
    """
    Commit local POS-backed machine assignments so the server snapshot can own them.

    Offline / not-strict-ready installs simply keep their local rows; ingest stays
    offline-capable and the next Sync DB or scheduler cycle can retry.
    """
    if not strict_mode_active(conn):
        return {"attempted": False, "skipped": True, "reason": "strict mode not ready"}

    summary: Dict[str, Any] = {
        "attempted": True,
        "batches": 0,
        "sent": 0,
        "accepted": 0,
        "skipped_existing": 0,
        "errors": [],
    }

    key_index = AssignmentKeyIndex(conn)
    for _ in range(max_batches):
        rows = _candidate_rows(conn, limit=batch_size, key_index=key_index)
        if not rows:
            break
        for batch in _chunked(rows, batch_size):
            catalog_delta = _build_catalog_delta(batch)
            event = _build_event(batch, catalog_delta)
            order_item_ids = [str(row["order_item_id"]) for row in batch]
            plan = build_plan(
                mutation_type=MUTATION_TYPE_DERIVED_ASSIGNMENT_SYNC,
                event=event,
                catalog_delta=catalog_delta,
                order_item_ids=order_item_ids,
            )
            result = commit_mutation(conn, plan)
            summary["batches"] += 1
            summary["sent"] += len(batch)
            if result.status != "ok":
                message = result.message or "Derived assignment flush failed"
                logger.warning("Derived assignment flush stopped: %s", message)
                summary["errors"].append(message)
                return summary
            skipped_existing = getattr(result, "skipped_existing", None)
            skipped_count = len(skipped_existing) if isinstance(skipped_existing, list) else 0
            summary["accepted"] += len(batch) - skipped_count
            summary["skipped_existing"] += skipped_count

    if summary["batches"] == 0:
        summary["skipped"] = True
        summary["reason"] = "no pending derived assignments"
    return summary
