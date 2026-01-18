import pandas as pd
from psycopg2.extras import RealDictCursor

def fetch_customer_reorder_rate(conn):
    """Fetch global customer reorder rate"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        WITH customer_stats AS (
            SELECT 
                total_orders,
                CASE WHEN total_orders > 1 THEN 1 ELSE 0 END as is_returning
            FROM customers c
            WHERE c.is_verified = TRUE
        )
        SELECT 
            COUNT(*) as total_customers,
            SUM(is_returning) as returning_customers,
            (CAST(SUM(is_returning) AS FLOAT) / NULLIF(COUNT(*), 0)) * 100 as reorder_rate
        FROM customer_stats
    """)
    return cursor.fetchone()

def fetch_customer_loyalty(conn):
    """Fetch Customer Retention Analysis by Cohort/Month"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        WITH customer_ranks AS (
            -- 1. Identify verified orders and rank them by customer lifetime
            SELECT 
                o.customer_id,
                o.created_on,
                o.total,
                ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.created_on ASC) as o_rank
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success'
              AND c.is_verified = TRUE
        ),
        monthly_stats AS (
            -- 2. Aggregate raw sums in the CTE
            SELECT 
                TO_CHAR(created_on, 'Mon-YYYY') as month_label,
                DATE_TRUNC('month', created_on) as month_sort,
                
                COUNT(*) as total_orders,
                COUNT(*) FILTER (WHERE o_rank > 1) as repeat_orders,
                
                COUNT(DISTINCT customer_id) as total_uid,
                COUNT(DISTINCT customer_id) FILTER (WHERE o_rank > 1) as repeat_uid,
                
                SUM(total) as raw_revenue,
                SUM(total) FILTER (WHERE o_rank > 1) as raw_repeat_revenue
            FROM customer_ranks
            GROUP BY 1, 2
        )
        SELECT 
            month_label as "Month",
            repeat_orders as "Repeat Orders",
            total_orders as "Total Orders",
            ROUND(100.0 * repeat_orders / NULLIF(total_orders, 0), 2) as "Order Repeat%",
            repeat_uid as "Repeat Customer Count",
            total_uid as "Total Verified Customers",
            ROUND(100.0 * repeat_uid / NULLIF(total_uid, 0), 2) as "Repeat Customer %",
            ROUND(raw_repeat_revenue) as "Repeat Revenue",
            ROUND(raw_revenue) as "Total Revenue",
            ROUND((100.0 * CAST(raw_repeat_revenue AS NUMERIC) / NULLIF(CAST(raw_revenue AS NUMERIC), 0)), 2) as "Revenue Repeat %",
            month_sort -- for sorting
        FROM monthly_stats
        ORDER BY month_sort DESC;
    """)
    return pd.DataFrame(cursor.fetchall())

def fetch_top_customers(conn):
    """Fetch Top Verified Customers by Spend"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        WITH customer_item_counts AS (
            -- Main Items
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
            
            -- Addons
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
            SELECT 
                customer_id,
                item_name,
                SUM(item_qty) as total_item_qty
            FROM customer_item_counts
            GROUP BY customer_id, item_name
        ),
        top_items_per_customer AS (
            SELECT DISTINCT ON (customer_id)
                customer_id,
                item_name,
                total_item_qty
            FROM final_counts
            ORDER BY customer_id, total_item_qty DESC, item_name ASC
        )
        SELECT 
            c.name,
            c.total_orders,
            c.total_spent,
            c.last_order_date,
            CASE WHEN c.total_orders > 1 THEN 'Returning' ELSE 'New' END as status,
            tic.item_name as favorite_item,
            tic.total_item_qty as fav_item_qty
        FROM customers c
        LEFT JOIN top_items_per_customer tic ON c.customer_id = tic.customer_id
        WHERE c.is_verified = TRUE
        ORDER BY c.total_spent DESC
        LIMIT 50
    """)
    return pd.DataFrame(cursor.fetchall())
