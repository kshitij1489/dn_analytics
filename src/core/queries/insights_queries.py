import pandas as pd
from src.core.utils.business_date import (
    BUSINESS_DATE_SQL, 
    get_current_business_date, 
    get_business_date_range
)

def fetch_kpis(conn):
    """Fetch Top-level KPIs: Revenue, Orders, Avg Order, Total Customers"""
    
    # Get range for "today" (Business Date)
    today_str = get_current_business_date()
    start_dt, end_dt = get_business_date_range(today_str)
    
    query = f"""
        SELECT 
            COUNT(*) as total_orders,
            SUM(total) as total_revenue,
            AVG(total) as avg_order_value,
            (SELECT COUNT(*) FROM customers) as total_customers,
            (
                SELECT COALESCE(SUM(total), 0) 
                FROM orders 
                WHERE order_status = 'Success' 
                AND created_on >= ? AND created_on <= ?
            ) as today_revenue
        FROM orders
        WHERE order_status = 'Success'
    """
    cursor = conn.execute(query, (start_dt, end_dt))
    row = cursor.fetchone()
    return dict(row) if row else None

def fetch_daily_sales(conn):
    """Fetch Daily Sales Performance"""
    # SQLite: sum(case when ...), date(...)
    # Use business date grouping
    cursor = conn.execute(f"""
        SELECT 
            {BUSINESS_DATE_SQL} as order_date,
            SUM(total) as total_revenue,
            SUM(total - tax_total) as net_revenue,
            SUM(tax_total) as tax_collected,
            COUNT(*) as total_orders,
            SUM(CASE WHEN order_from = 'Home Website' THEN total ELSE 0 END) as "Website Revenue",
            SUM(CASE WHEN order_from = 'POS' THEN total ELSE 0 END) as "POS Revenue",
            SUM(CASE WHEN order_from = 'Swiggy' THEN total ELSE 0 END) as "Swiggy Revenue",
            SUM(CASE WHEN order_from = 'Zomato' THEN total ELSE 0 END) as "Zomato Revenue"
        FROM orders
        WHERE order_status = 'Success'
        GROUP BY 1
        ORDER BY order_date DESC
    """)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_sales_trend(conn):
    """Fetch daily sales trend data (Revenue & Orders)"""
    cursor = conn.execute(f"""
        SELECT 
            {BUSINESS_DATE_SQL} as date,
            SUM(total) as revenue,
            COUNT(*) as num_orders
        FROM orders
        WHERE order_status = 'Success'
        GROUP BY 1
        ORDER BY date
    """)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_category_trend(conn):
    """Fetch daily sales by category"""
    cursor = conn.execute(f"""
        SELECT 
            {BUSINESS_DATE_SQL} as date,
            mi.type as category,
            SUM(oi.total_price) as revenue
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
        WHERE o.order_status = 'Success'
        GROUP BY 1, mi.type
        ORDER BY date
    """)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_top_items_data(conn, start_date=None, end_date=None):
    """Fetch Top 10 Items by Quantity with Revenue Share. Optional date range = business days (5:00 AM–4:59:59 AM IST)."""
    date_filter = ""
    params = []
    if start_date and end_date:
        start_dt, _ = get_business_date_range(start_date)
        _, end_dt = get_business_date_range(end_date)
        date_filter = " AND created_on >= ? AND created_on <= ?"
        params = [start_dt, end_dt]

    # 1. Total Revenue (within date range if set)
    total_query = "SELECT SUM(total) FROM orders WHERE order_status = 'Success'" + date_filter
    cursor = conn.execute(total_query, params) if params else conn.execute(total_query)
    row = cursor.fetchone()
    total_revenue = row[0] if row and row[0] else 0.0

    # 2. Top Items (same date filter on orders); quantity and revenue from filtered set only
    orders_subquery = "SELECT order_id FROM orders WHERE order_status = 'Success'" + date_filter
    query = """
        WITH dedup_items AS (
            SELECT DISTINCT order_id, name_raw, quantity, total_price, menu_item_id, order_item_id
            FROM order_items
            WHERE order_id IN (""" + orders_subquery + """)
        ),
        dedup_addons AS (
             SELECT DISTINCT order_item_id, name_raw, quantity, price, menu_item_id
             FROM order_item_addons
             WHERE order_item_id IN (SELECT order_item_id FROM dedup_items)
        ),
        item_rev_combined AS (
            SELECT menu_item_id, SUM(total_price) as rev
            FROM dedup_items
            GROUP BY menu_item_id
            UNION ALL
            SELECT menu_item_id, SUM(price * quantity) as rev
            FROM dedup_addons
            GROUP BY menu_item_id
        ),
        item_qty_combined AS (
            SELECT menu_item_id, SUM(quantity) as qty
            FROM dedup_items
            GROUP BY menu_item_id
            UNION ALL
            SELECT menu_item_id, SUM(quantity) as qty
            FROM dedup_addons
            GROUP BY menu_item_id
        ),
        item_totals AS (
            SELECT menu_item_id, SUM(rev) as rev
            FROM item_rev_combined
            GROUP BY menu_item_id
        ),
        item_sold AS (
            SELECT menu_item_id, SUM(qty) as total_sold
            FROM item_qty_combined
            GROUP BY menu_item_id
        )
        SELECT mi.name, COALESCE(isold.total_sold, 0) as total_sold, it.rev as item_revenue
        FROM menu_items mi
        LEFT JOIN item_totals it ON mi.menu_item_id = it.menu_item_id
        LEFT JOIN item_sold isold ON mi.menu_item_id = isold.menu_item_id
        WHERE mi.is_active = 1
        ORDER BY total_sold DESC
        LIMIT 10
    """
    cursor = conn.execute(query, params) if params else conn.execute(query)
    df = pd.DataFrame([dict(row) for row in cursor.fetchall()])
    return df, total_revenue

def fetch_revenue_by_category_data(conn, start_date=None, end_date=None):
    """Fetch Revenue by Category with Share. Optional date range = business days (5:00 AM–4:59:59 AM IST)."""
    date_filter = ""
    params = []
    if start_date and end_date:
        start_dt, _ = get_business_date_range(start_date)
        _, end_dt = get_business_date_range(end_date)
        date_filter = " AND created_on >= ? AND created_on <= ?"
        params = [start_dt, end_dt]

    # 1. Total Revenue (within date range if set)
    total_query = "SELECT SUM(total) FROM orders WHERE order_status = 'Success'" + date_filter
    cursor = conn.execute(total_query, params) if params else conn.execute(total_query)
    row = cursor.fetchone()
    total_revenue = row[0] if row and row[0] else 0.0

    # 2. Category Revenue (same date filter on orders)
    orders_subquery = "SELECT order_id FROM orders WHERE order_status = 'Success'" + date_filter
    query = """
        WITH dedup_items AS (
             SELECT DISTINCT order_id, name_raw, quantity, total_price, menu_item_id, order_item_id
             FROM order_items
             WHERE order_id IN (""" + orders_subquery + """)
        ),
        item_rev_combined AS (
            SELECT menu_item_id, total_price as rev
            FROM dedup_items
            UNION ALL
            SELECT menu_item_id, (price * quantity) as rev
            FROM order_item_addons
            WHERE order_item_id IN (SELECT order_item_id FROM dedup_items)
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
    """
    cursor = conn.execute(query, params) if params else conn.execute(query)
    df = pd.DataFrame([dict(row) for row in cursor.fetchall()])
    return df, total_revenue

def fetch_hourly_revenue_data(conn, days=None, start_date=None, end_date=None):
    """Fetch Hourly Revenue distribution, optionally for a date range (business days 5am–4:59am)."""
    # Use weekday of BUSINESS date (DATE(created_on, '-5 hours')) so 5am–4:59am day is consistent
    # SQLite strftime('%w', date) = 0 Sun, 1 Mon, ..., 6 Sat
    day_filter = ""
    if days and len(days) < 7:
        day_str = ",".join([str(d) for d in days])
        day_filter = f"AND CAST(strftime('%w', DATE(created_on, '-5 hours')) AS INTEGER) IN ({day_str})"

    date_filter = ""
    params = []
    if start_date and end_date:
        start_dt, _ = get_business_date_range(start_date)
        _, end_dt = get_business_date_range(end_date)
        date_filter = " AND created_on >= ? AND created_on <= ?"
        params = [start_dt, end_dt, start_dt, end_dt]  # once per CTE

    query = f"""
        WITH total_days AS (
            SELECT COUNT(DISTINCT {BUSINESS_DATE_SQL}) as day_count
            FROM orders
            WHERE order_status = 'Success'
            {day_filter}
            {date_filter}
        ),
        hourly_stats AS (
            SELECT 
                CAST(strftime('%H', created_on) AS INTEGER) as hour_num,
                SUM(total) as revenue
            FROM orders
            WHERE order_status = 'Success'
            {day_filter}
            {date_filter}
            GROUP BY hour_num
        )
        SELECT 
            h.hour_num, 
            h.revenue,
            h.revenue / NULLIF(d.day_count, 0) as avg_revenue
        FROM hourly_stats h, total_days d
        ORDER BY CASE WHEN h.hour_num >= 5 THEN h.hour_num ELSE h.hour_num + 24 END
    """
    cursor = conn.execute(query, params) if params else conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_order_source_data(conn, start_date=None, end_date=None):
    """Fetch Order Source metrics. Optional date range = business days (5:00 AM–4:59:59 AM IST)."""
    date_filter = ""
    params = []
    if start_date and end_date:
        start_dt, _ = get_business_date_range(start_date)
        _, end_dt = get_business_date_range(end_date)
        date_filter = " AND created_on >= ? AND created_on <= ?"
        params = [start_dt, end_dt]

    query = """
        SELECT order_from, COUNT(*) as count, SUM(total) as revenue
        FROM orders
        WHERE order_status = 'Success'
        """ + date_filter + """
        GROUP BY order_from
        ORDER BY count DESC
    """
    cursor = conn.execute(query, params) if params else conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])


def fetch_hourly_revenue_by_date(conn, date_str: str):
    """Fetch Hourly Revenue for a specific date"""
    start_dt, end_dt = get_business_date_range(date_str)
    
    cursor = conn.execute("""
        SELECT 
            CAST(strftime('%H', created_on) AS INTEGER) as hour_num,
            SUM(total) as revenue
        FROM orders
        WHERE order_status = 'Success'
          AND created_on >= ? AND created_on <= ?
        GROUP BY 1
        ORDER BY CASE WHEN hour_num >= 5 THEN hour_num ELSE hour_num + 24 END
    """, (start_dt, end_dt))
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])


def fetch_avg_revenue_by_day(conn, start_date=None, end_date=None):
    """Fetch Average Revenue by Day of Week using Pandas"""
    # Reuse fetch_daily_sales which returns a DF
    df = fetch_daily_sales(conn)
    
    if df.empty:
        return pd.DataFrame(columns=['dow', 'day_name', 'value'])

    df['order_date'] = pd.to_datetime(df['order_date'])
    df = df.set_index('order_date').sort_index()

    # Define Range
    min_date = pd.to_datetime(start_date) if start_date else df.index.min()
    max_date = pd.to_datetime(end_date) if end_date else df.index.max()
    
    if pd.isna(min_date) or pd.isna(max_date):
         return pd.DataFrame(columns=['dow', 'day_name', 'value'])

    full_idx = pd.date_range(start=min_date, end=max_date, freq='D')
    df_filtered = df['total_revenue'].reindex(full_idx, fill_value=0).to_frame()
    
    df_filtered['dow'] = df_filtered.index.dayofweek # 0=Mon, 6=Sun
    days = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday', 
            4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
    df_filtered['day_name'] = df_filtered['dow'].map(days)
    
    result = df_filtered.groupby(['dow', 'day_name'])['total_revenue'].mean().reset_index()
    result.rename(columns={'total_revenue': 'value'}, inplace=True)
    result = result.sort_values('dow')
    
    return result

