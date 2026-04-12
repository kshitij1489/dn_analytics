"""
Core rate calculators for customer return, retention, and repeat-order metrics.
"""

from __future__ import annotations

from typing import Sequence

from src.core.queries.customer_metric_fetchers import (
    build_customer_window_activity,
    count_orders_by_customer,
)
from src.core.queries.customer_metric_types import (
    CustomerMetricFilters,
    CustomerMetricOrder,
    cast_decimal,
    normalize_order_sources,
    percentage,
    resolve_lookback_window,
    round_half_up,
)


def calculate_customer_return_rate(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> dict[str, float | int]:
    evaluation_counts = count_orders_by_customer(
        orders,
        filters.evaluation_start_date,
        filters.evaluation_end_date,
    )
    lookback_start_date, lookback_end_date = resolve_lookback_window(filters)
    lookback_customers = set(
        count_orders_by_customer(
            orders,
            lookback_start_date,
            lookback_end_date,
        )
    )

    returning_customers = sum(
        1
        for customer_id, order_count in evaluation_counts.items()
        if order_count >= filters.min_orders_per_customer or customer_id in lookback_customers
    )
    total_customers = len(evaluation_counts)

    return {
        "returning_customers": returning_customers,
        "total_customers": total_customers,
        "return_rate": percentage(returning_customers, total_customers),
    }


def calculate_customer_retention_rate(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> dict[str, float | int]:
    evaluation_counts = count_orders_by_customer(
        orders,
        filters.evaluation_start_date,
        filters.evaluation_end_date,
    )
    previous_customers = set(
        count_orders_by_customer(
            orders,
            *resolve_lookback_window(filters),
        )
    )
    retained_customers = sum(
        1
        for customer_id in previous_customers
        if evaluation_counts.get(customer_id, 0) >= filters.min_orders_per_customer
    )
    total_customers = len(previous_customers)

    return {
        "retained_customers": retained_customers,
        "total_customers": total_customers,
        "retention_rate": percentage(retained_customers, total_customers),
    }


def calculate_repeat_order_rate(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> dict[str, float | int]:
    evaluation_counts = count_orders_by_customer(
        orders,
        filters.evaluation_start_date,
        filters.evaluation_end_date,
    )
    repeat_order_customers = sum(
        1 for order_count in evaluation_counts.values()
        if order_count >= filters.min_orders_per_customer
    )
    total_customers = len(evaluation_counts)

    return {
        "repeat_order_customers": repeat_order_customers,
        "total_customers": total_customers,
        "repeat_order_rate": percentage(repeat_order_customers, total_customers),
    }
