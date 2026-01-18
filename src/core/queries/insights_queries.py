import pandas as pd
from psycopg2.extras import RealDictCursor

def fetch_kpis(conn):
    """Fetch Top-level KPIs: Revenue, Orders, Avg Order, Total Customers"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT 
            COUNT(*) as total_orders,
            SUM(total) as total_revenue,
            AVG(total) as avg_order_value,
            (SELECT COUNT(*) FROM customers) as total_customers
        FROM orders
    """)
    return cursor.fetchone()

def fetch_daily_sales(conn):
    """Fetch Daily Sales Performance"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT 
            DATE(created_on) as order_date,
            SUM(total) as total_revenue,
            SUM(total - tax_total) as net_revenue,
            SUM(tax_total) as tax_collected,
            COUNT(*) as total_orders,
            SUM(total) FILTER (WHERE order_from = 'Home Website') as "Website Revenue",
            SUM(total) FILTER (WHERE order_from = 'POS') as "POS Revenue",
            SUM(total) FILTER (WHERE order_from = 'Swiggy') as "Swiggy Revenue",
            SUM(total) FILTER (WHERE order_from = 'Zomato') as "Zomato Revenue"
        FROM orders
        WHERE order_status = 'Success'
        GROUP BY DATE(created_on)
        ORDER BY order_date DESC
    """)
    return pd.DataFrame(cursor.fetchall())

def fetch_sales_trend(conn):
    """Fetch daily sales trend data (Revenue & Orders)"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT 
            DATE(created_on) as date,
            SUM(total) as revenue,
            COUNT(*) as num_orders
        FROM orders
        WHERE order_status = 'Success'
        GROUP BY DATE(created_on)
        ORDER BY date
    """)
    return pd.DataFrame(cursor.fetchall())

def fetch_category_trend(conn):
    """Fetch daily sales by category"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT 
            DATE(o.created_on) as date,
            mi.type as category,
            SUM(oi.total_price) as revenue
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        WHERE o.order_status = 'Success'
        GROUP BY DATE(o.created_on), mi.type
        ORDER BY date
    """)
    return pd.DataFrame(cursor.fetchall())

def fetch_top_items_data(conn):
    """Fetch Top 10 Items by Quantity with Revenue Share"""
    # 1. Total Revenue
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        WITH dedup_items AS (
            SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                oi.order_item_id, oi.total_price
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.order_status = 'Success'
        ),
        dedup_addons AS (
            SELECT DISTINCT ON (oia.order_item_id, oia.name_raw, oia.quantity, oia.price) 
                oia.order_item_id, (oia.price * oia.quantity) as rev
            FROM order_item_addons oia
            JOIN dedup_items di ON oia.order_item_id = di.order_item_id
        ),
        item_rev AS (
            SELECT SUM(total_price) as rev FROM dedup_items
            UNION ALL
            SELECT SUM(rev) as rev FROM dedup_addons
        )
        SELECT SUM(rev) FROM item_rev
    """)
    total_revenue = float(cursor.fetchone()['sum'] or 0)

    # 2. Top Items
    cursor.execute("""
        WITH dedup_items AS (
            SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                oi.order_item_id, oi.menu_item_id, oi.total_price
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.order_status = 'Success'
        ),
        item_rev_combined AS (
            SELECT di.menu_item_id, SUM(di.total_price) as rev
            FROM dedup_items di
            GROUP BY di.menu_item_id
            UNION ALL
            SELECT oia.menu_item_id, SUM(oia.price * oia.quantity) as rev
            FROM order_item_addons oia
            JOIN dedup_items di ON oia.order_item_id = di.order_item_id
            GROUP BY oia.menu_item_id
        ),
        item_totals AS (
            SELECT menu_item_id, SUM(rev) as rev
            FROM item_rev_combined
            GROUP BY menu_item_id
        )
        SELECT mi.name, mi.total_sold, it.rev as item_revenue
        FROM menu_items mi
        LEFT JOIN item_totals it ON mi.menu_item_id = it.menu_item_id
        WHERE mi.is_active = TRUE
        ORDER BY mi.total_sold DESC 
        LIMIT 10
    """)
    df = pd.DataFrame(cursor.fetchall())
    return df, total_revenue

def fetch_revenue_by_category_data(conn):
    """Fetch Revenue by Category with Share"""
    # 1. Total Revenue (Reused logic for simplicity in SQL block scope)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        WITH dedup_items AS (
            SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                oi.order_item_id, oi.total_price
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.order_status = 'Success'
        ),
        dedup_addons AS (
            SELECT DISTINCT ON (oia.order_item_id, oia.name_raw, oia.quantity, oia.price) 
                oia.order_item_id, (oia.price * oia.quantity) as rev
            FROM order_item_addons oia
            JOIN dedup_items di ON oia.order_item_id = di.order_item_id
        ),
        item_rev AS (
            SELECT SUM(total_price) as rev FROM dedup_items
            UNION ALL
            SELECT SUM(rev) as rev FROM dedup_addons
        )
        SELECT SUM(rev) FROM item_rev
    """)
    total_revenue = float(cursor.fetchone()['sum'] or 0)

    # 2. Category Revenue
    cursor.execute("""
        WITH dedup_items AS (
            SELECT DISTINCT ON (oi.order_id, oi.name_raw, oi.quantity, oi.unit_price) 
                oi.order_item_id, oi.menu_item_id, oi.total_price
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            WHERE o.order_status = 'Success'
        ),
        item_rev_combined AS (
            SELECT di.menu_item_id, di.total_price as rev
            FROM dedup_items di
            UNION ALL
            SELECT oia.menu_item_id, (oia.price * oia.quantity) as rev
            FROM order_item_addons oia
            JOIN dedup_items di ON oia.order_item_id = di.order_item_id
        ),
        cat_rev AS (
            SELECT mi.type as category, SUM(irc.rev) as revenue
            FROM item_rev_combined irc
            JOIN menu_items mi ON irc.menu_item_id = mi.menu_item_id
            WHERE mi.type IS NOT NULL AND mi.type != ''
            GROUP BY mi.type
        )
        SELECT category, revenue
        FROM cat_rev
        ORDER BY revenue DESC
    """)
    df = pd.DataFrame(cursor.fetchall())
    return df, total_revenue

def fetch_hourly_revenue_data(conn):
    """Fetch Hourly Revenue distribution"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        WITH total_days AS (
            SELECT COUNT(DISTINCT DATE(occurred_at)) as day_count
            FROM orders
            WHERE order_status = 'Success'
        ),
        hourly_stats AS (
            SELECT 
                EXTRACT(HOUR FROM occurred_at) as hour_num,
                SUM(total) as revenue
            FROM orders
            WHERE order_status = 'Success'
            GROUP BY hour_num
        )
        SELECT 
            h.hour_num, 
            h.revenue,
            h.revenue / NULLIF(d.day_count, 0) as avg_revenue
        FROM hourly_stats h, total_days d
        ORDER BY CASE WHEN h.hour_num = 0 THEN 24 ELSE h.hour_num END
    """)
    return pd.DataFrame(cursor.fetchall())

def fetch_order_source_data(conn):
    """Fetch Order Source metrics"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("""
        SELECT order_from, COUNT(*) as count, SUM(total) as revenue
        FROM orders
        WHERE order_status = 'Success'
        GROUP BY order_from
        ORDER BY count DESC
    """)
    return pd.DataFrame(cursor.fetchall())
