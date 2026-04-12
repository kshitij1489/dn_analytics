"""
KPI builders for monthly summary rows, trailing reorder rate, and customer quick-view.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as DateType, timedelta
from decimal import Decimal
from typing import Sequence

from src.core.queries.customer_metric_calculators import (
    calculate_customer_return_rate,
    calculate_customer_retention_rate,
    calculate_repeat_order_rate,
)
from src.core.queries.customer_metric_types import (
    CustomerMetricFilters,
    CustomerMetricOrder,
    cast_customer_ids,
    cast_decimal,
    month_bounds,
    percentage,
    percentage_decimal,
    round_half_up,
    shift_month,
)


def build_monthly_customer_metric_rows(
    orders: Sequence[CustomerMetricOrder],
) -> list[dict[str, object]]:
    monthly_stats: dict[str, dict[str, object]] = {}
    customer_ranks: dict[int, int] = defaultdict(int)

    for order in orders:
        customer_ranks[order.customer_id] += 1
        order_rank = customer_ranks[order.customer_id]

        month_stats = monthly_stats.setdefault(
            order.business_month,
            {
                "total_orders": 0,
                "repeat_orders": 0,
                "customer_ids": set(),
                "repeat_customer_ids": set(),
                "total_revenue": Decimal("0"),
                "repeat_revenue": Decimal("0"),
            },
        )

        month_stats["total_orders"] = int(month_stats["total_orders"]) + 1
        cast_customer_ids(month_stats["customer_ids"]).add(order.customer_id)
        month_stats["total_revenue"] = cast_decimal(month_stats["total_revenue"]) + Decimal(str(order.total))

        if order_rank > 1:
            month_stats["repeat_orders"] = int(month_stats["repeat_orders"]) + 1
            cast_customer_ids(month_stats["repeat_customer_ids"]).add(order.customer_id)
            month_stats["repeat_revenue"] = cast_decimal(month_stats["repeat_revenue"]) + Decimal(str(order.total))

    rows: list[dict[str, object]] = []
    for month_sort in sorted(monthly_stats.keys(), reverse=True):
        stats = monthly_stats[month_sort]
        total_orders = int(stats["total_orders"])
        repeat_orders = int(stats["repeat_orders"])
        total_customers = len(cast_customer_ids(stats["customer_ids"]))
        repeat_customers = len(cast_customer_ids(stats["repeat_customer_ids"]))
        total_revenue = cast_decimal(stats["total_revenue"])
        repeat_revenue = cast_decimal(stats["repeat_revenue"])

        rows.append(
            {
                "Month": month_sort,
                "Repeat Orders": repeat_orders,
                "Total Orders": total_orders,
                "Order Repeat%": percentage(repeat_orders, total_orders),
                "Repeat Customer Count": repeat_customers,
                "Total Verified Customers": total_customers,
                "Repeat Customer %": percentage(repeat_customers, total_customers),
                "Repeat Revenue": round_half_up(repeat_revenue, 0),
                "Total Revenue": round_half_up(total_revenue, 0),
                "Revenue Repeat %": percentage_decimal(repeat_revenue, total_revenue),
                "month_sort": month_sort,
            }
        )

    return rows


def build_trailing_customer_reorder_kpi(
    monthly_rows: Sequence[dict[str, object]],
    *,
    total_verified_customers: int,
    trailing_months: int = 3,
) -> dict[str, float | int]:
    recent_rows = list(monthly_rows[:trailing_months])
    if not recent_rows:
        return {
            "total_verified_customers": total_verified_customers,
            "total_customers": 0,
            "returning_customers": 0,
            "reorder_rate": 0.0,
        }

    total_customers_sum = sum(int(row["Total Verified Customers"]) for row in recent_rows)
    returning_customers_sum = sum(int(row["Repeat Customer Count"]) for row in recent_rows)

    return {
        "total_verified_customers": total_verified_customers,
        "total_customers": round_half_up(total_customers_sum / len(recent_rows), 0),
        "returning_customers": round_half_up(returning_customers_sum / len(recent_rows), 0),
        "reorder_rate": percentage(returning_customers_sum, total_customers_sum),
    }


def build_customer_quick_view_metrics(
    orders: Sequence[CustomerMetricOrder],
    *,
    current_business_date: str,
) -> dict[str, float | int | str]:
    current_month_start = DateType.fromisoformat(current_business_date).replace(day=1)
    previous_month_start = shift_month(current_month_start, -1)
    two_months_ago_start = shift_month(current_month_start, -2)

    cm_start, cm_end = month_bounds(current_month_start)
    pm_start, pm_end = month_bounds(previous_month_start)
    tm_start, _ = month_bounds(two_months_ago_start)
    lifetime_lb_end = (current_month_start - timedelta(days=1)).isoformat()

    def _f(**kw: object) -> CustomerMetricFilters:
        return CustomerMetricFilters(evaluation_start_date=cm_start, evaluation_end_date=cm_end, **kw)

    rr1 = calculate_customer_return_rate(orders, _f(lookback_start_date=pm_start, lookback_end_date=pm_end, min_orders_per_customer=2))
    rr2 = calculate_customer_return_rate(orders, _f(lookback_start_date=tm_start, lookback_end_date=pm_end, min_orders_per_customer=2))
    rr_lt = calculate_customer_return_rate(orders, _f(lookback_end_date=lifetime_lb_end, min_orders_per_customer=2))
    ret1 = calculate_customer_retention_rate(orders, _f(lookback_start_date=pm_start, lookback_end_date=pm_end, min_orders_per_customer=1))
    ret2 = calculate_customer_retention_rate(orders, _f(lookback_start_date=tm_start, lookback_end_date=pm_end, min_orders_per_customer=1))
    ror_cm = calculate_repeat_order_rate(orders, _f(min_orders_per_customer=2))
    ror_pm = calculate_repeat_order_rate(orders, CustomerMetricFilters(
        evaluation_start_date=pm_start, evaluation_end_date=pm_end, min_orders_per_customer=2,
    ))

    return {
        "current_month": current_month_start.strftime("%Y-%m"),
        "returning_current_month_customers_one_month": rr1["returning_customers"],
        "returning_current_month_customers_two_month": rr2["returning_customers"],
        "total_current_month_customers": rr1["total_customers"],
        "return_rate_one_month": rr1["return_rate"],
        "return_rate_two_month": rr2["return_rate"],
        "return_rate_lifetime": rr_lt["return_rate"],
        "retained_customers_one_month": ret1["retained_customers"],
        "total_previous_one_month_customers": ret1["total_customers"],
        "retention_rate_one_month": ret1["retention_rate"],
        "retained_customers_two_month": ret2["retained_customers"],
        "total_previous_two_month_customers": ret2["total_customers"],
        "retention_rate_two_month": ret2["retention_rate"],
        "repeat_order_rate_current_month": ror_cm["repeat_order_rate"],
        "repeat_order_rate_previous_month": ror_pm["repeat_order_rate"],
        "return_rate_current_month": rr2["return_rate"],
        "retention_rate_current_month": ret2["retention_rate"],
    }

