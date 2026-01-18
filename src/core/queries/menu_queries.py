import pandas as pd
from psycopg2.extras import RealDictCursor
def fetch_menu_stats(conn, name_search=None, type_choice="All"):
    """Fetch Menu Analytics (Reorder stats, revenue, etc)"""
    
    filter_sql = ""
    query_params = []
    
    if name_search:
        filter_sql += " AND item_name ILIKE %s"
        query_params.append(f"%{name_search}%")
    
    if type_choice and type_choice != "All":
        filter_sql += " AND item_type = %s"
        query_params.append(type_choice)

    menu_query = f"""
        WITH dedup_items AS (
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
             SELECT DISTINCT ON (oia.order_item_id, oia.name_raw, oia.quantity, oia.price) 
                oia.menu_item_id, 
                (oia.price * oia.quantity) as total_price,
                oia.quantity,
                oi.order_id,
                oi.order_item_id
            FROM order_item_addons oia
            JOIN dedup_items oi ON oia.order_item_id = oi.order_item_id
        ),
        customer_item_orders AS (
            -- Base items (ALL confirmed statuses, NO user filter)
            SELECT 
                mi.menu_item_id,
                mi.name AS item_name,
                mi.type AS item_type,
                o.customer_id,
                COUNT(DISTINCT o.order_id) AS order_occurrence_count,
                SUM(di.total_price) AS item_revenue,
                SUM(di.quantity) as sold_as_item_qty,
                0 as sold_as_addon_qty
            FROM dedup_items di
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON di.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success' 
            GROUP BY mi.menu_item_id, mi.name, mi.type, o.customer_id
            
            UNION ALL
            
            -- Addons (confirmed users)
            SELECT 
                mi.menu_item_id,
                mi.name AS item_name,
                mi.type AS item_type,
                o.customer_id,
                COUNT(DISTINCT o.order_id) AS order_occurrence_count,
                SUM(da.total_price) AS item_revenue,
                0 as sold_as_item_qty,
                SUM(da.quantity) as sold_as_addon_qty
            FROM dedup_addons da
            JOIN dedup_items di ON da.order_item_id = di.order_item_id
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON da.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success' 
            GROUP BY mi.menu_item_id, mi.name, mi.type, o.customer_id
        ),
        aggregated_customer_item AS (
            SELECT 
                menu_item_id, item_name, item_type, customer_id,
                SUM(order_occurrence_count) as total_order_occurrences,
                SUM(item_revenue) as total_item_revenue,
                SUM(sold_as_item_qty) as total_sold_as_item,
                SUM(sold_as_addon_qty) as total_sold_as_addon
            FROM customer_item_orders
            GROUP BY menu_item_id, item_name, item_type, customer_id
        ),
        reorder_stats AS (
            SELECT 
                menu_item_id, item_name, item_type,
                SUM(total_sold_as_item) as sold_as_item,
                SUM(total_sold_as_addon) as sold_as_addon,
                SUM(total_order_occurrences - 1) FILTER (WHERE total_order_occurrences > 1) AS total_reorders,
                COUNT(*) FILTER (WHERE total_order_occurrences > 1) AS customers_who_reordered,
                COUNT(*) AS total_unique_customers,
                (SUM(total_sold_as_item) + SUM(total_sold_as_addon)) AS total_qty_sold,
                SUM(total_order_occurrences) AS total_orders,
                SUM(total_item_revenue) AS total_revenue,
                SUM(total_item_revenue) FILTER (WHERE total_order_occurrences > 1) AS repeat_customer_revenue
            FROM aggregated_customer_item
            WHERE 1=1 {filter_sql}
            GROUP BY menu_item_id, item_name, item_type
        )
        SELECT 
            item_name as "Item Name",
            item_type as "Type",
            sold_as_addon as "As Addon (Qty)",
            sold_as_item as "As Item (Qty)",
            total_qty_sold as "Total Sold (Qty)",
            total_revenue as "Total Revenue",
            total_reorders AS "Reorder Count",
            customers_who_reordered AS "Repeat Customers",
            total_unique_customers AS "Unique Customers",
            ROUND(100.0 * customers_who_reordered / NULLIF(total_unique_customers, 0), 2) AS "Reorder Rate %%",
            ROUND(100.0 * repeat_customer_revenue / NULLIF(total_revenue, 0), 2) AS "Repeat Revenue %%"
        FROM reorder_stats
        WHERE total_unique_customers > 0
        ORDER BY total_revenue DESC;
    """
    
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute(menu_query, query_params)
    return pd.DataFrame(cursor.fetchall())

def fetch_menu_types(conn):
    """Fetch distinct menu item types"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT DISTINCT type FROM menu_items ORDER BY type")
    return [t['type'] for t in cursor.fetchall()]
