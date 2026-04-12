from __future__ import annotations

from datetime import date as DateType, timedelta

import pandas as pd

from src.core.queries.customer_metric_helpers import (
    CustomerMetricFilters,
    build_customer_return_rate_analysis,
    build_customer_retention_rate_analysis,
    build_monthly_customer_metric_rows,
    build_repeat_order_rate_analysis,
    fetch_customer_metric_orders,
    resolve_lookback_window,
    shift_month,
)
from src.core.utils.business_date import get_current_business_date


def fetch_customer_loyalty(conn):
    rows = build_monthly_customer_metric_rows(fetch_customer_metric_orders(conn))
    return pd.DataFrame(rows)


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
    filters = _build_customer_metric_filters(
        evaluation_start_date=evaluation_start_date,
        evaluation_end_date=evaluation_end_date,
        min_orders_per_customer=min_orders_per_customer,
        order_sources=order_sources,
        include_lookback=False,
    )
    return build_repeat_order_rate_analysis(_fetch_metric_orders(conn, filters), filters)


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
