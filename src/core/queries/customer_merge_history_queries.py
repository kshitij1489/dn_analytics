from src.core.queries.customer_query_utils import json_loads_maybe


def fetch_customer_merge_history(conn, limit: int = 20):
    history = []
    rows = conn.execute(
        """
        SELECT
            h.merge_id, h.source_customer_id, h.target_customer_id, h.similarity_score, h.model_name,
            h.moved_order_ids, h.copied_address_count, h.merged_at, h.undone_at,
            h.source_snapshot, h.target_snapshot,
            source.name as current_source_name, target.name as current_target_name
        FROM customer_merge_history h
        LEFT JOIN customers source ON source.customer_id = h.source_customer_id
        LEFT JOIN customers target ON target.customer_id = h.target_customer_id
        ORDER BY h.merged_at DESC, h.merge_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in rows:
        entry = dict(row)
        source_snapshot = json_loads_maybe(entry.get("source_snapshot"), {})
        target_snapshot = json_loads_maybe(entry.get("target_snapshot"), {})
        moved_order_ids = json_loads_maybe(entry.get("moved_order_ids"), [])
        history.append({
            "merge_id": int(entry["merge_id"]),
            "source_customer_id": str(entry["source_customer_id"]),
            "source_name": source_snapshot.get("name") or entry.get("current_source_name"),
            "target_customer_id": str(entry["target_customer_id"]),
            "target_name": target_snapshot.get("name") or entry.get("current_target_name"),
            "similarity_score": entry.get("similarity_score"),
            "model_name": entry.get("model_name"),
            "orders_moved": len(moved_order_ids),
            "copied_address_count": int(entry.get("copied_address_count") or 0),
            "merged_at": entry["merged_at"],
            "undone_at": entry.get("undone_at"),
        })
    return history
