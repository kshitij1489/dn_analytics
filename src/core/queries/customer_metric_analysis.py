"""
Full analysis assemblers for customer analytics views.

Each function combines summary metrics with detail rows into a single response dict.
"""

from __future__ import annotations

from typing import Sequence

from src.core.queries.customer_metric_calculators import (
    calculate_customer_return_rate,
    calculate_customer_retention_rate,
    calculate_repeat_order_rate,
)
from src.core.queries.customer_metric_detail_rows import (
    build_customer_return_rate_detail_rows,
    build_customer_retention_rate_detail_rows,
    build_repeat_order_rate_detail_rows,
)
from src.core.queries.customer_metric_types import (
    CustomerMetricFilters,
    CustomerMetricOrder,
    normalize_order_sources,
    resolve_lookback_window,
)


def build_customer_return_rate_analysis(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> dict[str, object]:
    detail_rows = build_customer_return_rate_detail_rows(orders, filters)
    metric = calculate_customer_return_rate(orders, filters)
    resolved_order_sources = list(normalize_order_sources(filters.order_sources) or [])
    lookback_start_date, lookback_end_date = resolve_lookback_window(filters)

    returning_by_repeat_orders = sum(int(row["qualified_by_repeat_orders"]) for row in detail_rows)
    returning_from_lookback = sum(int(row["qualified_by_lookback"]) for row in detail_rows)
    returning_by_both_conditions = sum(
        1
        for row in detail_rows
        if int(row["qualified_by_repeat_orders"]) and int(row["qualified_by_lookback"])
    )

    return {
        "summary": {
            "evaluation_start_date": filters.evaluation_start_date,
            "evaluation_end_date": filters.evaluation_end_date,
            "lookback_start_date": lookback_start_date,
            "lookback_end_date": lookback_end_date,
            "lookback_days": filters.lookback_days,
            "min_orders_per_customer": filters.min_orders_per_customer,
            "order_sources": resolved_order_sources,
            "order_source_label": "All" if not resolved_order_sources else ", ".join(resolved_order_sources),
            "total_customers": metric["total_customers"],
            "returning_customers": metric["returning_customers"],
            "return_rate": metric["return_rate"],
            "new_customers": int(metric["total_customers"]) - int(metric["returning_customers"]),
            "returning_by_repeat_orders": returning_by_repeat_orders,
            "returning_from_lookback": returning_from_lookback,
            "returning_by_both_conditions": returning_by_both_conditions,
        },
        "rows": detail_rows,
    }


def build_customer_retention_rate_analysis(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> dict[str, object]:
    detail_rows = build_customer_retention_rate_detail_rows(orders, filters)
    metric = calculate_customer_retention_rate(orders, filters)
    resolved_order_sources = list(normalize_order_sources(filters.order_sources) or [])
    lookback_start_date, lookback_end_date = resolve_lookback_window(filters)

    return {
        "summary": {
            "evaluation_start_date": filters.evaluation_start_date,
            "evaluation_end_date": filters.evaluation_end_date,
            "lookback_start_date": lookback_start_date,
            "lookback_end_date": lookback_end_date,
            "lookback_days": filters.lookback_days,
            "min_orders_per_customer": filters.min_orders_per_customer,
            "order_sources": resolved_order_sources,
            "order_source_label": "All" if not resolved_order_sources else ", ".join(resolved_order_sources),
            "total_customers": metric["total_customers"],
            "prior_cohort_size": metric["total_customers"],
            "retained_customers": metric["retained_customers"],
            "retention_rate": metric["retention_rate"],
            "not_retained_customers": int(metric["total_customers"]) - int(metric["retained_customers"]),
        },
        "rows": detail_rows,
    }


def build_repeat_order_rate_analysis(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> dict[str, object]:
    detail_rows = build_repeat_order_rate_detail_rows(orders, filters)
    metric = calculate_repeat_order_rate(orders, filters)
    resolved_order_sources = list(normalize_order_sources(filters.order_sources) or [])

    return {
        "summary": {
            "evaluation_start_date": filters.evaluation_start_date,
            "evaluation_end_date": filters.evaluation_end_date,
            "min_orders_per_customer": filters.min_orders_per_customer,
            "order_sources": resolved_order_sources,
            "order_source_label": "All" if not resolved_order_sources else ", ".join(resolved_order_sources),
            "total_customers": metric["total_customers"],
            "repeat_order_customers": metric["repeat_order_customers"],
            "repeat_order_rate": metric["repeat_order_rate"],
            "single_order_customers": int(metric["total_customers"]) - int(metric["repeat_order_customers"]),
        },
        "rows": detail_rows,
    }
