"""
Shared helpers for customer return, retention, and repeat-order metrics.

Re-export facade: keeps existing import paths stable while delegating
to focused sub-modules.
"""

# Types and utilities
from src.core.queries.customer_metric_types import (  # noqa: F401
    BUSINESS_DAY_OFFSET_HOURS,
    CustomerMetricFilters,
    CustomerMetricOrder,
    cast_customer_ids,
    cast_decimal,
    month_bounds,
    normalize_order_sources,
    percentage,
    percentage_decimal,
    resolve_lookback_window,
    round_half_up,
    shift_month,
)

# Data fetchers
from src.core.queries.customer_metric_fetchers import (  # noqa: F401
    build_customer_window_activity,
    count_orders_by_customer,
    fetch_customer_metric_orders,
    fetch_total_verified_customers,
)

# Core rate calculators
from src.core.queries.customer_metric_calculators import (  # noqa: F401
    calculate_customer_return_rate,
    calculate_customer_retention_rate,
    calculate_repeat_order_rate,
)

# Analysis builders (assembled responses)
from src.core.queries.customer_metric_analysis import (  # noqa: F401
    build_customer_return_rate_analysis,
    build_customer_retention_rate_analysis,
    build_repeat_order_rate_analysis,
)

# Detail row builders
from src.core.queries.customer_metric_detail_rows import (  # noqa: F401
    build_customer_return_rate_detail_rows,
    build_customer_retention_rate_detail_rows,
    build_repeat_order_rate_detail_rows,
)

# KPI builders (monthly summary, trailing KPI, quick view)
from src.core.queries.customer_metric_kpi_builders import (  # noqa: F401
    build_customer_quick_view_metrics,
    build_monthly_customer_metric_rows,
    build_trailing_customer_reorder_kpi,
)
