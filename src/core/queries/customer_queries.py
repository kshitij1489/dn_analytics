import pandas as pd
from src.core.utils.business_date import BUSINESS_DATE_SQL

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
                strftime('%Y-%m', created_on, '-5 hours') as month_sort,
                
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
    cursor = conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_brand_awareness(conn, granularity: str = 'day'):
    """
    Fetch new verified customers count grouped by first_order_date.
    Granularity: 'day', 'week', 'month'
    """
    # SQLite date formatting
    if granularity == 'month':
        date_format = '%Y-%m'
    elif granularity == 'week':
        # %W returns week number (00-53)
        date_format = '%Y-%W'
    else: # day
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
    
    cursor = conn.execute(query)
    rows = [dict(row) for row in cursor.fetchall()]
    return rows


def search_customers(conn, query_str: str, limit: int = 20):
    """
    Search customers by name or phone.
    Returns list of dicts.
    """
    # SQLite LIKE is case-insensitive for ASCII by default, but good to be explicit with UPPER if needed
    # CAST(phone AS TEXT) handles numeric phone storage if any
    sql = """
        SELECT 
            customer_id,
            name,
            phone,
            total_spent,
            last_order_date
        FROM customers
        WHERE 
            name LIKE ? 
            OR CAST(phone AS TEXT) LIKE ?
            OR CAST(customer_id AS TEXT) LIKE ?
        ORDER BY last_order_date DESC
        LIMIT ?
    """
    search_term = f"%{query_str}%"
    cursor = conn.execute(sql, (search_term, search_term, search_term, limit))
    results = [dict(row) for row in cursor.fetchall()]
    # Fix: Ensure customer_id is a string for Pydantic validation
    for r in results:
        r['customer_id'] = str(r['customer_id'])
    return results


def fetch_customer_profile_data(conn, customer_id: str):
    """
    Fetch customer details and their order history with item summaries.
    """
    # 1. Get Customer Details
    cust_sql = """
        SELECT 
            customer_id,
            name,
            phone,
            total_spent,
            last_order_date,
            is_verified
        FROM customers
        WHERE customer_id = ?
    """
    cursor = conn.execute(cust_sql, (customer_id,))
    row = cursor.fetchone()
    if not row:
        return None, None
        
    customer = dict(row)
    customer['customer_id'] = str(customer['customer_id'])
    customer['is_verified'] = bool(customer['is_verified'])

    # 2. Get Orders with Item Summary
    # We need to aggregate items per order. 
    # SQLite group_concat is useful here.
    orders_sql = """
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
    """
    
    cursor = conn.execute(orders_sql, (customer_id, customer_id))
    orders = [dict(row) for row in cursor.fetchall()]
    
    # SQLite booleans are 0/1, and IDs might be ints
    for o in orders:
        o['order_id'] = str(o['order_id'])
        o['order_number'] = str(o['order_number'])
        o['is_verified'] = bool(o['is_verified'])

    return customer, orders
