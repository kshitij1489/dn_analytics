import pandas as pd
from psycopg2.extras import RealDictCursor
def fetch_menu_stats(conn, name_search=None, type_choice="All", start_date=None, end_date=None, selected_weekdays=None):
    """Fetch Menu Analytics (Reorder stats, revenue, etc) with filtering"""
    
    # 1. Build order-level filters
    order_filter_sql = ""
    order_params = []
    
    if start_date:
        order_filter_sql += " AND o.created_on AT TIME ZONE 'Asia/Kolkata' >= %s"
        order_params.append(start_date)
    if end_date:
        # Include the full end day
        order_filter_sql += " AND o.created_on AT TIME ZONE 'Asia/Kolkata' <= %s"
        order_params.append(f"{end_date} 23:59:59")
    if selected_weekdays and len(selected_weekdays) < 7:
        order_filter_sql += " AND TRIM(TO_CHAR(o.created_on AT TIME ZONE 'Asia/Kolkata', 'Day')) = ANY(%s)"
        order_params.append(selected_weekdays)

    # 2. Build item-level filters
    item_filter_sql = ""
    item_params = []
    
    if name_search:
        item_filter_sql += " AND item_name ILIKE %s"
        item_params.append(f"%{name_search}%")
    
    if type_choice and type_choice != "All":
        item_filter_sql += " AND item_type = %s"
        item_params.append(type_choice)

    # Combined params: order params first (used in CTE), then item params (used in final stats)
    all_params = order_params + item_params

    menu_query = f"""
        WITH dedup_items AS (
            -- 1. Deduplicate Items GLOBALLY (No Date Filter yet)
            -- We need full history to determine if a customer is 'Repeat'
            SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                oi.order_item_id, 
                oi.menu_item_id, 
                oi.total_price,
                oi.quantity,
                oi.order_id
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.order_status = 'Success'
        ),
        dedup_addons AS (
             -- 1b. Deduplicate Addons GLOBALLY
             SELECT DISTINCT ON (oia.order_item_id, oia.name_raw, oia.quantity, oia.price) 
                oia.menu_item_id, 
                (oia.price * oia.quantity) as total_price,
                oia.quantity,
                oi.order_id,
                oi.order_item_id
            FROM order_item_addons oia
            JOIN dedup_items oi ON oia.order_item_id = oi.order_item_id
        ),
        global_item_history AS (
            -- 2. Combine & Rank Globally to establish 'Repeat' status
            SELECT 
                mi.menu_item_id,
                mi.name AS item_name,
                mi.type AS item_type,
                o.customer_id,
                o.created_on,
                di.total_price AS item_revenue,
                di.quantity as sold_as_item_qty,
                0 as sold_as_addon_qty,
                -- Rank this purchase for this customer+item tuple over time
                ROW_NUMBER() OVER (PARTITION BY o.customer_id, mi.menu_item_id ORDER BY o.created_on) as customer_item_rank
            FROM dedup_items di
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON di.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
            
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
                ROW_NUMBER() OVER (PARTITION BY o.customer_id, mi.menu_item_id ORDER BY o.created_on) as customer_item_rank
            FROM dedup_addons da
            JOIN dedup_items di ON da.order_item_id = di.order_item_id
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON da.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
        ),
        filtered_items AS (
            -- 3. Apply User Filters (Date, Day, Name, Type) to the RANKED data
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
                
                -- "Reorder Count": Total Quantity sold where Rank > 1
                SUM(sold_as_item_qty + sold_as_addon_qty) FILTER (WHERE customer_item_rank > 1) AS qty_reordered,
                
                -- "Repeat Customers": Distinct customers who bought with Rank > 1
                COUNT(DISTINCT customer_id) FILTER (WHERE customer_item_rank > 1) AS customers_who_reordered,
                
                COUNT(DISTINCT customer_id) AS total_unique_customers,
                (SUM(sold_as_item_qty) + SUM(sold_as_addon_qty)) AS total_qty_sold,
                COUNT(*) AS total_transactions, -- Or distinct order_id if available, but here we unwound orders. 
                SUM(item_revenue) AS total_revenue,
                SUM(item_revenue) FILTER (WHERE customer_item_rank > 1) AS repeat_customer_revenue
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
            qty_reordered AS "Reorder Count", -- Changed semantics to Qty Reordered based on prompt? Or keep Count events? 
                                              -- User asked "How many were reordered" -> usually Qty.
                                              -- "How many were ordered by repeat customers" -> Repeat Customer Revenue/Qty.
            customers_who_reordered AS "Repeat Customer (Lifetime)",
            total_unique_customers AS "Unique Customers",
            ROUND(100.0 * customers_who_reordered / NULLIF(total_unique_customers, 0), 2) AS "Reorder Rate %%",
            ROUND(100.0 * repeat_customer_revenue / NULLIF(total_revenue, 0), 2) AS "Repeat Revenue %%"
        FROM reorder_stats
        WHERE total_unique_customers > 0
        ORDER BY total_revenue DESC;
    """
    
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(menu_query, all_params)
    return pd.DataFrame(cursor.fetchall())

def fetch_menu_types(conn):
    """Fetch distinct menu item types"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT DISTINCT type FROM menu_items ORDER BY type")
    return [t['type'] for t in cursor.fetchall()]

def fetch_unverified_items(conn):
    """Fetch all unverified menu items with suggestions"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT 
            m.menu_item_id, m.name, m.type, m.created_at, m.suggestion_id,
            s.name as suggestion_name, s.type as suggestion_type
        FROM menu_items m
        LEFT JOIN menu_items s ON m.suggestion_id = s.menu_item_id
        WHERE m.is_verified = FALSE
        ORDER BY m.name
    """
    cursor.execute(query)
    return pd.DataFrame(cursor.fetchall()) 

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
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(query)
    return pd.DataFrame(cursor.fetchall())
