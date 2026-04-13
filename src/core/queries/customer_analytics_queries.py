from __future__ import annotations

from datetime import date as DateType, timedelta

import pandas as pd

from src.core.queries.customer_metric_affinity import analyze_customer_affinity
from src.core.queries.customer_metric_calculators import (
    calculate_customer_retention_rate,
    calculate_customer_return_rate,
    calculate_repeat_order_rate,
)
from src.core.queries.customer_metric_helpers import (
    CustomerMetricFilters,
    build_customer_return_rate_analysis,
    build_customer_retention_rate_analysis,
    build_monthly_customer_metric_rows,
    build_repeat_order_rate_analysis,
    fetch_customer_metric_orders,
    month_bounds,
    normalize_order_sources,
    resolve_lookback_window,
    shift_month,
)
from src.core.utils.business_date import get_current_business_date


def fetch_customer_loyalty(conn):
    rows = build_monthly_customer_metric_rows(fetch_customer_metric_orders(conn))
    return pd.DataFrame(rows)


def _require_min_orders_at_least(metric_name: str, min_orders_per_customer: int, minimum: int = 2) -> None:
    if min_orders_per_customer < minimum:
        raise ValueError(f"{metric_name} requires min_orders_per_customer to be at least {minimum}.")


def fetch_customer_return_rate_analysis(
    conn,
    *,
    evaluation_start_date: str | None = None,
    evaluation_end_date: str | None = None,
    lookback_start_date: str | None = None,
    lookback_end_date: str | None = None,
    lookback_days: int | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,
):
    _require_min_orders_at_least("Customer return rate", min_orders_per_customer)
    filters = _build_customer_metric_filters(
        evaluation_start_date=evaluation_start_date,
        evaluation_end_date=evaluation_end_date,
        lookback_start_date=lookback_start_date,
        lookback_end_date=lookback_end_date,
        lookback_days=lookback_days,
        min_orders_per_customer=min_orders_per_customer,
        order_sources=order_sources,
        include_lookback=True,
    )
    return build_customer_return_rate_analysis(_fetch_metric_orders(conn, filters), filters)


def fetch_customer_retention_rate_analysis(
    conn,
    *,
    evaluation_start_date: str | None = None,
    evaluation_end_date: str | None = None,
    lookback_start_date: str | None = None,
    lookback_end_date: str | None = None,
    lookback_days: int | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,
):
    filters = _build_customer_metric_filters(
        evaluation_start_date=evaluation_start_date,
        evaluation_end_date=evaluation_end_date,
        lookback_start_date=lookback_start_date,
        lookback_end_date=lookback_end_date,
        lookback_days=lookback_days,
        min_orders_per_customer=min_orders_per_customer,
        order_sources=order_sources,
        include_lookback=True,
    )
    return build_customer_retention_rate_analysis(_fetch_metric_orders(conn, filters), filters)


def fetch_repeat_order_rate_analysis(
    conn,
    *,
    evaluation_start_date: str | None = None,
    evaluation_end_date: str | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,
):
    _require_min_orders_at_least("Repeat order rate", min_orders_per_customer)
    filters = _build_customer_metric_filters(
        evaluation_start_date=evaluation_start_date,
        evaluation_end_date=evaluation_end_date,
        min_orders_per_customer=min_orders_per_customer,
        order_sources=order_sources,
        include_lookback=False,
    )
    return build_repeat_order_rate_analysis(_fetch_metric_orders(conn, filters), filters)


def fetch_customer_affinity_analysis(
    conn,
    *,
    evaluation_start_date: str | None = None,
    evaluation_end_date: str | None = None,
    order_sources: tuple[str, ...] | None = None,
):
    """
    Zomato-style affinity: among verified customers with ≥1 order in the evaluation window,
    segment by recency of last order strictly before evaluation start (60d / 365d rules).
    """
    filters = _build_customer_metric_filters(
        evaluation_start_date=evaluation_start_date,
        evaluation_end_date=evaluation_end_date,
        min_orders_per_customer=2,
        order_sources=order_sources,
        include_lookback=False,
    )
    resolved_sources = list(normalize_order_sources(filters.order_sources) or [])
    order_source_label = "All" if not resolved_sources else ", ".join(resolved_sources)

    orders = fetch_customer_metric_orders(
        conn,
        start_date=None,
        end_date=filters.evaluation_end_date,
        order_sources=filters.order_sources,
    )
    return analyze_customer_affinity(
        orders,
        filters.evaluation_start_date,
        filters.evaluation_end_date,
        order_source_label=order_source_label,
        include_rows=True,
    )


def _build_customer_metric_filters(
    *,
    evaluation_start_date: str | None = None,
    evaluation_end_date: str | None = None,
    lookback_start_date: str | None = None,
    lookback_end_date: str | None = None,
    lookback_days: int | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,
    include_lookback: bool,
):
    current_business_date = get_current_business_date()
    resolved_evaluation_start_date = evaluation_start_date or DateType.fromisoformat(current_business_date).replace(day=1).isoformat()
    default_lookback_start_date = None
    default_lookback_end_date = None

    if include_lookback:
        evaluation_month_start = DateType.fromisoformat(resolved_evaluation_start_date).replace(day=1)
        previous_month_start = shift_month(evaluation_month_start, -1)
        previous_month_end = evaluation_month_start - timedelta(days=1)
        default_lookback_start_date = None if lookback_days is not None else previous_month_start.isoformat()
        default_lookback_end_date = None if lookback_days is not None else previous_month_end.isoformat()

    return CustomerMetricFilters(
        evaluation_start_date=resolved_evaluation_start_date,
        evaluation_end_date=evaluation_end_date or current_business_date,
        lookback_start_date=lookback_start_date if lookback_start_date is not None else default_lookback_start_date,
        lookback_end_date=lookback_end_date if lookback_end_date is not None else default_lookback_end_date,
        lookback_days=lookback_days,
        min_orders_per_customer=min_orders_per_customer,
        order_sources=order_sources,
    )


def _fetch_metric_orders(conn, filters: CustomerMetricFilters):
    resolved_lookback_start_date, resolved_lookback_end_date = resolve_lookback_window(filters)
    has_unbounded_lookback = bool(resolved_lookback_end_date and not resolved_lookback_start_date)
    fetch_start_candidates = [
        date_str for date_str in (filters.evaluation_start_date, resolved_lookback_start_date) if date_str
    ]
    fetch_end_date_candidates = [
        date_str for date_str in (filters.evaluation_end_date, filters.lookback_end_date) if date_str
    ]
    return fetch_customer_metric_orders(
        conn,
        start_date=None if has_unbounded_lookback else min(fetch_start_candidates) if fetch_start_candidates else None,
        end_date=max(fetch_end_date_candidates) if fetch_end_date_candidates else None,
        order_sources=filters.order_sources,
    )


def fetch_top_customers(conn):
    query = """
        WITH customer_item_counts AS (
            SELECT
                o.customer_id,
                mi.name as item_name,
                SUM(oi.quantity) as item_qty
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.order_id
            JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
            WHERE o.order_status = 'Success'
            GROUP BY o.customer_id, mi.name

            UNION ALL

            SELECT
                o.customer_id,
                mi.name as item_name,
                SUM(oia.quantity) as item_qty
            FROM order_item_addons oia
            JOIN order_items oi ON oia.order_item_id = oi.order_item_id
            JOIN orders o ON oi.order_id = o.order_id
            JOIN menu_items mi ON oia.menu_item_id = mi.menu_item_id
            WHERE o.order_status = 'Success'
            GROUP BY o.customer_id, mi.name
        ),
        final_counts AS (
            SELECT customer_id, item_name, SUM(item_qty) as total_item_qty
            FROM customer_item_counts
            GROUP BY customer_id, item_name
        ),
        ranked_items AS (
            SELECT
                customer_id,
                item_name,
                total_item_qty,
                ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total_item_qty DESC, item_name ASC) as rn
            FROM final_counts
        )
        SELECT
            c.customer_id,
            c.name,
            c.total_orders,
            c.total_spent,
            c.last_order_date,
            CASE WHEN c.total_orders > 1 THEN 'Returning' ELSE 'New' END as status,
            ri.item_name as favorite_item,
            ri.total_item_qty as fav_item_qty
        FROM customers c
        LEFT JOIN ranked_items ri ON c.customer_id = ri.customer_id AND ri.rn = 1
        WHERE c.is_verified = 1
        ORDER BY c.total_spent DESC
        LIMIT 50
    """
    rows = conn.execute(query).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def fetch_brand_awareness(conn, granularity: str = 'day'):
    if granularity == 'month':
        date_format = '%Y-%m'
    elif granularity == 'week':
        date_format = '%Y-%W'
    else:
        date_format = '%Y-%m-%d'

    query = f"""
        SELECT
            strftime('{date_format}', first_order_date, '-5 hours') as date,
            COUNT(*) as new_customers
        FROM customers
        WHERE is_verified = 1
          AND first_order_date IS NOT NULL
        GROUP BY 1
        ORDER BY 1 ASC
    """
    return [dict(row) for row in conn.execute(query).fetchall()]


CUSTOMER_METRIC_TREND_MAX_MONTHS = 24
CUSTOMER_METRIC_TREND_DEFAULT_MONTHS = 6


def _normalize_trend_months(months: int | None) -> int:
    if months is None:
        return CUSTOMER_METRIC_TREND_DEFAULT_MONTHS
    if months < 1 or months > CUSTOMER_METRIC_TREND_MAX_MONTHS:
        raise ValueError(f"months must be between 1 and {CUSTOMER_METRIC_TREND_MAX_MONTHS}.")
    return months


def _month_trend_specs(business_date_iso: str, num_months: int) -> list[tuple[str, str, str]]:
    """
    Calendar-month rows, newest first.

    Each tuple is (month_label YYYY-MM, evaluation_start, evaluation_end).
    evaluation_end is the month-end or the business date, whichever is earlier.
    """
    bd = DateType.fromisoformat(business_date_iso)
    month_cursor = DateType(bd.year, bd.month, 1)
    specs: list[tuple[str, str, str]] = []
    for _ in range(num_months):
        m_start, m_end_full = month_bounds(month_cursor)
        m_end_dt = DateType.fromisoformat(m_end_full)
        eval_end = min(m_end_dt, bd).isoformat()
        label = f"{month_cursor.year:04d}-{month_cursor.month:02d}"
        specs.append((label, m_start, eval_end))
        month_cursor = shift_month(month_cursor, -1)
    return specs


def _trend_order_source_label(order_sources: tuple[str, ...] | None) -> str:
    resolved = list(normalize_order_sources(order_sources) or [])
    return "All" if not resolved else ", ".join(resolved)


def _return_rate_filters_for_lookback(
    *,
    eval_start: str,
    eval_end: str,
    min_orders_per_customer: int,
    order_sources: tuple[str, ...] | None,
    lookback_mode: str,
) -> CustomerMetricFilters:
    if lookback_mode == "30d":
        return CustomerMetricFilters(
            evaluation_start_date=eval_start,
            evaluation_end_date=eval_end,
            lookback_days=30,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=order_sources,
        )
    if lookback_mode == "60d":
        return CustomerMetricFilters(
            evaluation_start_date=eval_start,
            evaluation_end_date=eval_end,
            lookback_days=60,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=order_sources,
        )
    if lookback_mode == "lifetime":
        eval_start_dt = DateType.fromisoformat(eval_start)
        day_before = eval_start_dt - timedelta(days=1)
        return CustomerMetricFilters(
            evaluation_start_date=eval_start,
            evaluation_end_date=eval_end,
            lookback_start_date=None,
            lookback_end_date=day_before.isoformat(),
            min_orders_per_customer=min_orders_per_customer,
            order_sources=order_sources,
        )
    raise ValueError(f"Unknown lookback_mode: {lookback_mode}")


def fetch_customer_affinity_trend(
    conn,
    *,
    months: int | None = None,
    order_sources: tuple[str, ...] | None = None,
):
    num_months = _normalize_trend_months(months)
    business_date_iso = get_current_business_date()
    specs = _month_trend_specs(business_date_iso, num_months)
    if not specs:
        return _empty_trend_response(business_date_iso, num_months, _trend_order_source_label(order_sources))

    orders = fetch_customer_metric_orders(
        conn,
        start_date=None,
        end_date=business_date_iso,
        order_sources=order_sources,
    )
    label = _trend_order_source_label(order_sources)
    rows: list[dict[str, object]] = []
    for month_label, eval_start, eval_end in specs:
        result = analyze_customer_affinity(
            orders,
            eval_start,
            eval_end,
            order_source_label=label,
            include_rows=False,
        )
        s = result["summary"]
        rows.append(
            {
                "month": month_label,
                "evaluation_start_date": eval_start,
                "evaluation_end_date": eval_end,
                "customers_in_window": int(s["total_customers"]),
                "new_customers": int(s["new_customers"]),
                "repeat_customers": int(s["repeat_customers"]),
                "lapsed_customers": int(s["lapsed_customers"]),
                "new_pct": float(s["new_pct"]),
                "repeat_pct": float(s["repeat_pct"]),
                "lapsed_pct": float(s["lapsed_pct"]),
            }
        )
    return {
        "rows": rows,
        "defaults": {
            "num_months": num_months,
            "business_date": business_date_iso,
            "order_source_label": label,
            "horizon_note": (
                "One row per calendar month (newest first). "
                "The current month ends on the latest business date in the database."
            ),
        },
    }


def fetch_customer_return_rate_trend(
    conn,
    *,
    months: int | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,
):
    _require_min_orders_at_least("Customer return rate trend", min_orders_per_customer)
    num_months = _normalize_trend_months(months)
    business_date_iso = get_current_business_date()
    specs = _month_trend_specs(business_date_iso, num_months)
    label = _trend_order_source_label(order_sources)
    if not specs:
        return _empty_trend_response(business_date_iso, num_months, label)

    orders = fetch_customer_metric_orders(
        conn,
        start_date=None,
        end_date=business_date_iso,
        order_sources=order_sources,
    )
    rows: list[dict[str, object]] = []
    for month_label, eval_start, eval_end in specs:
        row: dict[str, object] = {
            "month": month_label,
            "evaluation_start_date": eval_start,
            "evaluation_end_date": eval_end,
        }
        for mode in ("30d", "60d", "lifetime"):
            filt = _return_rate_filters_for_lookback(
                eval_start=eval_start,
                eval_end=eval_end,
                min_orders_per_customer=min_orders_per_customer,
                order_sources=order_sources,
                lookback_mode=mode,
            )
            m = calculate_customer_return_rate(orders, filt)
            lb_s, lb_e = resolve_lookback_window(filt)
            row[f"return_rate_{mode}"] = float(m["return_rate"])
            row[f"returning_customers_{mode}"] = int(m["returning_customers"])
            row[f"evaluation_customers_{mode}"] = int(m["total_customers"])
            row[f"lookback_start_{mode}"] = lb_s
            row[f"lookback_end_{mode}"] = lb_e
        rows.append(row)
    return {
        "rows": rows,
        "defaults": {
            "num_months": num_months,
            "business_date": business_date_iso,
            "order_source_label": label,
            "min_orders_per_customer": min_orders_per_customer,
            "horizon_note": (
                "30d / 60d lookbacks end the day before evaluation starts (rolling calendar days). "
                "Lifetime lookback is all orders on or before that day."
            ),
        },
    }


def fetch_customer_retention_rate_trend(
    conn,
    *,
    months: int | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,
):
    if min_orders_per_customer < 1:
        raise ValueError("min_orders_per_customer must be at least 1.")
    num_months = _normalize_trend_months(months)
    business_date_iso = get_current_business_date()
    specs = _month_trend_specs(business_date_iso, num_months)
    label = _trend_order_source_label(order_sources)
    if not specs:
        return _empty_trend_response(business_date_iso, num_months, label)

    orders = fetch_customer_metric_orders(
        conn,
        start_date=None,
        end_date=business_date_iso,
        order_sources=order_sources,
    )
    rows: list[dict[str, object]] = []
    for month_label, eval_start, eval_end in specs:
        row: dict[str, object] = {
            "month": month_label,
            "evaluation_start_date": eval_start,
            "evaluation_end_date": eval_end,
        }
        for mode in ("30d", "60d", "lifetime"):
            filt = _return_rate_filters_for_lookback(
                eval_start=eval_start,
                eval_end=eval_end,
                min_orders_per_customer=min_orders_per_customer,
                order_sources=order_sources,
                lookback_mode=mode,
            )
            m = calculate_customer_retention_rate(orders, filt)
            lb_s, lb_e = resolve_lookback_window(filt)
            row[f"retention_rate_{mode}"] = float(m["retention_rate"])
            row[f"retained_customers_{mode}"] = int(m["retained_customers"])
            row[f"prior_cohort_size_{mode}"] = int(m["total_customers"])
            row[f"lookback_start_{mode}"] = lb_s
            row[f"lookback_end_{mode}"] = lb_e
        rows.append(row)
    return {
        "rows": rows,
        "defaults": {
            "num_months": num_months,
            "business_date": business_date_iso,
            "order_source_label": label,
            "min_orders_per_customer": min_orders_per_customer,
            "horizon_note": (
                "Cohort = customers with ≥1 order in the lookback window; retained = "
                "cohort members with ≥ min_orders in the evaluation month."
            ),
        },
    }


def fetch_customer_repeat_order_rate_trend(
    conn,
    *,
    months: int | None = None,
    min_orders_per_customer: int = 2,
    order_sources: tuple[str, ...] | None = None,
):
    _require_min_orders_at_least("Repeat order rate trend", min_orders_per_customer)
    num_months = _normalize_trend_months(months)
    business_date_iso = get_current_business_date()
    specs = _month_trend_specs(business_date_iso, num_months)
    label = _trend_order_source_label(order_sources)
    if not specs:
        return _empty_trend_response(business_date_iso, num_months, label)

    orders = fetch_customer_metric_orders(
        conn,
        start_date=None,
        end_date=business_date_iso,
        order_sources=order_sources,
    )
    rows: list[dict[str, object]] = []
    for month_label, eval_start, eval_end in specs:
        filt = CustomerMetricFilters(
            evaluation_start_date=eval_start,
            evaluation_end_date=eval_end,
            min_orders_per_customer=min_orders_per_customer,
            order_sources=order_sources,
        )
        m = calculate_repeat_order_rate(orders, filt)
        rows.append(
            {
                "month": month_label,
                "evaluation_start_date": eval_start,
                "evaluation_end_date": eval_end,
                "repeat_order_rate": float(m["repeat_order_rate"]),
                "repeat_order_customers": int(m["repeat_order_customers"]),
                "evaluation_customers": int(m["total_customers"]),
            }
        )
    return {
        "rows": rows,
        "defaults": {
            "num_months": num_months,
            "business_date": business_date_iso,
            "order_source_label": label,
            "min_orders_per_customer": min_orders_per_customer,
            "horizon_note": "Repeat order rate uses only orders inside each evaluation month.",
        },
    }


def _empty_trend_response(business_date_iso: str, num_months: int, order_source_label: str) -> dict[str, object]:
    return {
        "rows": [],
        "defaults": {
            "num_months": num_months,
            "business_date": business_date_iso,
            "order_source_label": order_source_label,
            "horizon_note": "No months in range.",
        },
    }
