from src.core.queries.customer_metric_helpers import (
    build_monthly_customer_metric_rows,
    build_trailing_customer_reorder_kpi,
    fetch_customer_metric_orders,
    fetch_total_verified_customers,
)


def fetch_customer_reorder_rate(conn):
    """Fetch trailing 3-month repeat customer KPI aligned with monthly retention."""
    monthly_rows = build_monthly_customer_metric_rows(fetch_customer_metric_orders(conn))
    return build_trailing_customer_reorder_kpi(
        monthly_rows,
        total_verified_customers=fetch_total_verified_customers(conn),
    )
