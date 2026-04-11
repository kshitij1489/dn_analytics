import json
import math
import re
from difflib import SequenceMatcher
from typing import List, Optional

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors
from src.core.utils.business_date import BUSINESS_DATE_SQL


def _active_customer_filter(alias: str = "c") -> str:
    return f"""
        NOT EXISTS (
            SELECT 1
            FROM customer_merge_history cmh
            WHERE cmh.source_customer_id = {alias}.customer_id
              AND cmh.undone_at IS NULL
        )
    """


def _normalize_phone(value: Optional[str]) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    return digits[-10:] if len(digits) > 10 else digits


def _normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return " ".join(value.lower().strip().split())


def _json_loads_maybe(raw_value, fallback):
    if not raw_value:
        return fallback
    if isinstance(raw_value, (dict, list)):
        return raw_value
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return fallback

def fetch_customer_reorder_rate(conn):
    """Fetch trailing 3-month repeat customer KPI aligned with monthly retention."""
    query = """
        WITH customer_ranks AS (
            SELECT 
                o.customer_id,
                o.created_on,
                ROW_NUMBER() OVER (
                    PARTITION BY o.customer_id
                    ORDER BY o.created_on ASC
                ) as o_rank
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success'
              AND c.is_verified = 1
        ),
        monthly_stats AS (
            SELECT
                strftime('%Y-%m', created_on, '-5 hours') as month_sort,
                COUNT(DISTINCT customer_id) as total_customers,
                COUNT(DISTINCT CASE WHEN o_rank > 1 THEN customer_id END) as returning_customers
            FROM customer_ranks
            GROUP BY 1
        ),
        last_three_months AS (
            SELECT
                total_customers,
                returning_customers
            FROM monthly_stats
            ORDER BY month_sort DESC
            LIMIT 3
        ),
        verified_customer_totals AS (
            SELECT COUNT(*) as total_verified_customers
            FROM customers
            WHERE is_verified = 1
        )
        SELECT
            COALESCE((SELECT total_verified_customers FROM verified_customer_totals), 0) as total_verified_customers,
            COALESCE(ROUND(AVG(total_customers)), 0) as total_customers,
            COALESCE(ROUND(AVG(returning_customers)), 0) as returning_customers,
            ROUND(
                100.0 * COALESCE(SUM(returning_customers), 0) / NULLIF(COALESCE(SUM(total_customers), 0), 0),
                2
            ) as reorder_rate
        FROM last_three_months
    """
    cursor = conn.execute(query)
    row = cursor.fetchone()
    # Convert Row object to dict or return None
    return dict(row) if row else None


def fetch_reorder_rate_trend(conn, granularity='day', start_date=None, end_date=None, metric='orders'):
    """
    Fetch reorder rate trend over time.
    Granularity: 'day', 'week', 'month'
    Metric: 'orders' (Repeat Order Rate) or 'customers' (Repeat Customer Rate)
    """
    from src.core.utils.business_date import get_business_date_range

    # 1. Determine Date/Group Format
    if granularity == 'month':
        date_format = '%Y-%m-01'  # Group by Month Start
    elif granularity == 'week':
        # SQLite: Get start of week (Monday)
        # date(date_column, 'weekday 0', '-6 days')
        date_format = 'week' 
    else: # day
        date_format = '%Y-%m-%d'

    # 2. Date Filtering
    date_filter = ""
    params = []
    if start_date and end_date:
        start_dt, _ = get_business_date_range(start_date)
        _, end_dt = get_business_date_range(end_date)
        date_filter = " AND o.created_on >= ? AND o.created_on <= ?"
        params = [start_dt, end_dt]

    # 3. Group Column Logic
    group_col = f"strftime('{date_format}', o.created_on, '-5 hours')"
    if granularity == 'week':
        group_col = "date(date(o.created_on, '-5 hours'), 'weekday 0', '-6 days')"

    # 4. Query Logic
    # Repeat Definition:
    # A customer/order is "repeat" if there exists a PRIOR successful order 
    # for the same customer created before the current order's bucket start.
    # Actually, simplistic definition: created before the current order.
    # BUT for optimization in aggregation, we can just say:
    # "Is there an order with created_on < o.created_on?"
    
    # However, for "Repeat Customer Rate" (Metric 2), we need to check if the CUSTOMER 
    # had an order before the bucket start. 
    # For "Repeat Order Rate" (Metric 1), we check if the ORDER is from a customer who had prior orders.
    
    # Let's use a unified approach:
    # For every order in the window, flag if it's a "repeat order" (index 0 or 1).
    # Then aggregate.
    
    # optimization: 
    # is_repeat = CASE WHEN EXISTS (SELECT 1 FROM orders prev WHERE prev.customer_id = o.customer_id AND prev.created_on < o.created_on AND prev.order_status = 'Success') THEN 1 ELSE 0 END
    
    # METRIC 1: Repeat Order Rate
    # Denom: Total Orders in bucket
    # Num: Orders where is_repeat = 1
    
    # METRIC 2: Repeat Customer Rate
    # Denom: Count DISTINCT customer_id in bucket
    # Num: Count DISTINCT customer_id where (any order in bucket is a repeat OR checking min(created_on) < bucket_start)
    # Actually simpler: A customer is a "Repeat Customer" in this bucket if they had ANY successful order BEFORE the bucket started.
    # Wait, if a customer joins on Monday (Order 1) and orders again on Tuesday (Order 2).
    # In 'Week' view: They are a NEW customer for that week (if Order 1 was their first ever).
    # But Order 2 is a repeat order.
    # The user defined: "repeat_customer = customer whose first lifetime order date < window_start"
    # So for the bucket, we check if MIN(lifetime_created_on) < bucket_start.
    
    # To do this efficiently in one query without massive joins, we can use the customer's first_order_date 
    # derived state IF we trusted it, but we don't.
    # So we compute first_order_date on the fly? Expensive.
    
    # Alternative:
    # Use window function LAG or MIN over partition, but that requires scanning history.
    # Efficient approach:
    # Pre-calculate `min_created_on` for every verified customer in a CTE/Subquery?
    # Or just use the correlated subquery approach, which is strictly correct but slower.
    # Given dataset size (thousands), correlated subquery is fine.
    
    # Efficient Logic:
    # 1. Get all relevant orders in window.
    # 2. For each, check if `EXISTS (SELECT 1 FROM orders WHERE customer_id=... AND created_on < bucket_start)`
    # This aligns with "Repeat Customer" definition: Active in window AND had history before window.
    
    # Let's adjust slightly:
    # For "Repeat Orders": The order itself is a repeat if previous history exists (created_on < o.created_on).
    # For "Repeat Customers": The customer is repeat if they had history before bucket_start.
    
    # Since we are modifying the query to support both, we'll implement consistent logic.
    
    if metric == 'customers':
        # Metric 2: Repeat Customer Rate
        # Denom: Count of unique active customers in bucket.
        # Num: Count of unique active customers who had an order BEFORE the bucket start.
        
        # We need the bucket start date for the EXISTS check.
        # SQLite's group by returns the mapped date.
        
        query = f"""
            WITH active_in_window AS (
                SELECT 
                    {group_col} as bucket_date,
                    o.customer_id,
                    MIN(o.created_on) as first_seen_in_bucket -- Just to get a timestamp in bucket
                FROM orders o
                JOIN customers c ON o.customer_id = c.customer_id
                WHERE o.order_status = 'Success'
                  AND c.is_verified = 1
                  {date_filter}
                GROUP BY 1, 2
            )
            SELECT 
                bucket_date as date,
                COUNT(DISTINCT customer_id) as total_customers,
                
                -- Numerator: Count customers who had an order BEFORE bucket_date
                -- Note: bucket_date is string 'YYYY-MM-DD'. SQLite compares strings ISO8601 correctly.
                SUM(
                    CASE WHEN EXISTS (
                        SELECT 1 FROM orders prev 
                        WHERE prev.customer_id = active_in_window.customer_id 
                          AND prev.order_status = 'Success'
                          -- bucket_date is 'YYYY-MM-DD'.
                          -- We must append 05:00:00 to match business day start.
                          -- Otherwise orders between 00:00 and 05:00 (which belong to PREV day) 
                          -- would inherently satisfy < 'YYYY-MM-DD 00:00:00', but orders at 02:00 belong to prev day 
                          -- and should be "prior history" for the current day starting at 05:00.
                          -- Wait, if bucket is Oct 27 (starts Oct 27 05:00).
                          -- Order A at Oct 27 02:00 (Business Day Oct 26).
                          -- Comparison: '2023-10-27 02:00' < '2023-10-27' (Midnight). FALSE.
                          -- So Order A is NOT counted as prior history.
                          -- Comparison Fix: '2023-10-27 02:00' < '2023-10-27 05:00'. TRUE.
                          -- So Order A IS counted as prior history. Correct.
                          AND prev.created_on < (bucket_date || ' 05:00:00')
                    ) THEN 1 ELSE 0 END
                ) as returning_customers
            FROM active_in_window
            GROUP BY 1
            ORDER BY 1 ASC
        """
        
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            formatted_date = row[0]
            total = row[1]
            returning = row[2]
            rate = round((returning / total * 100), 2) if total > 0 else 0.0
            results.append({
                "date": formatted_date,
                "total_orders": total, # reuse key for frontend compat
                "reordered_orders": returning, # reuse key
                "value": rate,
                "metric_label": "Customers"
            })
        return results

    else:
        # Metric 1: Repeat Order Rate (Default)
        # Denom: Total orders
        # Num: Orders where customer had AT LEAST ONE prior order (created_on < current_order.created_on)
        
        query = f"""
            SELECT 
                {group_col} as date,
                COUNT(o.order_id) as total_orders,
                SUM(
                    CASE WHEN EXISTS (
                        SELECT 1 FROM orders prev
                        WHERE prev.customer_id = o.customer_id
                          AND prev.order_status = 'Success'
                          AND prev.created_on < o.created_on
                    ) THEN 1 ELSE 0 END
                ) as reordered_orders
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success'
              AND c.is_verified = 1
              {date_filter}
            GROUP BY 1
            ORDER BY 1 ASC
        """
        
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            total = row[1]
            reordered = row[2]
            rate = round((reordered / total * 100), 2) if total > 0 else 0.0
            
            # Simple Dictionary for dataframe conversion
            results.append({
                "date": row[0],
                "total_orders": total,
                "reordered_orders": reordered,
                "value": rate,
                "metric_label": "Orders"
            })
            
        return results

def fetch_customer_loyalty(conn):
    """Fetch Customer Retention Analysis by Cohort/Month
    SQLite changes:
    - TO_CHAR -> strftime
    - DATE_TRUNC -> strftime
    - FILTER (WHERE ...) -> SUM(CASE WHEN ... THEN 1 ELSE 0 END)
    """
    query = """
        WITH customer_ranks AS (
            -- 1. Identify verified orders and rank them by customer lifetime
            SELECT 
                o.customer_id,
                o.created_on,
                o.total,
                ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.created_on ASC) as o_rank
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            WHERE o.order_status = 'Success'
              AND c.is_verified = 1
        ),
        monthly_stats AS (
            -- 2. Aggregate raw sums in the CTE
            SELECT 
                strftime('%Y-%m', created_on, '-5 hours') as month_sort,
                
                COUNT(*) as total_orders,
                SUM(CASE WHEN o_rank > 1 THEN 1 ELSE 0 END) as repeat_orders,
                
                COUNT(DISTINCT customer_id) as total_uid,
                -- Approximate distinct repeat customers in SQLite within group by without advanced FILTER 
                -- We use a case when inside count distinct if supported, or subquery. 
                -- Simpler: COUNT(DISTINCT CASE WHEN o_rank > 1 THEN customer_id END)
                COUNT(DISTINCT CASE WHEN o_rank > 1 THEN customer_id END) as repeat_uid,
                
                SUM(total) as raw_revenue,
                SUM(CASE WHEN o_rank > 1 THEN total ELSE 0 END) as raw_repeat_revenue
            FROM customer_ranks
            GROUP BY 1
        )
        SELECT 
            month_sort as "Month", -- Keeping simplistic for sort
            repeat_orders as "Repeat Orders",
            total_orders as "Total Orders",
            ROUND(100.0 * repeat_orders / NULLIF(total_orders, 0), 2) as "Order Repeat%",
            repeat_uid as "Repeat Customer Count",
            total_uid as "Total Verified Customers",
            ROUND(100.0 * repeat_uid / NULLIF(total_uid, 0), 2) as "Repeat Customer %",
            ROUND(raw_repeat_revenue) as "Repeat Revenue",
            ROUND(raw_revenue) as "Total Revenue",
            ROUND((100.0 * CAST(raw_repeat_revenue AS NUMERIC) / NULLIF(CAST(raw_revenue AS NUMERIC), 0)), 2) as "Revenue Repeat %",
            month_sort -- for sorting
        FROM monthly_stats
        ORDER BY month_sort DESC;
    """
    cursor = conn.execute(query)
    # pd.read_sql_query or convert fetchall
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_top_customers(conn):
    """Fetch Top Verified Customers by Spend"""
    # SQLite changes:
    # - DISTINCT ON -> Group By with Max/Min or Row Number
    # - RealDictCursor removed
    query = """
        WITH customer_item_counts AS (
            -- Main Items
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
            
            -- Addons
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
            SELECT 
                customer_id,
                item_name,
                SUM(item_qty) as total_item_qty
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
    cursor = conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])

def fetch_brand_awareness(conn, granularity: str = 'day'):
    """
    Fetch new verified customers count grouped by first_order_date.
    Granularity: 'day', 'week', 'month'
    """
    # SQLite date formatting
    if granularity == 'month':
        date_format = '%Y-%m'
    elif granularity == 'week':
        # %W returns week number (00-53)
        date_format = '%Y-%W'
    else: # day
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
    
    cursor = conn.execute(query)
    rows = [dict(row) for row in cursor.fetchall()]
    return rows


def search_customers(conn, query_str: str, limit: int = 20):
    """
    Search customers by name or phone.
    Returns list of dicts.
    """
    # SQLite LIKE is case-insensitive for ASCII by default, but good to be explicit with UPPER if needed
    # CAST(phone AS TEXT) handles numeric phone storage if any
    sql = """
        SELECT 
            customer_id,
            name,
            phone,
            total_spent,
            last_order_date
        FROM customers
        WHERE 
            {active_filter}
            AND (
                name LIKE ? 
                OR CAST(phone AS TEXT) LIKE ?
                OR CAST(customer_id AS TEXT) LIKE ?
            )
        ORDER BY last_order_date DESC
        LIMIT ?
    """.format(active_filter=_active_customer_filter("customers"))
    search_term = f"%{query_str}%"
    cursor = conn.execute(sql, (search_term, search_term, search_term, limit))
    results = [dict(row) for row in cursor.fetchall()]
    # Fix: Ensure customer_id is a string for Pydantic validation
    for r in results:
        r['customer_id'] = str(r['customer_id'])
    return results


def format_customer_address(address: dict) -> str:
    """Create a single-line address summary from structured address fields."""
    parts = [
        address.get("address_line_1"),
        address.get("address_line_2"),
        address.get("city"),
        address.get("state"),
        address.get("postal_code"),
        address.get("country"),
    ]
    return ", ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())


def _active_merge_target(conn, customer_id: str) -> str:
    """Resolve a customer through any active merge chain."""
    current_customer_id = str(customer_id)
    visited = set()

    while current_customer_id not in visited:
        visited.add(current_customer_id)
        row = conn.execute(
            """
            SELECT target_customer_id
            FROM customer_merge_history
            WHERE source_customer_id = ?
              AND undone_at IS NULL
            ORDER BY merge_id DESC
            LIMIT 1
            """,
            (current_customer_id,),
        ).fetchone()
        if not row:
            break
        current_customer_id = str(row[0])

    return current_customer_id


def _fetch_customer_summary(conn, customer_id: str):
    cursor = conn.execute(
        """
        WITH primary_address AS (
            SELECT
                ca.customer_id,
                ca.address_line_1,
                ca.address_line_2,
                ca.city,
                ca.state,
                ca.postal_code,
                ca.country,
                ROW_NUMBER() OVER (
                    PARTITION BY ca.customer_id
                    ORDER BY ca.is_default DESC, ca.address_id ASC
                ) as rn
            FROM customer_addresses ca
        )
        SELECT
            c.customer_id,
            c.name,
            c.phone,
            c.address as legacy_address,
            c.total_orders,
            c.total_spent,
            c.last_order_date,
            c.is_verified,
            (
                SELECT COUNT(*)
                FROM customer_addresses ca
                WHERE ca.customer_id = c.customer_id
            ) as address_count,
            CASE WHEN EXISTS (
                SELECT 1
                FROM customer_merge_history cmh
                WHERE cmh.source_customer_id = c.customer_id
                  AND cmh.undone_at IS NULL
            ) THEN 1 ELSE 0 END as is_merged_source,
            pa.address_line_1,
            pa.address_line_2,
            pa.city,
            pa.state,
            pa.postal_code,
            pa.country
        FROM customers c
        LEFT JOIN primary_address pa
            ON pa.customer_id = c.customer_id
           AND pa.rn = 1
        WHERE c.customer_id = ?
        """,
        (customer_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    summary = dict(row)
    address_summary = format_customer_address(summary) or summary.get("legacy_address") or None
    return {
        "customer_id": str(summary["customer_id"]),
        "name": summary.get("name") or f"Customer {summary['customer_id']}",
        "phone": summary.get("phone"),
        "address": address_summary,
        "total_orders": int(summary.get("total_orders") or 0),
        "total_spent": float(summary.get("total_spent") or 0.0),
        "last_order_date": summary.get("last_order_date"),
        "is_verified": bool(summary.get("is_verified")),
        "address_count": int(summary.get("address_count") or 0),
        "is_merged_source": bool(summary.get("is_merged_source")),
        "name_norm": _normalize_text(summary.get("name")),
        "phone_norm": _normalize_phone(summary.get("phone")),
        "address_norm": _normalize_text(address_summary),
        "feature_text": " | ".join(
            part for part in [
                _normalize_text(summary.get("name")),
                _normalize_phone(summary.get("phone")),
                _normalize_text(address_summary),
            ]
            if part
        ) or f"customer_{summary['customer_id']}",
    }


def _fetch_active_similarity_population(conn):
    cursor = conn.execute(
        f"""
        WITH primary_address AS (
            SELECT
                ca.customer_id,
                ca.address_line_1,
                ca.address_line_2,
                ca.city,
                ca.state,
                ca.postal_code,
                ca.country,
                ROW_NUMBER() OVER (
                    PARTITION BY ca.customer_id
                    ORDER BY ca.is_default DESC, ca.address_id ASC
                ) as rn
            FROM customer_addresses ca
        )
        SELECT
            c.customer_id,
            c.name,
            c.phone,
            c.address as legacy_address,
            c.total_orders,
            c.total_spent,
            c.last_order_date,
            c.is_verified,
            pa.address_line_1,
            pa.address_line_2,
            pa.city,
            pa.state,
            pa.postal_code,
            pa.country
        FROM customers c
        LEFT JOIN primary_address pa
            ON pa.customer_id = c.customer_id
           AND pa.rn = 1
        WHERE {_active_customer_filter("c")}
        """
    )
    rows = []
    for row in cursor.fetchall():
        record = dict(row)
        address_summary = format_customer_address(record) or record.get("legacy_address") or None
        rows.append({
            "customer_id": str(record["customer_id"]),
            "name": record.get("name") or f"Customer {record['customer_id']}",
            "phone": record.get("phone"),
            "address": address_summary,
            "total_orders": int(record.get("total_orders") or 0),
            "total_spent": float(record.get("total_spent") or 0.0),
            "last_order_date": record.get("last_order_date"),
            "is_verified": bool(record.get("is_verified")),
            "name_norm": _normalize_text(record.get("name")),
            "phone_norm": _normalize_phone(record.get("phone")),
            "address_norm": _normalize_text(address_summary),
            "feature_text": " | ".join(
                part for part in [
                    _normalize_text(record.get("name")),
                    _normalize_phone(record.get("phone")),
                    _normalize_text(address_summary),
                ]
                if part
            ) or f"customer_{record['customer_id']}",
        })
    return rows


def _similarity_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _numeric_closeness(left_value, right_value) -> float:
    left_float = float(left_value or 0.0)
    right_float = float(right_value or 0.0)
    denominator = max(abs(left_float), abs(right_float), 1.0)
    return max(0.0, 1.0 - abs(left_float - right_float) / denominator)


def _customer_rank(record: dict):
    try:
        customer_id_rank = -int(record["customer_id"])
    except (TypeError, ValueError):
        customer_id_rank = 0
    return (
        1 if record.get("is_verified") else 0,
        int(record.get("total_orders") or 0),
        float(record.get("total_spent") or 0.0),
        1 if record.get("phone_norm") else 0,
        customer_id_rank,
    )


def _build_similarity_candidate(left_record: dict, right_record: dict, text_similarity: float, model_name: str):
    name_similarity = _similarity_ratio(left_record["name_norm"], right_record["name_norm"])
    address_similarity = _similarity_ratio(left_record["address_norm"], right_record["address_norm"])
    phone_exact = bool(left_record["phone_norm"] and left_record["phone_norm"] == right_record["phone_norm"])
    orders_similarity = _numeric_closeness(left_record["total_orders"], right_record["total_orders"])
    spend_similarity = _numeric_closeness(left_record["total_spent"], right_record["total_spent"])
    behavior_similarity = (orders_similarity + spend_similarity) / 2.0

    score = (
        text_similarity * 0.45
        + name_similarity * 0.25
        + address_similarity * 0.15
        + behavior_similarity * 0.15
        + (0.20 if phone_exact else 0.0)
    )
    if phone_exact:
        score = max(score, 0.88)
    score = min(score, 0.99)

    reasons = []
    if phone_exact:
        reasons.append("Exact phone match")
    if name_similarity >= 0.85:
        reasons.append("Very similar customer names")
    if address_similarity >= 0.80:
        reasons.append("Very similar saved addresses")
    if behavior_similarity >= 0.75:
        reasons.append("Similar order count / spend profile")
    if text_similarity >= 0.80:
        reasons.append("Strong text similarity across name, phone, and address")
    if not reasons:
        reasons.append("High overall similarity score")

    source_record, target_record = (left_record, right_record)
    if _customer_rank(left_record) > _customer_rank(right_record):
        source_record, target_record = right_record, left_record
    elif _customer_rank(left_record) == _customer_rank(right_record):
        try:
            if int(left_record["customer_id"]) < int(right_record["customer_id"]):
                source_record, target_record = right_record, left_record
        except (TypeError, ValueError):
            pass

    return {
        "source_customer": {
            key: source_record[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "target_customer": {
            key: target_record[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "score": round(score, 4),
        "model_name": model_name,
        "reasons": reasons,
        "metrics": {
            "text_similarity": round(text_similarity, 4),
            "name_similarity": round(name_similarity, 4),
            "address_similarity": round(address_similarity, 4),
            "behavior_similarity": round(behavior_similarity, 4),
            "phone_exact_match": 1.0 if phone_exact else 0.0,
        },
    }


def fetch_customer_similarity_candidates(conn, limit: int = 20, min_score: float = 0.72):
    model_name = "basic_duplicate_knn_v1"
    population = _fetch_active_similarity_population(conn)
    if len(population) < 2:
        return []

    documents = [record["feature_text"] for record in population]
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
    tfidf_matrix = vectorizer.fit_transform(documents)

    neighbor_count = min(6, len(population))
    neighbor_model = NearestNeighbors(metric="cosine", algorithm="brute", n_neighbors=neighbor_count)
    neighbor_model.fit(tfidf_matrix)
    distances, indices = neighbor_model.kneighbors(tfidf_matrix)

    best_pairs = {}

    def register_pair(left_record, right_record, text_similarity):
        candidate = _build_similarity_candidate(left_record, right_record, text_similarity, model_name)
        metrics = candidate["metrics"]
        should_keep = (
            candidate["score"] >= min_score
            or metrics["phone_exact_match"] == 1.0
            or (metrics["name_similarity"] >= 0.80 and metrics["address_similarity"] >= 0.70)
        )
        if not should_keep:
            return

        pair_key = tuple(sorted([left_record["customer_id"], right_record["customer_id"]]))
        previous = best_pairs.get(pair_key)
        if previous is None or candidate["score"] > previous["score"]:
            best_pairs[pair_key] = candidate

    for row_index, neighbor_indexes in enumerate(indices):
        for distance, neighbor_index in zip(distances[row_index], neighbor_indexes):
            if neighbor_index == row_index:
                continue
            register_pair(
                population[row_index],
                population[neighbor_index],
                max(0.0, 1.0 - float(distance)),
            )

    phone_groups = {}
    for record in population:
        if record["phone_norm"]:
            phone_groups.setdefault(record["phone_norm"], []).append(record)

    for group in phone_groups.values():
        if len(group) < 2:
            continue
        for left_index in range(len(group)):
            for right_index in range(left_index + 1, len(group)):
                left_record = group[left_index]
                right_record = group[right_index]
                register_pair(
                    left_record,
                    right_record,
                    _similarity_ratio(left_record["feature_text"], right_record["feature_text"]),
                )

    suggestions = sorted(
        best_pairs.values(),
        key=lambda item: (item["score"], item["target_customer"]["total_orders"], item["target_customer"]["total_spent"]),
        reverse=True,
    )
    return suggestions[:limit]


def fetch_customer_merge_preview(
    conn,
    source_customer_id: str,
    target_customer_id: str,
    similarity_score: Optional[float] = None,
    model_name: Optional[str] = None,
    reasons: Optional[List[str]] = None,
):
    source_summary = _fetch_customer_summary(conn, source_customer_id)
    target_summary = _fetch_customer_summary(conn, target_customer_id)
    if not source_summary or not target_summary:
        return {"status": "error", "message": "One or both customers were not found."}
    if source_summary["customer_id"] == target_summary["customer_id"]:
        return {"status": "error", "message": "Source and target customers must be different."}
    if source_summary["is_merged_source"]:
        return {"status": "error", "message": "The selected source customer has already been merged."}
    if target_summary["is_merged_source"]:
        return {"status": "error", "message": "The selected target customer is not active."}

    moved_orders = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE customer_id = ?",
        (source_summary["customer_id"],),
    ).fetchone()[0]

    if not reasons:
        candidate = _build_similarity_candidate(
            source_summary,
            target_summary,
            _similarity_ratio(source_summary["feature_text"], target_summary["feature_text"]),
            model_name or "basic_duplicate_knn_v1",
        )
        reasons = candidate["reasons"]
        similarity_score = similarity_score if similarity_score is not None else candidate["score"]
        model_name = model_name or candidate["model_name"]

    return {
        "source_customer": {
            key: source_summary[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "target_customer": {
            key: target_summary[key]
            for key in ("customer_id", "name", "phone", "address", "total_orders", "total_spent", "last_order_date", "is_verified")
        },
        "orders_to_move": int(moved_orders),
        "source_address_count": int(source_summary["address_count"]),
        "target_address_count": int(target_summary["address_count"]),
        "reasons": reasons or [],
        "score": similarity_score,
        "model_name": model_name or "basic_duplicate_knn_v1",
    }


def _copy_customer_addresses_to_target(conn, source_customer_id: str, target_customer_id: str):
    source_rows = conn.execute(
        """
        SELECT label, address_line_1, address_line_2, city, state, postal_code, country, is_default
        FROM customer_addresses
        WHERE customer_id = ?
        ORDER BY is_default DESC, address_id ASC
        """,
        (source_customer_id,),
    ).fetchall()
    if not source_rows:
        return {"copied_count": 0, "inserted_address_ids": []}

    target_has_default = conn.execute(
        """
        SELECT 1
        FROM customer_addresses
        WHERE customer_id = ?
          AND is_default = 1
        LIMIT 1
        """,
        (target_customer_id,),
    ).fetchone() is not None

    copied_count = 0
    inserted_address_ids = []
    for row in source_rows:
        row_dict = dict(row)
        default_flag = 0 if target_has_default else (1 if row_dict.get("is_default") else 0)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO customer_addresses (
                customer_id,
                label,
                address_line_1,
                address_line_2,
                city,
                state,
                postal_code,
                country,
                is_default
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_customer_id,
                row_dict.get("label"),
                row_dict.get("address_line_1"),
                row_dict.get("address_line_2"),
                row_dict.get("city"),
                row_dict.get("state"),
                row_dict.get("postal_code"),
                row_dict.get("country"),
                default_flag,
            ),
        )
        if cursor.rowcount:
            copied_count += 1
            inserted_address_ids.append(int(cursor.lastrowid))
            if default_flag:
                target_has_default = True

    return {
        "copied_count": copied_count,
        "inserted_address_ids": inserted_address_ids,
    }


def _fetch_customer_mergeable_fields(conn, customer_id: str):
    row = conn.execute(
        """
        SELECT phone, address, gstin
        FROM customers
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    return dict(row) if row else {"phone": None, "address": None, "gstin": None}


def _recompute_customer_aggregates(conn, customer_id: str) -> None:
    aggregate_row = conn.execute(
        """
        SELECT
            COUNT(*) as total_orders,
            COALESCE(SUM(total), 0) as total_spent,
            MIN(created_on) as first_order_date,
            MAX(created_on) as last_order_date
        FROM orders
        WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()

    conn.execute(
        """
        UPDATE customers
        SET total_orders = ?,
            total_spent = ?,
            first_order_date = ?,
            last_order_date = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE customer_id = ?
        """,
        (
            int(aggregate_row["total_orders"] or 0),
            float(aggregate_row["total_spent"] or 0.0),
            aggregate_row["first_order_date"],
            aggregate_row["last_order_date"],
            customer_id,
        ),
    )


def merge_customers(
    conn,
    source_customer_id: str,
    target_customer_id: str,
    similarity_score: Optional[float] = None,
    model_name: Optional[str] = None,
    reasons: Optional[List[str]] = None,
):
    preview = fetch_customer_merge_preview(
        conn,
        source_customer_id,
        target_customer_id,
        similarity_score=similarity_score,
        model_name=model_name,
        reasons=reasons,
    )
    if preview.get("status") == "error":
        return preview

    source_customer_id = preview["source_customer"]["customer_id"]
    target_customer_id = preview["target_customer"]["customer_id"]

    try:
        order_rows = conn.execute(
            "SELECT order_id FROM orders WHERE customer_id = ? ORDER BY order_id ASC",
            (source_customer_id,),
        ).fetchall()
        moved_order_ids = [int(row[0]) for row in order_rows]
        address_copy_result = _copy_customer_addresses_to_target(conn, source_customer_id, target_customer_id)
        target_before_fields = _fetch_customer_mergeable_fields(conn, target_customer_id)

        conn.execute(
            """
            UPDATE customers
            SET phone = COALESCE(phone, (SELECT phone FROM customers WHERE customer_id = ?)),
                address = COALESCE(address, (SELECT address FROM customers WHERE customer_id = ?)),
                gstin = COALESCE(gstin, (SELECT gstin FROM customers WHERE customer_id = ?)),
                updated_at = CURRENT_TIMESTAMP
            WHERE customer_id = ?
            """,
            (source_customer_id, source_customer_id, source_customer_id, target_customer_id),
        )

        conn.execute(
            """
            UPDATE orders
            SET customer_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE customer_id = ?
            """,
            (target_customer_id, source_customer_id),
        )

        merge_cursor = conn.execute(
            """
            INSERT INTO customer_merge_history (
                source_customer_id,
                target_customer_id,
                similarity_score,
                model_name,
                suggestion_context,
                source_snapshot,
                target_snapshot,
                moved_order_ids,
                copied_address_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING merge_id
            """,
            (
                source_customer_id,
                target_customer_id,
                similarity_score if similarity_score is not None else preview.get("score"),
                model_name or preview.get("model_name"),
                json.dumps({
                    "reasons": reasons or preview.get("reasons", []),
                    "target_before_fields": target_before_fields,
                    "inserted_target_address_ids": address_copy_result["inserted_address_ids"],
                }),
                json.dumps(preview["source_customer"]),
                json.dumps(preview["target_customer"]),
                json.dumps(moved_order_ids),
                address_copy_result["copied_count"],
            ),
        )
        merge_id = merge_cursor.fetchone()[0]

        _recompute_customer_aggregates(conn, source_customer_id)
        _recompute_customer_aggregates(conn, target_customer_id)
        conn.commit()

        return {
            "status": "success",
            "message": f"Merged customer {source_customer_id} into {target_customer_id}.",
            "merge_id": int(merge_id),
            "source_customer_id": str(source_customer_id),
            "target_customer_id": str(target_customer_id),
            "orders_moved": len(moved_order_ids),
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}


def fetch_customer_merge_history(conn, limit: int = 20):
    cursor = conn.execute(
        """
        SELECT
            h.merge_id,
            h.source_customer_id,
            h.target_customer_id,
            h.similarity_score,
            h.model_name,
            h.moved_order_ids,
            h.copied_address_count,
            h.merged_at,
            h.undone_at,
            h.source_snapshot,
            h.target_snapshot,
            source.name as current_source_name,
            target.name as current_target_name
        FROM customer_merge_history h
        LEFT JOIN customers source ON source.customer_id = h.source_customer_id
        LEFT JOIN customers target ON target.customer_id = h.target_customer_id
        ORDER BY h.merged_at DESC, h.merge_id DESC
        LIMIT ?
        """,
        (limit,),
    )

    history = []
    for row in cursor.fetchall():
        entry = dict(row)
        source_snapshot = _json_loads_maybe(entry.get("source_snapshot"), {})
        target_snapshot = _json_loads_maybe(entry.get("target_snapshot"), {})
        moved_order_ids = _json_loads_maybe(entry.get("moved_order_ids"), [])
        history.append({
            "merge_id": int(entry["merge_id"]),
            "source_customer_id": str(entry["source_customer_id"]),
            "source_name": source_snapshot.get("name") or entry.get("current_source_name"),
            "target_customer_id": str(entry["target_customer_id"]),
            "target_name": target_snapshot.get("name") or entry.get("current_target_name"),
            "similarity_score": entry.get("similarity_score"),
            "model_name": entry.get("model_name"),
            "orders_moved": len(moved_order_ids),
            "copied_address_count": int(entry.get("copied_address_count") or 0),
            "merged_at": entry["merged_at"],
            "undone_at": entry.get("undone_at"),
        })
    return history


def undo_customer_merge(conn, merge_id: int):
    row = conn.execute(
        """
        SELECT *
        FROM customer_merge_history
        WHERE merge_id = ?
        """,
        (merge_id,),
    ).fetchone()
    if not row:
        return {"status": "error", "message": "Merge history entry not found."}
    if row["undone_at"] is not None:
        return {"status": "error", "message": "This merge has already been undone."}

    moved_order_ids = _json_loads_maybe(row["moved_order_ids"], [])
    if not isinstance(moved_order_ids, list):
        moved_order_ids = []
    suggestion_context = _json_loads_maybe(row["suggestion_context"], {})
    inserted_target_address_ids = suggestion_context.get("inserted_target_address_ids", [])
    if not isinstance(inserted_target_address_ids, list):
        inserted_target_address_ids = []
    target_before_fields = suggestion_context.get("target_before_fields", {})
    if not isinstance(target_before_fields, dict):
        target_before_fields = {}

    try:
        if moved_order_ids:
            placeholders = ",".join("?" for _ in moved_order_ids)
            conn.execute(
                f"""
                UPDATE orders
                SET customer_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE customer_id = ?
                  AND order_id IN ({placeholders})
                """,
                [row["source_customer_id"], row["target_customer_id"], *moved_order_ids],
            )

        if inserted_target_address_ids:
            placeholders = ",".join("?" for _ in inserted_target_address_ids)
            conn.execute(
                f"""
                DELETE FROM customer_addresses
                WHERE customer_id = ?
                  AND address_id IN ({placeholders})
                """,
                [row["target_customer_id"], *inserted_target_address_ids],
            )

        if target_before_fields:
            conn.execute(
                """
                UPDATE customers
                SET phone = ?,
                    address = ?,
                    gstin = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE customer_id = ?
                """,
                (
                    target_before_fields.get("phone"),
                    target_before_fields.get("address"),
                    target_before_fields.get("gstin"),
                    row["target_customer_id"],
                ),
            )

        conn.execute(
            """
            UPDATE customer_merge_history
            SET undone_at = CURRENT_TIMESTAMP,
                undo_context = ?
            WHERE merge_id = ?
            """,
            (
                json.dumps({
                    "restored_order_count": len(moved_order_ids),
                    "removed_target_address_ids": inserted_target_address_ids,
                    "restored_target_fields": sorted(target_before_fields.keys()),
                }),
                merge_id,
            ),
        )

        _recompute_customer_aggregates(conn, str(row["source_customer_id"]))
        _recompute_customer_aggregates(conn, str(row["target_customer_id"]))
        conn.commit()

        return {
            "status": "success",
            "message": f"Undo complete for merge {merge_id}.",
            "merge_id": int(merge_id),
            "source_customer_id": str(row["source_customer_id"]),
            "target_customer_id": str(row["target_customer_id"]),
            "orders_moved": len(moved_order_ids),
        }
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}


def fetch_customer_profile_data(conn, customer_id: str):
    """
    Fetch customer details and their order history with item summaries.
    """
    # 1. Get Customer Details
    cust_sql = """
        SELECT 
            customer_id,
            name,
            phone,
            address,
            total_spent,
            last_order_date,
            is_verified
        FROM customers
        WHERE customer_id = ?
    """
    cursor = conn.execute(cust_sql, (customer_id,))
    row = cursor.fetchone()
    if not row:
        return None, [], []
        
    customer = dict(row)
    customer['customer_id'] = str(customer['customer_id'])
    customer['is_verified'] = bool(customer['is_verified'])

    addresses_sql = """
        SELECT
            address_id,
            customer_id,
            label,
            address_line_1,
            address_line_2,
            city,
            state,
            postal_code,
            country,
            is_default
        FROM customer_addresses
        WHERE customer_id = ?
        ORDER BY is_default DESC, address_id ASC
    """
    cursor = conn.execute(addresses_sql, (customer_id,))
    addresses = [dict(row) for row in cursor.fetchall()]

    for address in addresses:
        address['customer_id'] = str(address['customer_id'])
        address['is_default'] = bool(address['is_default'])

    primary_address = next((address for address in addresses if address['is_default']), None) or (addresses[0] if addresses else None)
    if primary_address:
        customer['address'] = format_customer_address(primary_address)

    # 2. Get Orders with Item Summary
    # We need to aggregate items per order. 
    # SQLite group_concat is useful here.
    orders_sql = """
        WITH order_items_summary AS (
            SELECT 
                oi.order_id,
                GROUP_CONCAT(mi.name || ' (' || oi.quantity || ')', ', ') as items_summary
            FROM order_items oi
            JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
            WHERE oi.order_id IN (
                SELECT order_id FROM orders WHERE customer_id = ?
            )
            GROUP BY oi.order_id
        )
        SELECT 
            o.order_id,
            COALESCE(o.petpooja_order_id, o.order_id) as order_number,
            o.created_on,
            o.total as total_amount,
            COALESCE(o.order_from, 'Unknown') as order_source,
            o.order_status as status,
            c.is_verified,
            COALESCE(ois.items_summary, 'No Items') as items_summary
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        LEFT JOIN order_items_summary ois ON o.order_id = ois.order_id
        WHERE o.customer_id = ?
        ORDER BY o.created_on DESC
    """
    
    cursor = conn.execute(orders_sql, (customer_id, customer_id))
    orders = [dict(row) for row in cursor.fetchall()]
    
    # SQLite booleans are 0/1, and IDs might be ints
    for o in orders:
        o['order_id'] = str(o['order_id'])
        o['order_number'] = str(o['order_number'])
        o['is_verified'] = bool(o['is_verified'])

    return customer, orders, addresses
