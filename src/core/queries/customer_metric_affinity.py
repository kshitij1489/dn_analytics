"""
Customer affinity segmentation (Zomato-style): among customers who ordered in a period,
classify each by recency of their last order *strictly before* that period starts.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date as DateType
from decimal import Decimal
from typing import Sequence

from src.core.queries.customer_metric_types import CustomerMetricOrder, percentage, round_half_up


AFFINITY_RECENT_DAYS = 60
AFFINITY_DORMANT_DAYS = 365
AFFINITY_SEGMENT_ORDER = {"Repeat": 0, "Lapsed": 1, "New": 2}


def _affinity_reason(segment: str, prior_last: str | None, gap_days: int | None, recent_days: int, dormant_days: int) -> str:
    if segment == "New":
        if prior_last is None:
            return "No order before evaluation start (treated as new / re-acquired)."
        return f"Last prior order {prior_last}; {gap_days}d before evaluation start (≥{dormant_days}d)."
    if segment == "Repeat":
        return f"Last prior order {prior_last}; within {recent_days}d before evaluation start."
    return f"Last prior order {prior_last}; {gap_days}d before evaluation start ({recent_days + 1}–{dormant_days - 1}d)."


def _classify_segment(
    prior_last_iso: str | None,
    period_start_iso: str,
    *,
    recent_days: int,
    dormant_days: int,
) -> tuple[str, int | None]:
    period_start = DateType.fromisoformat(period_start_iso)
    if prior_last_iso is None:
        return "New", None
    prior_last = DateType.fromisoformat(prior_last_iso)
    gap_days = (period_start - prior_last).days
    if gap_days >= dormant_days:
        return "New", gap_days
    if gap_days <= recent_days:
        return "Repeat", gap_days
    return "Lapsed", gap_days


def _affinity_sort_key(row: dict[str, object]) -> tuple[int, int, float, str]:
    segment = str(row["affinity_segment"])
    return (
        AFFINITY_SEGMENT_ORDER.get(segment, len(AFFINITY_SEGMENT_ORDER)),
        -int(row["evaluation_order_count"]),
        -float(row["evaluation_total_spend"]),
        str(row["customer_name"]).lower(),
    )


def analyze_customer_affinity(
    orders: Sequence[CustomerMetricOrder],
    period_start_iso: str,
    period_end_iso: str,
    *,
    recent_days: int = AFFINITY_RECENT_DAYS,
    dormant_days: int = AFFINITY_DORMANT_DAYS,
    include_rows: bool = True,
    order_source_label: str = "All",
) -> dict[str, object]:
    """
    Customers with ≥1 order in [period_start_iso, period_end_iso] (business dates).

    Prior gap is measured from **last order strictly before** ``period_start_iso`` to
    the **evaluation start** calendar day (Zomato-style 60d / 365d buckets).
    """
    by_customer: dict[int, list[CustomerMetricOrder]] = defaultdict(list)
    for order in orders:
        by_customer[order.customer_id].append(order)

    new_n = repeat_n = lapsed_n = 0
    rows: list[dict[str, object]] = []

    for customer_id, cust_orders in by_customer.items():
        in_window = [o for o in cust_orders if period_start_iso <= o.business_date <= period_end_iso]
        if not in_window:
            continue

        prior_orders = [o for o in cust_orders if o.business_date < period_start_iso]
        prior_last_iso = max((o.business_date for o in prior_orders), default=None)
        segment, gap_days = _classify_segment(
            prior_last_iso,
            period_start_iso,
            recent_days=recent_days,
            dormant_days=dormant_days,
        )
        if segment == "New":
            new_n += 1
        elif segment == "Repeat":
            repeat_n += 1
        else:
            lapsed_n += 1

        if include_rows:
            eval_dates = [o.business_date for o in in_window]
            spend = sum(Decimal(str(o.total)) for o in in_window)
            name = next((o.customer_name for o in in_window if o.customer_name), f"Customer {customer_id}")
            rows.append(
                {
                    "customer_id": customer_id,
                    "customer_name": name or f"Customer {customer_id}",
                    "affinity_segment": segment,
                    "evaluation_order_count": len(in_window),
                    "evaluation_total_spend": round_half_up(spend, 2),
                    "first_order_date": min(eval_dates),
                    "last_order_date": max(eval_dates),
                    "prior_last_order_date": prior_last_iso,
                    "gap_days_before_eval": gap_days,
                    "affinity_reason": _affinity_reason(segment, prior_last_iso, gap_days, recent_days, dormant_days),
                }
            )

    rows.sort(key=_affinity_sort_key)
    total = new_n + repeat_n + lapsed_n
    summary = {
        "evaluation_start_date": period_start_iso,
        "evaluation_end_date": period_end_iso,
        "order_source_label": order_source_label,
        "recent_recency_days": recent_days,
        "dormant_recency_days": dormant_days,
        "total_customers": total,
        "new_customers": new_n,
        "repeat_customers": repeat_n,
        "lapsed_customers": lapsed_n,
        "new_pct": percentage(new_n, total),
        "repeat_pct": percentage(repeat_n, total),
        "lapsed_pct": percentage(lapsed_n, total),
    }
    return {"summary": summary, "rows": rows}
