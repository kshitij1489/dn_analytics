"""
Shared helpers for customer return, retention, and repeat-order metrics.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date as DateType, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from src.core.utils.business_date import get_business_date_range

BUSINESS_DAY_OFFSET_HOURS = 5


@dataclass(frozen=True)
class CustomerMetricFilters:
    evaluation_start_date: str | None = None
    evaluation_end_date: str | None = None
    lookback_start_date: str | None = None
    lookback_end_date: str | None = None
    lookback_days: int | None = None
    min_orders_per_customer: int = 2
    order_sources: tuple[str, ...] | None = None
    verified_only: bool = True
    order_status: str = "Success"

    def __post_init__(self) -> None:
        if bool(self.evaluation_start_date) != bool(self.evaluation_end_date):
            raise ValueError("evaluation_start_date and evaluation_end_date must be provided together.")
        if (
            self.evaluation_start_date and self.evaluation_end_date
            and self.evaluation_start_date > self.evaluation_end_date
        ):
            raise ValueError("evaluation_start_date cannot be after evaluation_end_date.")
        if (
            self.lookback_start_date and self.lookback_end_date
            and self.lookback_start_date > self.lookback_end_date
        ):
            raise ValueError("lookback_start_date cannot be after lookback_end_date.")
        if self.lookback_days is not None and self.lookback_days < 1:
            raise ValueError("lookback_days must be at least 1 when provided.")
        if self.min_orders_per_customer < 1:
            raise ValueError("min_orders_per_customer must be at least 1.")


@dataclass(frozen=True)
class CustomerMetricOrder:
    order_id: int
    customer_id: int
    customer_name: str | None
    created_on: str
    total: float
    order_from: str
    business_date: str
    business_month: str


def shift_month(month_start: DateType, months: int) -> DateType:
    total_months = (month_start.year * 12) + (month_start.month - 1) + months
    return DateType(total_months // 12, (total_months % 12) + 1, 1)


def month_bounds(month_start: DateType) -> tuple[str, str]:
    next_month_start = shift_month(month_start, 1)
    month_end = next_month_start - timedelta(days=1)
    return month_start.isoformat(), month_end.isoformat()


def normalize_order_sources(order_sources: Sequence[str] | None) -> tuple[str, ...] | None:
    if not order_sources:
        return None

    normalized = tuple(
        dict.fromkeys(
            source.strip() for source in order_sources
            if source and source.strip() and source.strip().lower() != "all"
        )
    )
    return normalized or None


def fetch_total_verified_customers(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) as total_verified_customers
        FROM customers
        WHERE is_verified = 1
        """
    ).fetchone()
    return int(row["total_verified_customers"]) if row else 0


def fetch_customer_metric_orders(
    conn,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    order_sources: Sequence[str] | None = None,
    verified_only: bool = True,
    order_status: str = "Success",
) -> list[CustomerMetricOrder]:
    clauses = [
        "o.customer_id IS NOT NULL",
    ]
    params: list[object] = []

    if order_status:
        clauses.append("o.order_status = ?")
        params.append(order_status)

    if verified_only:
        clauses.append("c.is_verified = 1")

    if start_date:
        start_dt, _ = get_business_date_range(start_date)
        clauses.append("o.created_on >= ?")
        params.append(start_dt)

    if end_date:
        _, end_dt = get_business_date_range(end_date)
        clauses.append("o.created_on <= ?")
        params.append(end_dt)

    sources = normalize_order_sources(order_sources)
    if sources:
        placeholders = ", ".join("?" for _ in sources)
        clauses.append(f"o.order_from IN ({placeholders})")
        params.extend(sources)

    query = f"""
        SELECT
            o.order_id,
            o.customer_id,
            c.name as customer_name,
            o.created_on,
            COALESCE(o.total, 0) as total,
            COALESCE(o.order_from, 'Unknown') as order_from
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        WHERE {" AND ".join(clauses)}
        ORDER BY o.customer_id ASC, o.created_on ASC, o.order_id ASC
    """
    rows = conn.execute(query, params).fetchall()
    return [_to_metric_order(row) for row in rows]


def resolve_lookback_window(filters: CustomerMetricFilters) -> tuple[str | None, str | None]:
    if filters.lookback_start_date or filters.lookback_end_date:
        return filters.lookback_start_date, filters.lookback_end_date

    if not filters.lookback_days or not filters.evaluation_start_date:
        return None, None

    evaluation_start = DateType.fromisoformat(filters.evaluation_start_date)
    lookback_end = evaluation_start - timedelta(days=1)
    lookback_start = lookback_end - timedelta(days=filters.lookback_days - 1)
    return lookback_start.isoformat(), lookback_end.isoformat()


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
    evaluation_customers = set(
        count_orders_by_customer(
            orders,
            filters.evaluation_start_date,
            filters.evaluation_end_date,
        )
    )
    lookback_start_date, lookback_end_date = resolve_lookback_window(filters)
    previous_customers = set(
        count_orders_by_customer(
            orders,
            lookback_start_date,
            lookback_end_date,
        )
    )
    retained_customers = len(previous_customers & evaluation_customers)
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

    current_month_start_date, current_month_end_date = month_bounds(current_month_start)
    previous_month_start_date, previous_month_end_date = month_bounds(previous_month_start)
    two_months_ago_start_date, _ = month_bounds(two_months_ago_start)
    lifetime_lookback_end_date = (current_month_start - timedelta(days=1)).isoformat()

    return_rate_one_month = calculate_customer_return_rate(
        orders,
        CustomerMetricFilters(
            evaluation_start_date=current_month_start_date,
            evaluation_end_date=current_month_end_date,
            lookback_start_date=previous_month_start_date,
            lookback_end_date=previous_month_end_date,
            min_orders_per_customer=2,
        ),
    )
    return_rate_two_month = calculate_customer_return_rate(
        orders,
        CustomerMetricFilters(
            evaluation_start_date=current_month_start_date,
            evaluation_end_date=current_month_end_date,
            lookback_start_date=two_months_ago_start_date,
            lookback_end_date=previous_month_end_date,
            min_orders_per_customer=2,
        ),
    )
    return_rate_lifetime = calculate_customer_return_rate(
        orders,
        CustomerMetricFilters(
            evaluation_start_date=current_month_start_date,
            evaluation_end_date=current_month_end_date,
            lookback_end_date=lifetime_lookback_end_date,
            min_orders_per_customer=2,
        ),
    )
    retention_rate_one_month = calculate_customer_retention_rate(
        orders,
        CustomerMetricFilters(
            evaluation_start_date=current_month_start_date,
            evaluation_end_date=current_month_end_date,
            lookback_start_date=previous_month_start_date,
            lookback_end_date=previous_month_end_date,
        ),
    )
    retention_rate_two_month = calculate_customer_retention_rate(
        orders,
        CustomerMetricFilters(
            evaluation_start_date=current_month_start_date,
            evaluation_end_date=current_month_end_date,
            lookback_start_date=two_months_ago_start_date,
            lookback_end_date=previous_month_end_date,
        ),
    )
    repeat_order_rate_current_month = calculate_repeat_order_rate(
        orders,
        CustomerMetricFilters(
            evaluation_start_date=current_month_start_date,
            evaluation_end_date=current_month_end_date,
            min_orders_per_customer=2,
        ),
    )
    repeat_order_rate_previous_month = calculate_repeat_order_rate(
        orders,
        CustomerMetricFilters(
            evaluation_start_date=previous_month_start_date,
            evaluation_end_date=previous_month_end_date,
            min_orders_per_customer=2,
        ),
    )

    return {
        "current_month": current_month_start.strftime("%Y-%m"),
        "returning_current_month_customers_one_month": return_rate_one_month["returning_customers"],
        "returning_current_month_customers_two_month": return_rate_two_month["returning_customers"],
        "total_current_month_customers": return_rate_one_month["total_customers"],
        "return_rate_one_month": return_rate_one_month["return_rate"],
        "return_rate_two_month": return_rate_two_month["return_rate"],
        "return_rate_lifetime": return_rate_lifetime["return_rate"],
        "retained_customers_one_month": retention_rate_one_month["retained_customers"],
        "total_previous_one_month_customers": retention_rate_one_month["total_customers"],
        "retention_rate_one_month": retention_rate_one_month["retention_rate"],
        "retained_customers_two_month": retention_rate_two_month["retained_customers"],
        "total_previous_two_month_customers": retention_rate_two_month["total_customers"],
        "retention_rate_two_month": retention_rate_two_month["retention_rate"],
        "repeat_order_rate_current_month": repeat_order_rate_current_month["repeat_order_rate"],
        "repeat_order_rate_previous_month": repeat_order_rate_previous_month["repeat_order_rate"],
        "return_rate_current_month": return_rate_two_month["return_rate"],
        "retention_rate_current_month": retention_rate_two_month["retention_rate"],
    }


def build_customer_return_rate_detail_rows(
    orders: Sequence[CustomerMetricOrder],
    filters: CustomerMetricFilters,
) -> list[dict[str, object]]:
    lookback_start_date, lookback_end_date = resolve_lookback_window(filters)
    lookback_counts = count_orders_by_customer(orders, lookback_start_date, lookback_end_date)
    customer_rows: dict[int, dict[str, object]] = {}

    for order in orders:
        if filters.evaluation_start_date and order.business_date < filters.evaluation_start_date:
            continue
        if filters.evaluation_end_date and order.business_date > filters.evaluation_end_date:
            continue

        row = customer_rows.setdefault(
            order.customer_id,
            {
                "customer_id": order.customer_id,
                "customer_name": order.customer_name or f"Customer {order.customer_id}",
                "evaluation_order_count": 0,
                "lookback_order_count": int(lookback_counts.get(order.customer_id, 0)),
                "evaluation_total_spend": Decimal("0"),
                "first_order_date": order.business_date,
                "last_order_date": order.business_date,
            },
        )
        row["evaluation_order_count"] = int(row["evaluation_order_count"]) + 1
        row["evaluation_total_spend"] = cast_decimal(row["evaluation_total_spend"]) + Decimal(str(order.total))
        if order.business_date < str(row["first_order_date"]):
            row["first_order_date"] = order.business_date
        if order.business_date > str(row["last_order_date"]):
            row["last_order_date"] = order.business_date

    rows: list[dict[str, object]] = []
    for row in customer_rows.values():
        evaluation_order_count = int(row["evaluation_order_count"])
        lookback_order_count = int(row["lookback_order_count"])
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
                "customer_id": row["customer_id"],
                "customer_name": row["customer_name"],
                "evaluation_order_count": evaluation_order_count,
                "lookback_order_count": lookback_order_count,
                "evaluation_total_spend": round_half_up(cast_decimal(row["evaluation_total_spend"]), 2),
                "first_order_date": row["first_order_date"],
                "last_order_date": row["last_order_date"],
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


def count_orders_by_customer(
    orders: Sequence[CustomerMetricOrder],
    start_date: str | None,
    end_date: str | None,
) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for order in orders:
        if start_date and order.business_date < start_date:
            continue
        if end_date and order.business_date > end_date:
            continue
        counts[order.customer_id] += 1
    return dict(counts)


def percentage(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return float(round_half_up((100.0 * numerator) / denominator, 2))


def percentage_decimal(numerator: Decimal, denominator: Decimal) -> float:
    if not denominator:
        return 0.0
    return float(round_half_up((Decimal("100.0") * numerator) / denominator, 2))


def round_half_up(value: int | float | Decimal, digits: int = 2) -> int | float:
    exponent = Decimal("1") if digits == 0 else Decimal(f"1e-{digits}")
    rounded = Decimal(str(value)).quantize(exponent, rounding=ROUND_HALF_UP)
    if digits == 0:
        return int(rounded)
    return float(rounded)


def cast_customer_ids(value: object) -> set[int]:
    return value if isinstance(value, set) else set()


def cast_decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal("0")


def _to_metric_order(row) -> CustomerMetricOrder:
    business_dt = datetime.fromisoformat(row["created_on"]) - timedelta(hours=BUSINESS_DAY_OFFSET_HOURS)
    return CustomerMetricOrder(
        order_id=int(row["order_id"]),
        customer_id=int(row["customer_id"]),
        customer_name=row["customer_name"],
        created_on=row["created_on"],
        total=float(row["total"] or 0),
        order_from=row["order_from"],
        business_date=business_dt.date().isoformat(),
        business_month=business_dt.strftime("%Y-%m"),
    )
