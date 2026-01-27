import pandas as pd

def fetch_customer_reorder_rate(conn):
    """Fetch global customer reorder rate"""
    # SQLite logic: sum(case when ...)
    query = """
        WITH customer_stats AS (
            SELECT 
                total_orders,
                CASE WHEN total_orders > 1 THEN 1 ELSE 0 END as is_returning
            FROM customers c
            WHERE c.is_verified = 1
        )
        SELECT 
            COUNT(*) as total_customers,
            SUM(is_returning) as returning_customers,
            (CAST(SUM(is_returning) AS FLOAT) / NULLIF(COUNT(*), 0)) * 100 as reorder_rate
        FROM customer_stats
    """
    cursor = conn.execute(query)
    row = cursor.fetchone()
    # Convert Row object to dict or return None
    return dict(row) if row else None

def fetch_customer_loyalty(conn):
    """Fetch Customer Retention Analysis by Cohort/Month
    SQLite changes:
    - TO_CHAR -> strftime
    - DATE_TRUNC -> strftime
    - FILTER (WHERE ...) -> SUM(CASE WHEN ... THEN 1 ELSE 0 END)
    """
    query = """
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
              AND c.is_verified = 1
        ),
        monthly_stats AS (
            -- 2. Aggregate raw sums in the CTE
            SELECT 
                strftime('%Y-%m', created_on) as month_sort,
                
                COUNT(*) as total_orders,
                SUM(CASE WHEN o_rank > 1 THEN 1 ELSE 0 END) as repeat_orders,
                
                COUNT(DISTINCT customer_id) as total_uid,
                -- Approximate distinct repeat customers in SQLite within group by without advanced FILTER 
                -- We use a case when inside count distinct if supported, or subquery. 
                -- Simpler: COUNT(DISTINCT CASE WHEN o_rank > 1 THEN customer_id END)
                COUNT(DISTINCT CASE WHEN o_rank > 1 THEN customer_id END) as repeat_uid,
                
                SUM(total) as raw_revenue,
                SUM(CASE WHEN o_rank > 1 THEN total ELSE 0 END) as raw_repeat_revenue
            FROM customer_ranks
            GROUP BY 1
        )
        SELECT 
            month_sort as "Month", -- Keeping simplistic for sort
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
    """
    cursor = conn.execute(query)
    # pd.read_sql_query or convert fetchall
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_top_customers(conn):
    """Fetch Top Verified Customers by Spend"""
    # SQLite changes:
    # - DISTINCT ON -> Group By with Max/Min or Row Number
    # - RealDictCursor removed
    query = """
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
        ranked_items AS (
            SELECT 
                customer_id,
                item_name,
                total_item_qty,
                ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total_item_qty DESC, item_name ASC) as rn
            FROM final_counts
        )
        SELECT 
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
    cursor = conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])
