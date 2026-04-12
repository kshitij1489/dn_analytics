import pandas as pd

from src.core.queries.customer_metric_helpers import (
    build_monthly_customer_metric_rows,
    fetch_customer_metric_orders,
)


def fetch_customer_loyalty(conn):
    rows = build_monthly_customer_metric_rows(fetch_customer_metric_orders(conn))
    return pd.DataFrame(rows)


def fetch_top_customers(conn):
    query = """
        WITH customer_item_counts AS (
            SELECT
                o.customer_id,
                mi.name as item_name,
                SUM(oi.quantity) as item_qty
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
            WHERE o.order_status = 'Success'
            GROUP BY o.customer_id, mi.name

            UNION ALL

            SELECT
                o.customer_id,
                mi.name as item_name,
                SUM(oia.quantity) as item_qty
            FROM order_item_addons oia
            JOIN order_items oi ON oia.order_item_id = oi.order_item_id
            JOIN orders o ON oi.order_id = o.order_id
            JOIN menu_items mi ON oia.menu_item_id = mi.menu_item_id
            WHERE o.order_status = 'Success'
            GROUP BY o.customer_id, mi.name
        ),
        final_counts AS (
            SELECT customer_id, item_name, SUM(item_qty) as total_item_qty
            FROM customer_item_counts
            GROUP BY customer_id, item_name
        ),
        ranked_items AS (
            SELECT
                customer_id,
                item_name,
                total_item_qty,
                ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total_item_qty DESC, item_name ASC) as rn
            FROM final_counts
        )
        SELECT
            c.customer_id,
            c.name,
            c.total_orders,
            c.total_spent,
            c.last_order_date,
            CASE WHEN c.total_orders > 1 THEN 'Returning' ELSE 'New' END as status,
            ri.item_name as favorite_item,
            ri.total_item_qty as fav_item_qty
        FROM customers c
        LEFT JOIN ranked_items ri ON c.customer_id = ri.customer_id AND ri.rn = 1
        WHERE c.is_verified = 1
        ORDER BY c.total_spent DESC
        LIMIT 50
    """
    rows = conn.execute(query).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def fetch_brand_awareness(conn, granularity: str = 'day'):
    if granularity == 'month':
        date_format = '%Y-%m'
    elif granularity == 'week':
        date_format = '%Y-%W'
    else:
        date_format = '%Y-%m-%d'

    query = f"""
        SELECT
            strftime('{date_format}', first_order_date, '-5 hours') as date,
            COUNT(*) as new_customers
        FROM customers
        WHERE is_verified = 1
          AND first_order_date IS NOT NULL
        GROUP BY 1
        ORDER BY 1 ASC
    """
    return [dict(row) for row in conn.execute(query).fetchall()]
