from src.core.queries.customer_metric_helpers import (
    build_monthly_customer_metric_rows,
    build_trailing_customer_reorder_kpi,
)
from src.core.queries.customer_metric_sources import resolve_orders_source


def fetch_customer_reorder_rate(conn, *, orders_source=None):
    """Fetch trailing 3-month repeat customer KPI aligned with monthly retention."""
    source = resolve_orders_source(conn, orders_source)
    monthly_rows = build_monthly_customer_metric_rows(source.fetch())
    return build_trailing_customer_reorder_kpi(
        monthly_rows,
        total_verified_customers=source.count_verified_customers(),
    )
