from src.core.utils.business_date import get_business_date_range


def _resolve_trend_grouping(granularity: str):
    if granularity == 'month':
        return '%Y-%m-01', "strftime('%Y-%m-01', o.created_on, '-5 hours')"
    if granularity == 'week':
        return 'week', "date(date(o.created_on, '-5 hours'), 'weekday 0', '-6 days')"
    return '%Y-%m-%d', "strftime('%Y-%m-%d', o.created_on, '-5 hours')"


def _resolve_date_filter(start_date=None, end_date=None):
    if not (start_date and end_date):
        return "", []

    start_dt, _ = get_business_date_range(start_date)
    _, end_dt = get_business_date_range(end_date)
    return " AND o.created_on >= ? AND o.created_on <= ?", [start_dt, end_dt]


def _format_trend_rows(rows, label: str):
    results = []
    for row in rows:
        total = row[1]
        repeat = row[2]
        rate = round((repeat / total * 100), 2) if total > 0 else 0.0
        results.append({
            "date": row[0],
            "total_orders": total,
            "reordered_orders": repeat,
            "value": rate,
            "metric_label": label,
        })
    return results


def _fetch_customer_repeat_trend(conn, group_col: str, date_filter: str, params):
    query = f"""
        WITH active_in_window AS (
            SELECT
                {group_col} as bucket_date,
                o.customer_id,
                MIN(o.created_on) as first_seen_in_bucket
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
            SUM(
                CASE WHEN EXISTS (
                    SELECT 1 FROM orders prev
                    WHERE prev.customer_id = active_in_window.customer_id
                      AND prev.order_status = 'Success'
                      AND prev.created_on < (bucket_date || ' 05:00:00')
                ) THEN 1 ELSE 0 END
            ) as returning_customers
        FROM active_in_window
        GROUP BY 1
        ORDER BY 1 ASC
    """
    rows = conn.execute(query, params).fetchall()
    return _format_trend_rows(rows, "Customers")


def _fetch_order_repeat_trend(conn, group_col: str, date_filter: str, params):
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
    rows = conn.execute(query, params).fetchall()
    return _format_trend_rows(rows, "Orders")


def fetch_reorder_rate_trend(conn, granularity='day', start_date=None, end_date=None, metric='orders'):
    """
    Fetch reorder rate trend over time.
    Granularity: 'day', 'week', 'month'
    Metric: 'orders' or 'customers'
    """
    _, group_col = _resolve_trend_grouping(granularity)
    date_filter, params = _resolve_date_filter(start_date, end_date)

    if metric == 'customers':
        return _fetch_customer_repeat_trend(conn, group_col, date_filter, params)
    return _fetch_order_repeat_trend(conn, group_col, date_filter, params)
