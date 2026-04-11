def fetch_customer_reorder_rate(conn):
    """Fetch trailing 3-month repeat customer KPI aligned with monthly retention."""
    query = """
        WITH customer_ranks AS (
            SELECT
                o.customer_id,
                o.created_on,
                ROW_NUMBER() OVER (
                    PARTITION BY o.customer_id
                    ORDER BY o.created_on ASC
                ) as o_rank
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success'
              AND c.is_verified = 1
        ),
        monthly_stats AS (
            SELECT
                strftime('%Y-%m', created_on, '-5 hours') as month_sort,
                COUNT(DISTINCT customer_id) as total_customers,
                COUNT(DISTINCT CASE WHEN o_rank > 1 THEN customer_id END) as returning_customers
            FROM customer_ranks
            GROUP BY 1
        ),
        last_three_months AS (
            SELECT total_customers, returning_customers
            FROM monthly_stats
            ORDER BY month_sort DESC
            LIMIT 3
        ),
        verified_customer_totals AS (
            SELECT COUNT(*) as total_verified_customers
            FROM customers
            WHERE is_verified = 1
        )
        SELECT
            COALESCE((SELECT total_verified_customers FROM verified_customer_totals), 0) as total_verified_customers,
            COALESCE(ROUND(AVG(total_customers)), 0) as total_customers,
            COALESCE(ROUND(AVG(returning_customers)), 0) as returning_customers,
            ROUND(
                100.0 * COALESCE(SUM(returning_customers), 0) / NULLIF(COALESCE(SUM(total_customers), 0), 0),
                2
            ) as reorder_rate
        FROM last_three_months
    """
    row = conn.execute(query).fetchone()
    return dict(row) if row else None
