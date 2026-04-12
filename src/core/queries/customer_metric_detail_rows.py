"""
Detail-row builders for customer analytics tables.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from src.core.queries.customer_metric_fetchers import (
    build_customer_window_activity,
    count_orders_by_customer,
)
from src.core.queries.customer_metric_types import (
    CustomerMetricFilters,
    CustomerMetricOrder,
    cast_decimal,
    resolve_lookback_window,
    round_half_up,
)


def build_customer_return_rate_detail_rows(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> list[dict[str, object]]:
    lookback_start_date, lookback_end_date = resolve_lookback_window(filters)
    evaluation_activity = build_customer_window_activity(
        orders,
        filters.evaluation_start_date,
        filters.evaluation_end_date,
    )
    lookback_counts = count_orders_by_customer(orders, lookback_start_date, lookback_end_date)

    rows: list[dict[str, object]] = []
    for customer_id, evaluation_row in evaluation_activity.items():
        evaluation_order_count = int(evaluation_row["order_count"])
        lookback_order_count = int(lookback_counts.get(customer_id, 0))
        qualified_by_repeat_orders = 1 if evaluation_order_count >= filters.min_orders_per_customer else 0
        qualified_by_lookback = 1 if lookback_order_count > 0 else 0
        returning_flag = 1 if qualified_by_repeat_orders or qualified_by_lookback else 0

        if qualified_by_repeat_orders and qualified_by_lookback:
            return_reason = "Repeat in evaluation window and ordered in lookback window"
        elif qualified_by_repeat_orders:
            return_reason = "Repeat in evaluation window"
        elif qualified_by_lookback:
            return_reason = "Ordered in lookback window"
        else:
            return_reason = "First-time within selected windows"

        rows.append(
            {
                "customer_id": evaluation_row["customer_id"],
                "customer_name": evaluation_row["customer_name"],
                "evaluation_order_count": evaluation_order_count,
                "lookback_order_count": lookback_order_count,
                "evaluation_total_spend": round_half_up(cast_decimal(evaluation_row["total_spend"]), 2),
                "first_order_date": evaluation_row["first_order_date"],
                "last_order_date": evaluation_row["last_order_date"],
                "qualified_by_repeat_orders": qualified_by_repeat_orders,
                "qualified_by_lookback": qualified_by_lookback,
                "returning_flag": returning_flag,
                "returning_status": "Returning" if returning_flag else "New",
                "return_reason": return_reason,
            }
        )

    rows.sort(
        key=lambda row: (
            -int(row["returning_flag"]),
            -int(row["evaluation_order_count"]),
            -float(row["evaluation_total_spend"]),
            str(row["customer_name"]).lower(),
        )
    )
    return rows


def build_customer_retention_rate_detail_rows(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> list[dict[str, object]]:
    lookback_start_date, lookback_end_date = resolve_lookback_window(filters)
    lookback_activity = build_customer_window_activity(orders, lookback_start_date, lookback_end_date)
    evaluation_activity = build_customer_window_activity(
        orders,
        filters.evaluation_start_date,
        filters.evaluation_end_date,
    )

    rows: list[dict[str, object]] = []
    for customer_id, lookback_row in lookback_activity.items():
        evaluation_row = evaluation_activity.get(customer_id)
        evaluation_order_count = int(evaluation_row["order_count"]) if evaluation_row else 0
        retained_flag = 1 if evaluation_order_count >= filters.min_orders_per_customer else 0

        if evaluation_order_count == 0:
            retention_reason = "No orders in evaluation window"
        elif retained_flag:
            retention_reason = f"Met the {filters.min_orders_per_customer}+ order threshold"
        else:
            retention_reason = "Below the selected evaluation-window threshold"

        rows.append(
            {
                "customer_id": lookback_row["customer_id"],
                "customer_name": lookback_row["customer_name"],
                "lookback_order_count": int(lookback_row["order_count"]),
                "evaluation_order_count": evaluation_order_count,
                "evaluation_total_spend": round_half_up(
                    cast_decimal(evaluation_row["total_spend"]) if evaluation_row else Decimal("0"),
                    2,
                ),
                "first_evaluation_order_date": evaluation_row["first_order_date"] if evaluation_row else None,
                "last_evaluation_order_date": evaluation_row["last_order_date"] if evaluation_row else None,
                "retained_flag": retained_flag,
                "retention_status": "Retained" if retained_flag else "Not Retained",
                "retention_reason": retention_reason,
            }
        )

    rows.sort(
        key=lambda row: (
            -int(row["retained_flag"]),
            -int(row["evaluation_order_count"]),
            -float(row["evaluation_total_spend"]),
            str(row["customer_name"]).lower(),
        )
    )
    return rows


def build_repeat_order_rate_detail_rows(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> list[dict[str, object]]:
    evaluation_activity = build_customer_window_activity(
        orders,
        filters.evaluation_start_date,
        filters.evaluation_end_date,
    )

    rows: list[dict[str, object]] = []
    for evaluation_row in evaluation_activity.values():
        evaluation_order_count = int(evaluation_row["order_count"])
        repeat_order_flag = 1 if evaluation_order_count >= filters.min_orders_per_customer else 0
        repeat_order_reason = (
            f"Met the {filters.min_orders_per_customer}+ order threshold"
            if repeat_order_flag
            else "Below the selected repeat-order threshold"
        )
        rows.append(
            {
                "customer_id": evaluation_row["customer_id"],
                "customer_name": evaluation_row["customer_name"],
                "evaluation_order_count": evaluation_order_count,
                "evaluation_total_spend": round_half_up(cast_decimal(evaluation_row["total_spend"]), 2),
                "first_order_date": evaluation_row["first_order_date"],
                "last_order_date": evaluation_row["last_order_date"],
                "repeat_order_flag": repeat_order_flag,
                "repeat_order_status": "Repeat" if repeat_order_flag else "Single Order",
                "repeat_order_reason": repeat_order_reason,
            }
        )

    rows.sort(
        key=lambda row: (
            -int(row["repeat_order_flag"]),
            -int(row["evaluation_order_count"]),
            -float(row["evaluation_total_spend"]),
            str(row["customer_name"]).lower(),
        )
    )
    return rows
