from src.core.queries.customer_query_utils import active_customer_filter, format_customer_address


def search_customers(conn, query_str: str, limit: int = 20):
    sql = """
        SELECT
            customer_id,
            name,
            phone,
            total_spent,
            last_order_date
        FROM customers
        WHERE
            {active_filter}
            AND (
                name LIKE ?
                OR CAST(phone AS TEXT) LIKE ?
                OR CAST(customer_id AS TEXT) LIKE ?
            )
        ORDER BY last_order_date DESC
        LIMIT ?
    """.format(active_filter=active_customer_filter("customers"))
    search_term = f"%{query_str}%"
    results = [dict(row) for row in conn.execute(sql, (search_term, search_term, search_term, limit)).fetchall()]
    for result in results:
        result["customer_id"] = str(result["customer_id"])
    return results


def fetch_customer_profile_data(conn, customer_id: str):
    customer_row = conn.execute(
        """
        SELECT
            customer_id,
            name,
            phone,
            address,
            total_spent,
            last_order_date,
            is_verified
        FROM customers
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    if not customer_row:
        return None, [], []

    customer = dict(customer_row)
    customer["customer_id"] = str(customer["customer_id"])
    customer["is_verified"] = bool(customer["is_verified"])

    addresses = [dict(row) for row in conn.execute(
        """
        SELECT
            address_id,
            customer_id,
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
    ).fetchall()]
    for address in addresses:
        address["customer_id"] = str(address["customer_id"])
        address["is_default"] = bool(address["is_default"])

    primary_address = next((address for address in addresses if address["is_default"]), None) or (addresses[0] if addresses else None)
    if primary_address:
        customer["address"] = format_customer_address(primary_address)

    orders = [dict(row) for row in conn.execute(
        """
        WITH order_items_summary AS (
            SELECT
                oi.order_id,
                GROUP_CONCAT(mi.name || ' (' || oi.quantity || ')', ', ') as items_summary
            FROM order_items oi
            JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
            WHERE oi.order_id IN (
                SELECT order_id FROM orders WHERE customer_id = ?
            )
            GROUP BY oi.order_id
        )
        SELECT
            o.order_id,
            COALESCE(o.petpooja_order_id, o.order_id) as order_number,
            o.created_on,
            o.total as total_amount,
            COALESCE(o.order_from, 'Unknown') as order_source,
            o.order_status as status,
            c.is_verified,
            COALESCE(ois.items_summary, 'No Items') as items_summary
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        LEFT JOIN order_items_summary ois ON o.order_id = ois.order_id
        WHERE o.customer_id = ?
        ORDER BY o.created_on DESC
        """,
        (customer_id, customer_id),
    ).fetchall()]
    for order in orders:
        order["order_id"] = str(order["order_id"])
        order["order_number"] = str(order["order_number"])
        order["is_verified"] = bool(order["is_verified"])

    return customer, orders, addresses
