def copy_customer_addresses_to_target(conn, source_customer_id: str, target_customer_id: str):
    source_rows = conn.execute(
        """
        SELECT label, address_line_1, address_line_2, city, state, postal_code, country, is_default
        FROM customer_addresses
        WHERE customer_id = ?
        ORDER BY is_default DESC, address_id ASC
        """,
        (source_customer_id,),
    ).fetchall()
    if not source_rows:
        return {"copied_count": 0, "inserted_address_ids": []}

    target_has_default = conn.execute(
        """
        SELECT 1
        FROM customer_addresses
        WHERE customer_id = ?
          AND is_default = 1
        LIMIT 1
        """,
        (target_customer_id,),
    ).fetchone() is not None

    copied_count = 0
    inserted_address_ids = []
    for row in source_rows:
        row_dict = dict(row)
        default_flag = 0 if target_has_default else (1 if row_dict.get("is_default") else 0)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO customer_addresses (
                customer_id, label, address_line_1, address_line_2, city, state, postal_code, country, is_default
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_customer_id,
                row_dict.get("label"),
                row_dict.get("address_line_1"),
                row_dict.get("address_line_2"),
                row_dict.get("city"),
                row_dict.get("state"),
                row_dict.get("postal_code"),
                row_dict.get("country"),
                default_flag,
            ),
        )
        if cursor.rowcount:
            copied_count += 1
            inserted_address_ids.append(int(cursor.lastrowid))
            if default_flag:
                target_has_default = True

    return {"copied_count": copied_count, "inserted_address_ids": inserted_address_ids}


def fetch_customer_mergeable_fields(conn, customer_id: str):
    row = conn.execute(
        """
        SELECT phone, address, gstin
        FROM customers
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    return dict(row) if row else {"phone": None, "address": None, "gstin": None}


def recompute_customer_aggregates(conn, customer_id: str) -> None:
    aggregate_row = conn.execute(
        """
        SELECT
            COUNT(*) as total_orders,
            COALESCE(SUM(total), 0) as total_spent,
            MIN(created_on) as first_order_date,
            MAX(created_on) as last_order_date
        FROM orders
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()

    conn.execute(
        """
        UPDATE customers
        SET total_orders = ?,
            total_spent = ?,
            first_order_date = ?,
            last_order_date = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE customer_id = ?
        """,
        (
            int(aggregate_row["total_orders"] or 0),
            float(aggregate_row["total_spent"] or 0.0),
            aggregate_row["first_order_date"],
            aggregate_row["last_order_date"],
            customer_id,
        ),
    )
