import json
from typing import List, Optional

from src.core.customer_merge_sync_events import (
    record_merge_applied_event,
    record_merge_undone_event,
)
from src.core.queries.customer_merge_helpers import (
    copy_customer_addresses_to_target,
    fetch_customer_mergeable_fields,
    recompute_customer_aggregates,
)
from src.core.queries.customer_query_utils import json_loads_maybe
from src.core.queries.customer_similarity_queries import fetch_customer_merge_preview


def merge_customers(
    conn,
    source_customer_id: str,
    target_customer_id: str,
    similarity_score: Optional[float] = None,
    model_name: Optional[str] = None,
    reasons: Optional[List[str]] = None,
    mark_target_verified: Optional[bool] = None,
):
    preview = fetch_customer_merge_preview(
        conn,
        source_customer_id,
        target_customer_id,
        similarity_score=similarity_score,
        model_name=model_name,
        reasons=reasons,
    )
    if preview.get("status") == "error":
        return preview

    source_customer_id = preview["source_customer"]["customer_id"]
    target_customer_id = preview["target_customer"]["customer_id"]
    source_is_verified = bool(preview["source_customer"]["is_verified"])
    target_is_verified = bool(preview["target_customer"]["is_verified"])
    should_mark_target_verified = bool(mark_target_verified) and not source_is_verified and not target_is_verified
    target_is_verified_after_merge = target_is_verified or should_mark_target_verified

    try:
        moved_order_ids = [int(row[0]) for row in conn.execute(
            "SELECT order_id FROM orders WHERE customer_id = ? ORDER BY order_id ASC",
            (source_customer_id,),
        ).fetchall()]
        address_copy_result = copy_customer_addresses_to_target(conn, source_customer_id, target_customer_id)
        target_before_fields = fetch_customer_mergeable_fields(conn, target_customer_id)

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
                source_customer_id, target_customer_id, similarity_score, model_name, suggestion_context,
                source_snapshot, target_snapshot, moved_order_ids, copied_address_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING merge_id
            """,
            (
                source_customer_id,
                target_customer_id,
                similarity_score if similarity_score is not None else preview.get("score"),
                model_name or preview.get("model_name"),
                json.dumps({
                    "reasons": reasons or preview.get("reasons", []),
                    "target_before_fields": target_before_fields,
                    "inserted_target_address_ids": address_copy_result["inserted_address_ids"],
                    "target_is_verified_after_merge": target_is_verified_after_merge,
                }),
                json.dumps(preview["source_customer"]),
                json.dumps(preview["target_customer"]),
                json.dumps(moved_order_ids),
                address_copy_result["copied_count"],
            ),
        ).fetchone()[0]

        record_merge_applied_event(conn, int(merge_id))
        recompute_customer_aggregates(conn, source_customer_id)
        recompute_customer_aggregates(conn, target_customer_id)
        conn.commit()
        return {
            "status": "success",
            "message": f"Merged customer {source_customer_id} into {target_customer_id}.",
            "merge_id": int(merge_id),
            "source_customer_id": str(source_customer_id),
            "target_customer_id": str(target_customer_id),
            "orders_moved": len(moved_order_ids),
            "target_is_verified": bool(target_is_verified_after_merge),
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}


def undo_customer_merge(conn, merge_id: int):
    row = conn.execute("SELECT * FROM customer_merge_history WHERE merge_id = ?", (merge_id,)).fetchone()
    if not row:
        return {"status": "error", "message": "Merge history entry not found."}
    if row["undone_at"] is not None:
        return {"status": "error", "message": "This merge has already been undone."}

    moved_order_ids = json_loads_maybe(row["moved_order_ids"], [])
    suggestion_context = json_loads_maybe(row["suggestion_context"], {})
    inserted_target_address_ids = suggestion_context.get("inserted_target_address_ids", [])
    target_before_fields = suggestion_context.get("target_before_fields", {})
    if not isinstance(moved_order_ids, list):
        moved_order_ids = []
    if not isinstance(inserted_target_address_ids, list):
        inserted_target_address_ids = []
    if not isinstance(target_before_fields, dict):
        target_before_fields = {}

    try:
        if moved_order_ids:
            placeholders = ",".join("?" for _ in moved_order_ids)
            conn.execute(
                f"UPDATE orders SET customer_id = ?, updated_at = CURRENT_TIMESTAMP WHERE customer_id = ? AND order_id IN ({placeholders})",
                [row["source_customer_id"], row["target_customer_id"], *moved_order_ids],
            )
        if inserted_target_address_ids:
            placeholders = ",".join("?" for _ in inserted_target_address_ids)
            conn.execute(
                f"DELETE FROM customer_addresses WHERE customer_id = ? AND address_id IN ({placeholders})",
                [row["target_customer_id"], *inserted_target_address_ids],
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
                    row["target_customer_id"],
                ),
            )

        conn.execute(
            """
            UPDATE customer_merge_history
            SET undone_at = CURRENT_TIMESTAMP, undo_context = ?
            WHERE merge_id = ?
            """,
            (json.dumps({
                "restored_order_count": len(moved_order_ids),
                "removed_target_address_ids": inserted_target_address_ids,
                "restored_target_fields": sorted(target_before_fields.keys()),
            }), merge_id),
        )
        record_merge_undone_event(conn, int(merge_id))
        recompute_customer_aggregates(conn, str(row["source_customer_id"]))
        recompute_customer_aggregates(conn, str(row["target_customer_id"]))
        conn.commit()
        return {
            "status": "success",
            "message": f"Undo complete for merge {merge_id}.",
            "merge_id": int(merge_id),
            "source_customer_id": str(row["source_customer_id"]),
            "target_customer_id": str(row["target_customer_id"]),
            "orders_moved": len(moved_order_ids),
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
