from collections import defaultdict


def fetch_customer_order_snapshot(conn, customer_id: str, order_limit: int = 6, top_item_limit: int = 3):
    order_rows = conn.execute(
        """
        SELECT
            o.order_id,
            CAST(COALESCE(o.petpooja_order_id, o.order_id) AS TEXT) as order_number,
            o.created_on,
            COALESCE(o.total, 0) as total_amount
        FROM orders o
        WHERE o.customer_id = ?
        ORDER BY o.created_on DESC, o.order_id DESC
        LIMIT ?
        """,
        (customer_id, order_limit),
    ).fetchall()

    order_items_by_order = defaultdict(lambda: defaultdict(int))
    if order_rows:
        order_ids = [row["order_id"] for row in order_rows]
        placeholders = ",".join("?" for _ in order_ids)
        item_rows = conn.execute(
            f"""
            SELECT
                oi.order_id,
                COALESCE(mi.name, oi.name_raw) as item_name,
                COALESCE(oi.quantity, 0) as quantity
            FROM order_items oi
            LEFT JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
            WHERE oi.order_id IN ({placeholders})
            ORDER BY LOWER(COALESCE(mi.name, oi.name_raw)) ASC, oi.order_item_id ASC
            """,
            order_ids,
        ).fetchall()
        for row in item_rows:
            item_name = (row["item_name"] or "").strip()
            if not item_name:
                continue
            order_items_by_order[row["order_id"]][item_name] += int(row["quantity"] or 0)

    recent_orders = []
    for row in order_rows:
        aggregated_items = order_items_by_order.get(row["order_id"], {})
        items = [
            {"item_name": item_name, "quantity": int(quantity)}
            for item_name, quantity in sorted(aggregated_items.items(), key=lambda item: item[0].lower())
        ]
        items_summary = ", ".join(
            f"{item['item_name']} x{item['quantity']}" if item["quantity"] > 1 else item["item_name"]
            for item in items
        ) or "No items"
        recent_orders.append({
            "order_id": str(row["order_id"]),
            "order_number": row["order_number"],
            "created_on": row["created_on"],
            "total_amount": float(row["total_amount"] or 0.0),
            "items": items,
            "items_summary": items_summary,
        })

    top_item_rows = conn.execute(
        """
        SELECT
            COALESCE(mi.name, oi.name_raw) as item_name,
            COUNT(DISTINCT oi.order_id) as order_count,
            COALESCE(SUM(oi.quantity), 0) as total_quantity
        FROM order_items oi
        JOIN orders o ON o.order_id = oi.order_id
        LEFT JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        WHERE o.customer_id = ?
        GROUP BY COALESCE(mi.name, oi.name_raw)
        ORDER BY total_quantity DESC, order_count DESC, LOWER(item_name) ASC
        LIMIT ?
        """,
        (customer_id, top_item_limit),
    ).fetchall()

    return {
        "recent_orders": recent_orders,
        "top_items": [
            {
                "item_name": row["item_name"],
                "order_count": int(row["order_count"] or 0),
                "total_quantity": int(row["total_quantity"] or 0),
            }
            for row in top_item_rows
            if row["item_name"]
        ],
    }
