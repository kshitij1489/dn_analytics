import pandas as pd
from datetime import datetime, timedelta

# SQLite strftime('%w') = 0 Sunday, 1 Monday, ..., 6 Saturday
DAY_NAME_TO_SQLITE_DOW = {
    "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
    "Thursday": 4, "Friday": 5, "Saturday": 6,
}


def _weekdays_to_sqlite_dow(selected_weekdays):
    """Convert day names (e.g. from frontend) to SQLite %w values (0-6). Pass-through if already ints."""
    if not selected_weekdays:
        return None
    result = []
    for d in selected_weekdays:
        if isinstance(d, int) and 0 <= d <= 6:
            result.append(d)
        elif isinstance(d, str) and d in DAY_NAME_TO_SQLITE_DOW:
            result.append(DAY_NAME_TO_SQLITE_DOW[d])
    return result if result else None


def fetch_menu_stats(conn, name_search=None, type_choice="All", start_date=None, end_date=None, selected_weekdays=None):
    """Fetch Menu Analytics (Reorder stats, revenue, etc) with filtering"""
    
    # 1. Build order-level filters
    order_filter_sql = ""
    order_params = []
    
    if start_date:
        order_filter_sql += " AND o.created_on >= ?"
        # Business day starts at 5:00 AM
        order_params.append(f"{start_date} 05:00:00")
    if end_date:
        # Business day ends at 4:59:59 AM NEXT day
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_str = end_dt.strftime("%Y-%m-%d")
        order_filter_sql += " AND o.created_on <= ?"
        order_params.append(f"{end_str} 04:59:59")
    # Days filter: include only orders whose business-day weekday is in selected days (exclude unselected)
    dow_list = _weekdays_to_sqlite_dow(selected_weekdays) if selected_weekdays else None
    if dow_list is not None and len(dow_list) < 7:
        placeholders = ",".join("?" for _ in dow_list)
        # strftime('%w', ..., '-5 hours') = weekday in business-day terms (0=Sun .. 6=Sat)
        order_filter_sql += f" AND CAST(strftime('%w', o.created_on, '-5 hours') AS INTEGER) IN ({placeholders})"
        order_params.extend(dow_list)

    # 2. Build item-level filters
    item_filter_sql = ""
    item_params = []
    
    if name_search:
        item_filter_sql += " AND item_name LIKE ?"
        item_params.append(f"%{name_search}%")
    
    if type_choice and type_choice != "All":
        item_filter_sql += " AND item_type = ?"
        item_params.append(type_choice)

    # Combined params
    all_params = order_params + item_params

    # SQLite compatible query using GROUP BY instead of DISTINCT ON
    menu_query = f"""
        WITH dedup_items AS (
            -- 1. Deduplicate Items GLOBALLY using MAX/GROUP BY trick or ROW_NUMBER
            SELECT 
                order_item_id, menu_item_id, total_price, quantity, order_id, variant_id
            FROM (
                SELECT 
                    oi.*, 
                    ROW_NUMBER() OVER(PARTITION BY oi.order_id, oi.name_raw, oi.quantity, oi.unit_price ORDER BY oi.order_item_id) as rn
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.order_id
                WHERE o.order_status = 'Success'
            ) WHERE rn = 1
        ),
        dedup_addons AS (
             -- 1b. Deduplicate Addons
             SELECT 
                menu_item_id, total_price, quantity, order_id, order_item_id, variant_id
             FROM (
                SELECT 
                    oia.menu_item_id, (oia.price * oia.quantity) as total_price, oia.quantity, oi.order_id, oi.order_item_id, oia.variant_id,
                    ROW_NUMBER() OVER(PARTITION BY oia.order_item_id, oia.name_raw, oia.quantity, oia.price ORDER BY oia.order_item_addon_id) as rn
                FROM order_item_addons oia
                JOIN dedup_items oi ON oia.order_item_id = oi.order_item_id
             ) WHERE rn = 1
        ),
        global_item_history AS (
            -- 2. Combine & Rank Globally (with variant unit/value for aggregation)
            SELECT 
                mi.menu_item_id,
                mi.name AS item_name,
                mi.type AS item_type,
                o.customer_id,
                o.created_on,
                di.total_price AS item_revenue,
                di.quantity as sold_as_item_qty,
                0 as sold_as_addon_qty,
                COALESCE(UPPER(v.unit), '') as variant_unit,
                COALESCE(v.value, 0) * di.quantity as unit_amount,
                ROW_NUMBER() OVER (PARTITION BY o.customer_id, mi.menu_item_id ORDER BY o.created_on) as customer_item_rank
            FROM dedup_items di
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON di.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN variants v ON di.variant_id = v.variant_id
            
            UNION ALL
            
            SELECT 
                mi.menu_item_id,
                mi.name AS item_name,
                mi.type AS item_type,
                o.customer_id,
                o.created_on,
                da.total_price AS item_revenue,
                0 as sold_as_item_qty,
                da.quantity as sold_as_addon_qty,
                COALESCE(UPPER(v.unit), '') as variant_unit,
                COALESCE(v.value, 0) * da.quantity as unit_amount,
                ROW_NUMBER() OVER (PARTITION BY o.customer_id, mi.menu_item_id ORDER BY o.created_on) as customer_item_rank
            FROM dedup_addons da
            JOIN dedup_items di ON da.order_item_id = di.order_item_id
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON da.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN variants v ON da.variant_id = v.variant_id
        ),
        filtered_items AS (
            -- 3. Apply User Filters
            SELECT * 
            FROM global_item_history o
            WHERE 1=1 {order_filter_sql} {item_filter_sql}
        ),
        reorder_stats AS (
            -- 4. Aggregate
            SELECT 
                menu_item_id, item_name, item_type,
                SUM(sold_as_item_qty) as sold_as_item,
                SUM(sold_as_addon_qty) as sold_as_addon,
                
                SUM(CASE WHEN customer_item_rank > 1 THEN sold_as_item_qty + sold_as_addon_qty ELSE 0 END) AS qty_reordered,
                
                COUNT(DISTINCT CASE WHEN customer_item_rank > 1 THEN customer_id END) AS customers_who_reordered,
                
                COUNT(DISTINCT customer_id) AS total_unique_customers,
                (SUM(sold_as_item_qty) + SUM(sold_as_addon_qty)) AS total_qty_sold,
                COUNT(*) AS total_transactions,
                SUM(item_revenue) AS total_revenue,
                SUM(CASE WHEN customer_item_rank > 1 THEN item_revenue ELSE 0 END) AS repeat_customer_revenue,
                
                -- Unit-based aggregations: only sum value*qty where unit matches
                SUM(CASE WHEN variant_unit = 'GMS' THEN unit_amount ELSE 0 END) AS total_gms,
                SUM(CASE WHEN variant_unit = 'ML' THEN unit_amount ELSE 0 END) AS total_ml,
                SUM(CASE WHEN variant_unit = 'COUNT' THEN unit_amount ELSE 0 END) AS total_count
            FROM filtered_items
            GROUP BY menu_item_id, item_name, item_type
        )
        SELECT 
            item_name as "Item Name",
            item_type as "Type",
            sold_as_addon as "As Addon (Qty)",
            sold_as_item as "As Item (Qty)",
            total_qty_sold as "Total Sold (Qty)",
            total_revenue as "Total Revenue",
            total_gms as "Total GMS",
            total_ml as "Total ML",
            total_count as "Total COUNT",
            qty_reordered AS "Reorder Count", 
            customers_who_reordered AS "Repeat Customer (Lifetime)",
            total_unique_customers AS "Unique Customers",
            ROUND(100.0 * customers_who_reordered / NULLIF(total_unique_customers, 0), 2) AS "Reorder Rate %",
            ROUND(100.0 * repeat_customer_revenue / NULLIF(total_revenue, 0), 2) AS "Repeat Revenue %"
        FROM reorder_stats
        WHERE total_unique_customers > 0
        ORDER BY total_revenue DESC;
    """
    
    cursor = conn.execute(menu_query, all_params)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_menu_types(conn):
    """Fetch distinct menu item types"""
    cursor = conn.execute("SELECT DISTINCT type FROM menu_items ORDER BY type")
    # Fetch results
    rows = cursor.fetchall()
    # sqlite3.Row access by name 'type'
    return [row['type'] for row in rows]

def fetch_unverified_items(conn):
    """Fetch all unverified menu items with suggestions"""
    query = """
        SELECT 
            m.menu_item_id, m.name, m.type, m.created_at, m.suggestion_id,
            s.name as suggestion_name, s.type as suggestion_type
        FROM menu_items m
        LEFT JOIN menu_items s ON m.suggestion_id = s.menu_item_id
        WHERE m.is_verified = 0
        ORDER BY m.name
    """
    cursor = conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_menu_matrix(conn):
    """Fetch the full menu matrix"""
    query = """
        SELECT 
            mi.name, mi.type, v.variant_name, miv.price, miv.is_active, 
            miv.addon_eligible, miv.delivery_eligible, miv.menu_item_id, miv.variant_id
        FROM menu_item_variants miv
        JOIN menu_items mi ON miv.menu_item_id = mi.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        ORDER BY mi.type, mi.name, v.variant_name
    """
    cursor = conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])
