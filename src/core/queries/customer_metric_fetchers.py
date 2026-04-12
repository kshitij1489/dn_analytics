"""
Database queries for fetching customer metric order data.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Sequence

from src.core.queries.customer_metric_types import (
    BUSINESS_DAY_OFFSET_HOURS,
    CustomerMetricOrder,
    cast_decimal,
    normalize_order_sources,
)
from src.core.utils.business_date import get_business_date_range


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


def build_customer_window_activity(
    orders: Sequence[CustomerMetricOrder],
    start_date: str | None,
    end_date: str | None,
) -> dict[int, dict[str, object]]:
    customer_rows: dict[int, dict[str, object]] = {}

    for order in orders:
        if start_date and order.business_date < start_date:
            continue
        if end_date and order.business_date > end_date:
            continue

        row = customer_rows.setdefault(
            order.customer_id,
            {
                "customer_id": order.customer_id,
                "customer_name": order.customer_name or f"Customer {order.customer_id}",
                "order_count": 0,
                "total_spend": Decimal("0"),
                "first_order_date": order.business_date,
                "last_order_date": order.business_date,
            },
        )
        row["order_count"] = int(row["order_count"]) + 1
        row["total_spend"] = cast_decimal(row["total_spend"]) + Decimal(str(order.total))
        if order.business_date < str(row["first_order_date"]):
            row["first_order_date"] = order.business_date
        if order.business_date > str(row["last_order_date"]):
            row["last_order_date"] = order.business_date

    return customer_rows


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
