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


def fetch_reorder_rate_trend(conn, granularity='day', start_date=None, end_date=None, metric='orders'):
    """
    Fetch reorder rate trend over time.
    Granularity: 'day', 'week', 'month'
    Metric: 'orders' (Repeat Order Rate) or 'customers' (Repeat Customer Rate)
    """
    from src.core.utils.business_date import get_business_date_range

    # 1. Determine Date/Group Format
    if granularity == 'month':
        date_format = '%Y-%m-01'  # Group by Month Start
    elif granularity == 'week':
        # SQLite: Get start of week (Monday)
        # date(date_column, 'weekday 0', '-6 days')
        date_format = 'week' 
    else: # day
        date_format = '%Y-%m-%d'

    # 2. Date Filtering
    date_filter = ""
    params = []
    if start_date and end_date:
        start_dt, _ = get_business_date_range(start_date)
        _, end_dt = get_business_date_range(end_date)
        date_filter = " AND o.created_on >= ? AND o.created_on <= ?"
        params = [start_dt, end_dt]

    # 3. Group Column Logic
    group_col = f"strftime('{date_format}', o.created_on, '-5 hours')"
    if granularity == 'week':
        group_col = "date(date(o.created_on, '-5 hours'), 'weekday 0', '-6 days')"

    # 4. Query Logic
    # Repeat Definition:
    # A customer/order is "repeat" if there exists a PRIOR successful order 
    # for the same customer created before the current order's bucket start.
    # Actually, simplistic definition: created before the current order.
    # BUT for optimization in aggregation, we can just say:
    # "Is there an order with created_on < o.created_on?"
    
    # However, for "Repeat Customer Rate" (Metric 2), we need to check if the CUSTOMER 
    # had an order before the bucket start. 
    # For "Repeat Order Rate" (Metric 1), we check if the ORDER is from a customer who had prior orders.
    
    # Let's use a unified approach:
    # For every order in the window, flag if it's a "repeat order" (index 0 or 1).
    # Then aggregate.
    
    # optimization: 
    # is_repeat = CASE WHEN EXISTS (SELECT 1 FROM orders prev WHERE prev.customer_id = o.customer_id AND prev.created_on < o.created_on AND prev.order_status = 'Success') THEN 1 ELSE 0 END
    
    # METRIC 1: Repeat Order Rate
    # Denom: Total Orders in bucket
    # Num: Orders where is_repeat = 1
    
    # METRIC 2: Repeat Customer Rate
    # Denom: Count DISTINCT customer_id in bucket
    # Num: Count DISTINCT customer_id where (any order in bucket is a repeat OR checking min(created_on) < bucket_start)
    # Actually simpler: A customer is a "Repeat Customer" in this bucket if they had ANY successful order BEFORE the bucket started.
    # Wait, if a customer joins on Monday (Order 1) and orders again on Tuesday (Order 2).
    # In 'Week' view: They are a NEW customer for that week (if Order 1 was their first ever).
    # But Order 2 is a repeat order.
    # The user defined: "repeat_customer = customer whose first lifetime order date < window_start"
    # So for the bucket, we check if MIN(lifetime_created_on) < bucket_start.
    
    # To do this efficiently in one query without massive joins, we can use the customer's first_order_date 
    # derived state IF we trusted it, but we don't.
    # So we compute first_order_date on the fly? Expensive.
    
    # Alternative:
    # Use window function LAG or MIN over partition, but that requires scanning history.
    # Efficient approach:
    # Pre-calculate `min_created_on` for every verified customer in a CTE/Subquery?
    # Or just use the correlated subquery approach, which is strictly correct but slower.
    # Given dataset size (thousands), correlated subquery is fine.
    
    # Efficient Logic:
    # 1. Get all relevant orders in window.
    # 2. For each, check if `EXISTS (SELECT 1 FROM orders WHERE customer_id=... AND created_on < bucket_start)`
    # This aligns with "Repeat Customer" definition: Active in window AND had history before window.
    
    # Let's adjust slightly:
    # For "Repeat Orders": The order itself is a repeat if previous history exists (created_on < o.created_on).
    # For "Repeat Customers": The customer is repeat if they had history before bucket_start.
    
    # Since we are modifying the query to support both, we'll implement consistent logic.
    
    if metric == 'customers':
        # Metric 2: Repeat Customer Rate
        # Denom: Count of unique active customers in bucket.
        # Num: Count of unique active customers who had an order BEFORE the bucket start.
        
        # We need the bucket start date for the EXISTS check.
        # SQLite's group by returns the mapped date.
        
        query = f"""
            WITH active_in_window AS (
                SELECT 
                    {group_col} as bucket_date,
                    o.customer_id,
                    MIN(o.created_on) as first_seen_in_bucket -- Just to get a timestamp in bucket
                FROM orders o
                JOIN customers c ON o.customer_id = c.customer_id
                WHERE o.order_status = 'Success'
                  AND c.is_verified = 1
                  {date_filter}
                GROUP BY 1, 2
            )
            SELECT 
                bucket_date as date,
                COUNT(DISTINCT customer_id) as total_customers,
                
                -- Numerator: Count customers who had an order BEFORE bucket_date
                -- Note: bucket_date is string 'YYYY-MM-DD'. SQLite compares strings ISO8601 correctly.
                SUM(
                    CASE WHEN EXISTS (
                        SELECT 1 FROM orders prev 
                        WHERE prev.customer_id = active_in_window.customer_id 
                          AND prev.order_status = 'Success'
                          -- bucket_date is 'YYYY-MM-DD'.
                          -- We must append 05:00:00 to match business day start.
                          -- Otherwise orders between 00:00 and 05:00 (which belong to PREV day) 
                          -- would inherently satisfy < 'YYYY-MM-DD 00:00:00', but orders at 02:00 belong to prev day 
                          -- and should be "prior history" for the current day starting at 05:00.
                          -- Wait, if bucket is Oct 27 (starts Oct 27 05:00).
                          -- Order A at Oct 27 02:00 (Business Day Oct 26).
                          -- Comparison: '2023-10-27 02:00' < '2023-10-27' (Midnight). FALSE.
                          -- So Order A is NOT counted as prior history.
                          -- Comparison Fix: '2023-10-27 02:00' < '2023-10-27 05:00'. TRUE.
                          -- So Order A IS counted as prior history. Correct.
                          AND prev.created_on < (bucket_date || ' 05:00:00')
                    ) THEN 1 ELSE 0 END
                ) as returning_customers
            FROM active_in_window
            GROUP BY 1
            ORDER BY 1 ASC
        """
        
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            formatted_date = row[0]
            total = row[1]
            returning = row[2]
            rate = round((returning / total * 100), 2) if total > 0 else 0.0
            results.append({
                "date": formatted_date,
                "total_orders": total, # reuse key for frontend compat
                "reordered_orders": returning, # reuse key
                "value": rate,
                "metric_label": "Customers"
            })
        return results

    else:
        # Metric 1: Repeat Order Rate (Default)
        # Denom: Total orders
        # Num: Orders where customer had AT LEAST ONE prior order (created_on < current_order.created_on)
        
        query = f"""
            SELECT 
                {group_col} as date,
                COUNT(o.order_id) as total_orders,
                SUM(
                    CASE WHEN EXISTS (
                        SELECT 1 FROM orders prev
                        WHERE prev.customer_id = o.customer_id
                          AND prev.order_status = 'Success'
                          AND prev.created_on < o.created_on
                    ) THEN 1 ELSE 0 END
                ) as reordered_orders
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success'
              AND c.is_verified = 1
              {date_filter}
            GROUP BY 1
            ORDER BY 1 ASC
        """
        
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            total = row[1]
            reordered = row[2]
            rate = round((reordered / total * 100), 2) if total > 0 else 0.0
            
            # Simple Dictionary for dataframe conversion
            results.append({
                "date": row[0],
                "total_orders": total,
                "reordered_orders": reordered,
                "value": rate,
                "metric_label": "Orders"
            })
            
        return results

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
