import pandas as pd
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.core.order_item_key import AssignmentKeyIndex, has_local_pos_backing
from src.core.utils.business_date import get_business_date_range
from utils.menu_item_variant_enforcement import (
    is_synthetic_mapping,
    synthetic_mapping_kind,
)

# SQLite strftime('%w') = 0 Sunday, 1 Monday, ..., 6 Saturday
DAY_NAME_TO_SQLITE_DOW = {
    "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
    "Thursday": 4, "Friday": 5, "Saturday": 6,
}

# Each assignment-pair predicate consumes two bind parameters and contributes
# two levels to SQLite's expression tree. Keep batches below both the legacy
# 999-variable ceiling and the default 1000-level expression-depth ceiling.
ASSIGNMENT_PAIR_QUERY_CHUNK_SIZE = 400


def _weekdays_to_sqlite_dow(selected_weekdays):
    """Convert day names (e.g. from frontend) to SQLite %w values (0-6). Pass-through if already ints."""
    if not selected_weekdays:
        return None
    result = []
    for d in selected_weekdays:
        if isinstance(d, int) and 0 <= d <= 6:
            result.append(d)
        elif isinstance(d, str) and d in DAY_NAME_TO_SQLITE_DOW:
            result.append(DAY_NAME_TO_SQLITE_DOW[d])
    return result if result else None


def fetch_menu_stats(conn, name_search=None, type_choice="All", start_date=None, end_date=None, selected_weekdays=None, include_identity=False):
    """Fetch Menu Analytics (Reorder stats, revenue, etc) with filtering"""
    
    # 1. Build order-level filters
    order_filter_sql = ""
    order_params = []
    
    if start_date:
        order_filter_sql += " AND o.created_on >= ?"
        # Business day starts at 5:00 AM
        order_params.append(f"{start_date} 05:00:00")
    if end_date:
        # Business day ends at 4:59:59 AM NEXT day
        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_str = end_dt.strftime("%Y-%m-%d")
        order_filter_sql += " AND o.created_on <= ?"
        order_params.append(f"{end_str} 04:59:59")
    # Days filter: include only orders whose business-day weekday is in selected days (exclude unselected)
    dow_list = _weekdays_to_sqlite_dow(selected_weekdays) if selected_weekdays else None
    if dow_list is not None and len(dow_list) < 7:
        placeholders = ",".join("?" for _ in dow_list)
        # strftime('%w', ..., '-5 hours') = weekday in business-day terms (0=Sun .. 6=Sat)
        order_filter_sql += f" AND CAST(strftime('%w', o.created_on, '-5 hours') AS INTEGER) IN ({placeholders})"
        order_params.extend(dow_list)

    # 2. Build item-level filters
    item_filter_sql = ""
    item_params = []
    
    if name_search:
        item_filter_sql += " AND item_name LIKE ?"
        item_params.append(f"%{name_search}%")
    
    if type_choice and type_choice != "All":
        item_filter_sql += " AND item_type = ?"
        item_params.append(type_choice)

    # Combined params
    all_params = order_params + item_params

    # SQLite compatible query using GROUP BY instead of DISTINCT ON
    menu_query = f"""
        WITH dedup_items AS (
            -- 1. Deduplicate Items GLOBALLY using MAX/GROUP BY trick or ROW_NUMBER
            SELECT 
                order_item_id, menu_item_id, total_price, quantity, order_id, variant_id
            FROM (
                SELECT 
                    oi.*, 
                    ROW_NUMBER() OVER(PARTITION BY oi.order_id, oi.name_raw, oi.quantity, oi.unit_price ORDER BY oi.order_item_id) as rn
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.order_id
                WHERE o.order_status = 'Success'
            ) WHERE rn = 1
        ),
        dedup_addons AS (
             -- 1b. Deduplicate Addons
             SELECT 
                menu_item_id, total_price, quantity, order_id, order_item_id, variant_id
             FROM (
                SELECT 
                    oia.menu_item_id, (oia.price * oia.quantity) as total_price, oia.quantity, oi.order_id, oi.order_item_id, oia.variant_id,
                    ROW_NUMBER() OVER(PARTITION BY oia.order_item_id, oia.name_raw, oia.quantity, oia.price ORDER BY oia.order_item_addon_id) as rn
                FROM order_item_addons oia
                JOIN dedup_items oi ON oia.order_item_id = oi.order_item_id
             ) WHERE rn = 1
        ),
        global_item_history AS (
            -- 2. Combine & Rank Globally (with variant unit/value for aggregation)
            SELECT 
                mi.menu_item_id,
                mi.name AS item_name,
                mi.type AS item_type,
                o.customer_id,
                o.created_on,
                di.total_price AS item_revenue,
                di.quantity as sold_as_item_qty,
                0 as sold_as_addon_qty,
                COALESCE(UPPER(v.unit), '') as variant_unit,
                COALESCE(v.value, 0) * di.quantity as unit_amount,
                ROW_NUMBER() OVER (PARTITION BY o.customer_id, mi.menu_item_id ORDER BY o.created_on) as customer_item_rank
            FROM dedup_items di
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON di.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN variants v ON di.variant_id = v.variant_id
            
            UNION ALL
            
            SELECT 
                mi.menu_item_id,
                mi.name AS item_name,
                mi.type AS item_type,
                o.customer_id,
                o.created_on,
                da.total_price AS item_revenue,
                0 as sold_as_item_qty,
                da.quantity as sold_as_addon_qty,
                COALESCE(UPPER(v.unit), '') as variant_unit,
                COALESCE(v.value, 0) * da.quantity as unit_amount,
                ROW_NUMBER() OVER (PARTITION BY o.customer_id, mi.menu_item_id ORDER BY o.created_on) as customer_item_rank
            FROM dedup_addons da
            JOIN dedup_items di ON da.order_item_id = di.order_item_id
            JOIN orders o ON di.order_id = o.order_id
            JOIN menu_items mi ON da.menu_item_id = mi.menu_item_id
            JOIN customers c ON o.customer_id = c.customer_id
            LEFT JOIN variants v ON da.variant_id = v.variant_id
        ),
        filtered_items AS (
            -- 3. Apply User Filters
            SELECT * 
            FROM global_item_history o
            WHERE 1=1 {order_filter_sql} {item_filter_sql}
        ),
        reorder_stats AS (
            -- 4. Aggregate
            SELECT 
                menu_item_id, item_name, item_type,
                SUM(sold_as_item_qty) as sold_as_item,
                SUM(sold_as_addon_qty) as sold_as_addon,
                
                SUM(CASE WHEN customer_item_rank > 1 THEN sold_as_item_qty + sold_as_addon_qty ELSE 0 END) AS qty_reordered,
                
                COUNT(DISTINCT CASE WHEN customer_item_rank > 1 THEN customer_id END) AS customers_who_reordered,
                
                COUNT(DISTINCT customer_id) AS total_unique_customers,
                (SUM(sold_as_item_qty) + SUM(sold_as_addon_qty)) AS total_qty_sold,
                COUNT(*) AS total_transactions,
                SUM(item_revenue) AS total_revenue,
                SUM(CASE WHEN customer_item_rank > 1 THEN item_revenue ELSE 0 END) AS repeat_customer_revenue,
                
                -- Unit-based aggregations: only sum value*qty where unit matches
                SUM(CASE WHEN variant_unit = 'GMS' THEN unit_amount ELSE 0 END) AS total_gms,
                SUM(CASE WHEN variant_unit = 'ML' THEN unit_amount ELSE 0 END) AS total_ml,
                SUM(CASE WHEN variant_unit = 'COUNT' THEN unit_amount ELSE 0 END) AS total_count
            FROM filtered_items
            GROUP BY menu_item_id, item_name, item_type
        )
        SELECT 
            {('menu_item_id,' if include_identity else '')}
            item_name as "Item Name",
            item_type as "Type",
            sold_as_addon as "As Addon (Qty)",
            sold_as_item as "As Item (Qty)",
            total_qty_sold as "Total Sold (Qty)",
            total_revenue as "Total Revenue",
            repeat_customer_revenue as "Repeat Revenue",
            total_gms as "Total GMS",
            total_ml as "Total ML",
            total_count as "Total COUNT",
            qty_reordered AS "Reorder Count", 
            customers_who_reordered AS "Repeat Customer (Lifetime)",
            total_unique_customers AS "Unique Customers",
            ROUND(100.0 * customers_who_reordered / NULLIF(total_unique_customers, 0), 2) AS "Reorder Rate %",
            ROUND(100.0 * repeat_customer_revenue / NULLIF(total_revenue, 0), 2) AS "Repeat Revenue %"
        FROM reorder_stats
        WHERE total_unique_customers > 0
        ORDER BY total_revenue DESC;
    """
    
    cursor = conn.execute(menu_query, all_params)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])


def fetch_menu_items_summary(
    conn,
    page=1,
    page_size=50,
    sort_column="total_revenue",
    sort_direction="DESC",
    filters=None,
    start_date=None,
    end_date=None,
):
    """Fetch paginated menu item stats with optional business-date filtering."""
    try:
        sort_map = {
            "menu_item_id": "menu_item_id",
            "name": "name",
            "type": "type",
            "total_revenue": "total_revenue",
            "total_sold": "total_sold",
            "sold_as_item": "sold_as_item",
            "sold_as_addon": "sold_as_addon",
            "is_active": "is_active",
        }
        safe_sort_column = sort_map.get(sort_column, "total_revenue")
        safe_sort_direction = "ASC" if str(sort_direction).upper() == "ASC" else "DESC"

        date_conditions = []
        date_params = []
        if start_date:
            start_dt, _ = get_business_date_range(start_date)
            date_conditions.append("o.created_on >= ?")
            date_params.append(start_dt)
        if end_date:
            _, end_dt = get_business_date_range(end_date)
            date_conditions.append("o.created_on <= ?")
            date_params.append(end_dt)
        date_filter_sql = f" AND {' AND '.join(date_conditions)}" if date_conditions else ""

        filter_map = {
            "menu_item_id": "mi.menu_item_id",
            "name": "mi.name",
            "type": "mi.type",
        }
        where_conditions = []
        filter_params = []
        for key, value in (filters or {}).items():
            mapped_column = filter_map.get(key)
            if not mapped_column or value in (None, ""):
                continue
            where_conditions.append(f"UPPER(CAST({mapped_column} AS TEXT)) LIKE ?")
            filter_params.append(f"%{str(value).upper()}%")

        where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""

        count_query = f"""
            SELECT COUNT(*) AS count
            FROM menu_items mi
            {where_clause}
        """
        total_count = conn.execute(count_query, filter_params).fetchone()[0]

        offset = (page - 1) * page_size
        data_query = f"""
            WITH filtered_orders AS (
                SELECT o.order_id
                FROM orders o
                WHERE o.order_status = 'Success'
                {date_filter_sql}
            ),
            dedup_items AS (
                SELECT order_item_id, menu_item_id, total_price, quantity
                FROM (
                    SELECT
                        oi.order_item_id,
                        oi.menu_item_id,
                        oi.total_price,
                        oi.quantity,
                        ROW_NUMBER() OVER (
                            PARTITION BY oi.order_id, oi.name_raw, oi.quantity, oi.unit_price
                            ORDER BY oi.order_item_id
                        ) AS rn
                    FROM order_items oi
                    JOIN filtered_orders fo ON fo.order_id = oi.order_id
                )
                WHERE rn = 1
            ),
            dedup_addons AS (
                SELECT menu_item_id, price, quantity
                FROM (
                    SELECT
                        oia.menu_item_id,
                        oia.price,
                        oia.quantity,
                        ROW_NUMBER() OVER (
                            PARTITION BY oia.order_item_id, oia.name_raw, oia.quantity, oia.price
                            ORDER BY oia.order_item_addon_id
                        ) AS rn
                    FROM order_item_addons oia
                    JOIN dedup_items di ON di.order_item_id = oia.order_item_id
                )
                WHERE rn = 1
            ),
            item_stats AS (
                SELECT
                    combined.menu_item_id,
                    SUM(combined.total_revenue) AS total_revenue,
                    SUM(combined.sold_as_item) AS sold_as_item,
                    SUM(combined.sold_as_addon) AS sold_as_addon,
                    SUM(combined.total_sold) AS total_sold
                FROM (
                    SELECT
                        di.menu_item_id,
                        COALESCE(di.total_price, 0) AS total_revenue,
                        COALESCE(di.quantity, 0) AS sold_as_item,
                        0 AS sold_as_addon,
                        COALESCE(di.quantity, 0) AS total_sold
                    FROM dedup_items di

                    UNION ALL

                    SELECT
                        da.menu_item_id,
                        COALESCE(da.price, 0) * COALESCE(da.quantity, 0) AS total_revenue,
                        0 AS sold_as_item,
                        COALESCE(da.quantity, 0) AS sold_as_addon,
                        COALESCE(da.quantity, 0) AS total_sold
                    FROM dedup_addons da
                ) combined
                GROUP BY combined.menu_item_id
            )
            SELECT
                mi.menu_item_id,
                mi.name,
                mi.type,
                COALESCE(ist.total_revenue, 0) AS total_revenue,
                COALESCE(ist.total_sold, 0) AS total_sold,
                COALESCE(ist.sold_as_item, 0) AS sold_as_item,
                COALESCE(ist.sold_as_addon, 0) AS sold_as_addon,
                mi.is_active
            FROM menu_items mi
            LEFT JOIN item_stats ist ON ist.menu_item_id = mi.menu_item_id
            {where_clause}
            ORDER BY {safe_sort_column} {safe_sort_direction}
            LIMIT {page_size} OFFSET {offset}
        """
        params = date_params + filter_params
        cursor = conn.execute(data_query, params)
        return pd.DataFrame([dict(row) for row in cursor.fetchall()]), total_count, None
    except Exception as e:
        return None, 0, str(e)

def fetch_menu_types(conn):
    """Fetch distinct menu item types"""
    cursor = conn.execute("SELECT DISTINCT type FROM menu_items ORDER BY type")
    # Fetch results
    rows = cursor.fetchall()
    # sqlite3.Row access by name 'type'
    return [row['type'] for row in rows]

def _synthetic_global_gap_exclusions(conn) -> list[str]:
    """Verified catalog-only rows are not unresolved restaurant assignments."""
    synthetic_candidates = conn.execute(
        """
        SELECT order_item_id, menu_item_id, variant_id
        FROM menu_item_variants
        WHERE is_verified = 1
        """
    ).fetchall()
    key_index = AssignmentKeyIndex(conn)
    return [
        str(order_item_id)
        for order_item_id, menu_item_id, variant_id in synthetic_candidates
        if is_synthetic_mapping(
            str(menu_item_id),
            str(variant_id) if variant_id is not None else None,
            str(order_item_id),
        )
        and not has_local_pos_backing(conn, str(order_item_id), key_index=key_index)
    ]


def fetch_resolution_counts(
    conn, *, include_global_identity_gaps: bool = False
) -> dict[str, int]:
    """Return a disjoint partition of local mapping rows for the resolution UI."""
    ignored_synthetic_ids = (
        _synthetic_global_gap_exclusions(conn)
        if include_global_identity_gaps
        else []
    )
    synthetic_gap_filter = ""
    params: list[Any] = [1 if include_global_identity_gaps else 0]
    if ignored_synthetic_ids:
        placeholders = ", ".join("?" for _ in ignored_synthetic_ids)
        synthetic_gap_filter = f"AND mv.order_item_id NOT IN ({placeholders})"
        params.extend(ignored_synthetic_ids)

    row = conn.execute(
        f"""
        WITH classified AS (
            SELECT CASE
                WHEN mv.is_verified = 0 THEN 'local_unverified'
                WHEN ? = 1
                     AND (gil.global_menu_item_id IS NULL
                          OR gvl.global_variant_id IS NULL)
                     {synthetic_gap_filter}
                    THEN 'globally_unlinked'
                ELSE 'mapped_verified'
            END AS resolution_state
            FROM menu_item_variants mv
            LEFT JOIN menu_item_global_links gil
                ON gil.local_menu_item_id = mv.menu_item_id
            LEFT JOIN variant_global_links gvl
                ON gvl.local_variant_id = mv.variant_id
        )
        SELECT
            COALESCE(SUM(resolution_state = 'local_unverified'), 0),
            COALESCE(SUM(resolution_state = 'globally_unlinked'), 0),
            COALESCE(SUM(resolution_state = 'mapped_verified'), 0)
        FROM classified
        """,
        params,
    ).fetchone()
    return {
        "local_unverified": int(row[0] or 0),
        "globally_unlinked": int(row[1] or 0),
        "mapped_verified": int(row[2] or 0),
    }


def fetch_unverified_items(conn, *, include_global_identity_gaps: bool = False):
    """Fetch unresolved menu item + variant rows for the resolutions workflow."""
    ignored_synthetic_ids = (
        _synthetic_global_gap_exclusions(conn)
        if include_global_identity_gaps
        else []
    )

    synthetic_gap_filter = ""
    params: list[Any] = [1 if include_global_identity_gaps else 0]
    if ignored_synthetic_ids:
        placeholders = ", ".join("?" for _ in ignored_synthetic_ids)
        synthetic_gap_filter = f"AND mv.order_item_id NOT IN ({placeholders})"
        params.extend(ignored_synthetic_ids)

    query = f"""
        WITH unresolved_variants AS (
            SELECT
                mv.menu_item_id,
                mv.variant_id,
                MIN(mv.is_verified) AS is_verified,
                MIN(
                    CASE
                        WHEN gil.global_menu_item_id IS NOT NULL
                         AND gvl.global_variant_id IS NOT NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS has_complete_global_identity,
                COUNT(*) AS unresolved_mapping_rows
            FROM menu_item_variants mv
            LEFT JOIN menu_item_global_links gil
                ON gil.local_menu_item_id = mv.menu_item_id
            LEFT JOIN variant_global_links gvl
                ON gvl.local_variant_id = mv.variant_id
            WHERE mv.is_verified = 0
               OR (
                    ? = 1
                    AND (gil.global_menu_item_id IS NULL OR gvl.global_variant_id IS NULL)
                    {synthetic_gap_filter}
               )
            GROUP BY mv.menu_item_id, mv.variant_id
        ),
        order_item_usage AS (
            SELECT
                oi.menu_item_id,
                oi.variant_id,
                MIN(oi.name_raw) AS sample_order_name,
                COUNT(*) AS order_item_rows,
                COALESCE(SUM(oi.quantity), 0) AS order_item_qty
            FROM order_items oi
            GROUP BY oi.menu_item_id, oi.variant_id
        ),
        addon_usage AS (
            SELECT
                oa.menu_item_id,
                oa.variant_id,
                MIN(oa.name_raw) AS sample_addon_name,
                COUNT(*) AS addon_rows,
                COALESCE(SUM(oa.quantity), 0) AS addon_qty
            FROM order_item_addons oa
            GROUP BY oa.menu_item_id, oa.variant_id
        )
        SELECT
            m.menu_item_id,
            m.name,
            m.type,
            m.created_at,
            m.suggestion_id,
            s.name AS suggestion_name,
            s.type AS suggestion_type,
            uv.variant_id AS source_variant_id,
            uv.is_verified,
            uv.has_complete_global_identity,
            COALESCE(v.variant_name, 'UNKNOWN') AS source_variant_name,
            COALESCE(oi.sample_order_name, au.sample_addon_name) AS sample_order_name,
            uv.unresolved_mapping_rows,
            COALESCE(oi.order_item_rows, 0) AS order_item_rows,
            COALESCE(oi.order_item_qty, 0) AS order_item_qty,
            COALESCE(au.addon_rows, 0) AS addon_rows,
            COALESCE(au.addon_qty, 0) AS addon_qty
        FROM unresolved_variants uv
        JOIN menu_items m ON uv.menu_item_id = m.menu_item_id
        LEFT JOIN menu_items s ON m.suggestion_id = s.menu_item_id
        LEFT JOIN variants v ON uv.variant_id = v.variant_id
        LEFT JOIN order_item_usage oi
            ON uv.menu_item_id = oi.menu_item_id AND uv.variant_id = oi.variant_id
        LEFT JOIN addon_usage au
            ON uv.menu_item_id = au.menu_item_id AND uv.variant_id = au.variant_id
        ORDER BY m.name, v.variant_name
    """
    cursor = conn.execute(query, params)
    df = pd.DataFrame([dict(row) for row in cursor.fetchall()])
    if df.empty:
        return df

    cursor = conn.execute(
        """
        SELECT menu_item_id, variant_id, order_item_id
        FROM menu_item_variants
        WHERE is_verified = 0
        """
    )
    addon_gap_pairs = {
        (str(mid), str(vid))
        for mid, vid, oid in cursor.fetchall()
        if synthetic_mapping_kind(str(mid), str(vid), str(oid)) == "addon_seeded"
    }

    df["resolution_kind"] = df.apply(
        lambda row: (
            "global_identity_gap"
            if bool(row["is_verified"])
            else (
                "addon_gap"
                if (str(row["menu_item_id"]), str(row["source_variant_id"])) in addon_gap_pairs
                else "unverified_mapping"
            )
        ),
        axis=1,
    )
    resolution_pairs = {
        (
            str(row["menu_item_id"]),
            None
            if pd.isna(row["source_variant_id"])
            else str(row["source_variant_id"]),
        )
        for _, row in df.iterrows()
    }
    sorted_resolution_pairs = sorted(
        resolution_pairs,
        key=lambda candidate: (candidate[0], candidate[1] or ""),
    )
    assignment_keys: dict[tuple[str, Optional[str]], list[str]] = {}
    for offset in range(
        0, len(sorted_resolution_pairs), ASSIGNMENT_PAIR_QUERY_CHUNK_SIZE
    ):
        pair_batch = sorted_resolution_pairs[
            offset : offset + ASSIGNMENT_PAIR_QUERY_CHUNK_SIZE
        ]
        pair_clauses = " OR ".join(
            "(menu_item_id = ? AND variant_id IS ?)" for _ in pair_batch
        )
        pair_params = [value for pair in pair_batch for value in pair]
        for menu_item_id, variant_id, order_item_id in conn.execute(
            f"""
            SELECT menu_item_id, variant_id, order_item_id
            FROM menu_item_variants
            WHERE {pair_clauses}
            ORDER BY order_item_id
            """,
            pair_params,
        ).fetchall():
            key = (
                str(menu_item_id),
                str(variant_id) if variant_id is not None else None,
            )
            assignment_keys.setdefault(key, []).append(str(order_item_id))
    df["assignment_order_item_ids"] = df.apply(
        lambda row: assignment_keys.get(
            (
                str(row["menu_item_id"]),
                str(row["source_variant_id"])
                if not pd.isna(row["source_variant_id"])
                else None,
            ),
            [],
        ),
        axis=1,
    )
    return df

def fetch_menu_matrix(conn):
    """Fetch the full menu matrix as unique menu item + variant pairs."""
    query = """
        SELECT 
            mi.name,
            mi.type,
            v.variant_name,
            MIN(miv.price) AS price,
            MIN(miv.is_active) AS is_active,
            MIN(miv.addon_eligible) AS addon_eligible,
            MIN(miv.delivery_eligible) AS delivery_eligible,
            MIN(miv.is_verified) AS is_verified,
            miv.menu_item_id,
            miv.variant_id,
            COUNT(*) AS mapping_count,
            COALESCE(MIN(oc.n), 0) AS order_count
        FROM menu_item_variants miv
        JOIN menu_items mi ON miv.menu_item_id = mi.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        LEFT JOIN (
            SELECT menu_item_id, variant_id, COUNT(*) AS n
            FROM (
                SELECT menu_item_id, variant_id FROM order_items
                UNION ALL
                SELECT menu_item_id, variant_id FROM order_item_addons
            )
            GROUP BY menu_item_id, variant_id
        ) oc ON oc.menu_item_id = miv.menu_item_id AND oc.variant_id = miv.variant_id
        GROUP BY mi.name, mi.type, v.variant_name, miv.menu_item_id, miv.variant_id
        ORDER BY mi.type, mi.name, v.variant_name
    """
    cursor = conn.execute(query)
    return pd.DataFrame([dict(row) for row in cursor.fetchall()])


def fetch_group_menu_catalog(
    conn, menu_group_id: str, restaurant_id: Optional[str] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Return active group-owned items and variants, never restaurant analytics."""
    items = [
        {
            **dict(row),
            "is_verified": bool(row[4]),
        }
        for row in conn.execute(
            """
            SELECT i.global_menu_item_id, i.canonical_name, i.canonical_type,
                   COUNT(r.rule_id) AS active_pos_rules, i.is_verified,
                   i.server_revision, i.updated_at
            FROM global_menu_items i
            LEFT JOIN global_menu_mapping_rules r
              ON r.target_global_menu_item_id=i.global_menu_item_id
             AND r.menu_group_id=i.menu_group_id
             AND r.locator_scope='restaurant'
             AND r.locator_kind IN ('pos-item', 'pos-addon')
             AND r.lifecycle_state='active'
             AND (? IS NULL OR r.restaurant_id=?)
            WHERE i.menu_group_id=? AND i.lifecycle_state='active'
            GROUP BY i.global_menu_item_id, i.canonical_name, i.canonical_type,
                     i.is_verified, i.server_revision, i.updated_at
            ORDER BY i.canonical_type, i.canonical_name, i.global_menu_item_id
            """,
            (restaurant_id, restaurant_id, menu_group_id),
        ).fetchall()
    ]
    variants = [
        {
            **dict(row),
            "is_verified": bool(row[5]),
        }
        for row in conn.execute(
            """
            SELECT global_variant_id, canonical_name, description, unit, value,
                   is_verified, server_revision, updated_at
            FROM global_variants
            WHERE menu_group_id=? AND lifecycle_state='active'
            ORDER BY canonical_name, unit, value, global_variant_id
            """,
            (menu_group_id,),
        ).fetchall()
    ]
    return {"items": items, "variants": variants}


def fetch_group_menu_matrix(
    conn, menu_group_id: str, restaurant_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return restaurant-scoped POS mapping rules for the selected member."""
    rows = conn.execute(
        """
        SELECT r.rule_id,
               REPLACE(r.locator_kind, '-', '_') AS locator_type,
               r.locator_value,
               r.restaurant_id,
               r.target_global_menu_item_id AS global_menu_item_id,
               r.target_global_variant_id AS global_variant_id,
               i.canonical_name AS name,
               i.canonical_type AS type,
               COALESCE(v.canonical_name, 'No variant') AS variant_name,
               v.unit,
               v.value,
               r.is_verified,
               r.server_revision,
               miv.menu_item_id,
               miv.variant_id
        FROM global_menu_mapping_rules r
        JOIN global_menu_items i
          ON i.global_menu_item_id=r.target_global_menu_item_id
         AND i.menu_group_id=r.menu_group_id
         AND i.lifecycle_state='active'
        LEFT JOIN global_variants v
          ON v.global_variant_id=r.target_global_variant_id
         AND v.menu_group_id=r.menu_group_id
         AND v.lifecycle_state='active'
        LEFT JOIN menu_item_variants miv ON miv.order_item_id=r.locator_value
        WHERE r.menu_group_id=?
          AND r.locator_scope='restaurant'
          AND r.locator_kind IN ('pos-item', 'pos-addon')
          AND r.lifecycle_state='active'
          AND (? IS NULL OR r.restaurant_id=?)
        ORDER BY r.locator_kind, r.locator_value, r.rule_id
        """,
        (menu_group_id, restaurant_id, restaurant_id),
    ).fetchall()
    return [
        {
            **dict(row),
            "is_verified": bool(row[11]),
        }
        for row in rows
    ]


def _menu_summary_window_specs(as_of_date: str):
    """
    Rolling windows are inclusive business dates ending at as_of_date.
    month_1 / month_2 use 30 and 60 calendar-day spans in business-date space.
    """
    end = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    specs = [
        ("day_1", 1),
        ("day_2", 2),
        ("day_3", 3),
        ("day_5", 5),
        ("day_7", 7),
        ("day_14", 14),
        ("month_1", 30),
        ("month_2", 60),
    ]
    windows = []
    params = []
    for key, n in specs:
        start = (end - timedelta(days=n - 1)).isoformat()
        windows.append((key, start, as_of_date))
        params.extend([start, as_of_date])
    return windows, params


def _menu_summary_norm_unit_sql() -> str:
    """Normalize variant.unit for rollup columns (COUNT-style → PIECES)."""
    return """
        CASE
            WHEN UPPER(TRIM(COALESCE(v.unit, ''))) IN ('COUNT', 'PIECE', 'PIECES', 'PCS') THEN 'PIECES'
            WHEN UPPER(TRIM(COALESCE(v.unit, ''))) = 'ML' THEN 'ML'
            WHEN UPPER(TRIM(COALESCE(v.unit, ''))) = 'GMS' THEN 'GMS'
            WHEN TRIM(COALESCE(v.unit, '')) = '' OR v.unit IS NULL THEN 'UNKNOWN'
            ELSE UPPER(TRIM(v.unit))
        END
    """


_MENU_SUMMARY_SORT_COLUMNS = frozenset(
    {
        "day_1",
        "day_2",
        "day_3",
        "day_5",
        "day_7",
        "day_14",
        "month_1",
        "month_2",
        "lifetime",
    }
)


def fetch_menu_summary_rollups(
    conn,
    mode: str,
    as_of_date: str,
    page: int = 1,
    page_size: int = 50,
    name_search: Optional[str] = None,
    sort_by: str = "lifetime",
    sort_desc: bool = True,
):
    """
    Rolling sales by menu item for the Menu Summary page.

    mode:
      - quantity: one row per menu item; values are total line quantity
        (deduped order lines + add-ons), sum(quantity).
      - volume: one row per (menu item, normalized unit); values are
        sum(COALESCE(variant.value, 0) * quantity) for that unit only.
    """
    try:
        finite_windows, window_params = _menu_summary_window_specs(as_of_date)
        life_param = [as_of_date]

        if mode == "quantity":
            measure_sql = "CAST(COALESCE(e.qty, 0) AS REAL)"
            group_sql = "mi.menu_item_id, mi.name, mi.type"
            select_extra = "mi.menu_item_id, mi.name, mi.type"
        elif mode == "volume":
            measure_sql = "(COALESCE(v.value, 0) * CAST(COALESCE(e.qty, 0) AS REAL))"
            nu = _menu_summary_norm_unit_sql()
            group_sql = f"mi.menu_item_id, mi.name, mi.type, ({nu.strip()})"
            select_extra = f"mi.menu_item_id, mi.name, mi.type, ({nu.strip()}) AS unit"
        else:
            return None, 0, "mode must be 'volume' or 'quantity'"

        sum_fragments = []
        for key, start, end in finite_windows:
            sum_fragments.append(
                f"SUM(CASE WHEN e.business_date >= ? AND e.business_date <= ? "
                f"THEN {measure_sql} ELSE 0 END) AS {key}"
            )
        sum_fragments.append(
            f"SUM(CASE WHEN e.business_date <= ? THEN {measure_sql} ELSE 0 END) AS lifetime"
        )

        sums_sql = ",\n                ".join(sum_fragments)

        search_clause = ""
        search_params: list = []
        if name_search and str(name_search).strip():
            search_clause = "AND UPPER(mi.name) LIKE ?"
            search_params.append(f"%{str(name_search).strip().upper()}%")

        inner_query = f"""
            WITH filtered_orders AS (
                SELECT o.order_id, o.created_on
                FROM orders o
                WHERE o.order_status = 'Success'
            ),
            dedup_items AS (
                SELECT
                    order_item_id,
                    order_id,
                    menu_item_id,
                    quantity AS qty,
                    variant_id,
                    business_date
                FROM (
                    SELECT
                        oi.order_item_id,
                        oi.order_id,
                        oi.menu_item_id,
                        oi.quantity,
                        oi.variant_id,
                        DATE(fo.created_on, '-5 hours') AS business_date,
                        ROW_NUMBER() OVER (
                            PARTITION BY oi.order_id, oi.name_raw, oi.quantity, oi.unit_price
                            ORDER BY oi.order_item_id
                        ) AS rn
                    FROM order_items oi
                    JOIN filtered_orders fo ON fo.order_id = oi.order_id
                )
                WHERE rn = 1
            ),
            dedup_addons AS (
                SELECT
                    menu_item_id,
                    quantity AS qty,
                    variant_id,
                    business_date
                FROM (
                    SELECT
                        oia.menu_item_id,
                        oia.quantity,
                        oia.variant_id,
                        di.business_date,
                        ROW_NUMBER() OVER (
                            PARTITION BY oia.order_item_id, oia.name_raw, oia.quantity, oia.price
                            ORDER BY oia.order_item_addon_id
                        ) AS rn
                    FROM order_item_addons oia
                    JOIN dedup_items di ON di.order_item_id = oia.order_item_id
                )
                WHERE rn = 1
            ),
            events AS (
                SELECT menu_item_id, business_date, qty, variant_id FROM dedup_items
                UNION ALL
                SELECT menu_item_id, business_date, qty, variant_id FROM dedup_addons
            )
            SELECT
                {select_extra},
                {sums_sql}
            FROM events e
            JOIN menu_items mi ON e.menu_item_id = mi.menu_item_id
            LEFT JOIN variants v ON e.variant_id = v.variant_id
            WHERE e.business_date <= ?
            {search_clause}
            GROUP BY {group_sql}
        """

        measure_params = window_params + life_param + life_param
        inner_params = measure_params + search_params

        sort_dir = "DESC" if sort_desc else "ASC"
        safe_sort_col = sort_by if sort_by in _MENU_SUMMARY_SORT_COLUMNS else "lifetime"
        offset = (page - 1) * page_size

        count_sql = f"SELECT COUNT(*) FROM ({inner_query}) AS agg"
        count_cursor = conn.execute(count_sql, inner_params)
        total_count = count_cursor.fetchone()[0]

        data_sql = f"{inner_query} ORDER BY {safe_sort_col} {sort_dir} LIMIT ? OFFSET ?"
        data_params = inner_params + [page_size, offset]
        cursor = conn.execute(data_sql, data_params)
        return pd.DataFrame([dict(row) for row in cursor.fetchall()]), total_count, None
    except Exception as e:
        return None, 0, str(e)


def fetch_menu_items_daily_timeseries(
    conn,
    menu_item_ids: tuple[str, ...],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """
    Per business day, per menu item: quantity, volume, revenue.

    Quantity and volume use the same deduped line + add-on events and volume math
    as ``fetch_menu_summary_rollups`` (variant value × quantity, summed across units).

    Revenue matches ``fetch_menu_items_summary``: deduped line ``total_price`` plus
    add-on ``price * quantity``.
    """
    if not menu_item_ids:
        return pd.DataFrame([]), None
    try:
        placeholders = ",".join(["?"] * len(menu_item_ids))
        date_clauses: list[str] = []
        date_params: list = []
        if start_date:
            date_clauses.append("e.business_date >= ?")
            date_params.append(start_date)
        if end_date:
            date_clauses.append("e.business_date <= ?")
            date_params.append(end_date)
        date_sql = f" AND {' AND '.join(date_clauses)}" if date_clauses else ""

        query = f"""
            WITH filtered_orders AS (
                SELECT o.order_id, o.created_on
                FROM orders o
                WHERE o.order_status = 'Success'
            ),
            dedup_items AS (
                SELECT
                    order_item_id,
                    order_id,
                    menu_item_id,
                    quantity AS qty,
                    variant_id,
                    business_date,
                    COALESCE(total_price, 0) AS line_revenue
                FROM (
                    SELECT
                        oi.order_item_id,
                        oi.order_id,
                        oi.menu_item_id,
                        oi.quantity,
                        oi.variant_id,
                        DATE(fo.created_on, '-5 hours') AS business_date,
                        COALESCE(oi.total_price, 0) AS total_price,
                        ROW_NUMBER() OVER (
                            PARTITION BY oi.order_id, oi.name_raw, oi.quantity, oi.unit_price
                            ORDER BY oi.order_item_id
                        ) AS rn
                    FROM order_items oi
                    JOIN filtered_orders fo ON fo.order_id = oi.order_id
                )
                WHERE rn = 1
            ),
            dedup_addons AS (
                SELECT
                    menu_item_id,
                    quantity AS qty,
                    variant_id,
                    business_date,
                    (COALESCE(price, 0) * COALESCE(quantity, 0)) AS line_revenue
                FROM (
                    SELECT
                        oia.menu_item_id,
                        oia.quantity,
                        oia.variant_id,
                        di.business_date,
                        oia.price,
                        ROW_NUMBER() OVER (
                            PARTITION BY oia.order_item_id, oia.name_raw, oia.quantity, oia.price
                            ORDER BY oia.order_item_addon_id
                        ) AS rn
                    FROM order_item_addons oia
                    JOIN dedup_items di ON di.order_item_id = oia.order_item_id
                )
                WHERE rn = 1
            ),
            events AS (
                SELECT menu_item_id, business_date, qty, variant_id, line_revenue FROM dedup_items
                UNION ALL
                SELECT menu_item_id, business_date, qty, variant_id, line_revenue FROM dedup_addons
            )
            SELECT
                e.menu_item_id,
                mi.name AS menu_item_name,
                e.business_date AS date,
                SUM(CAST(COALESCE(e.qty, 0) AS REAL)) AS quantity,
                SUM(COALESCE(v.value, 0) * CAST(COALESCE(e.qty, 0) AS REAL)) AS volume,
                SUM(COALESCE(e.line_revenue, 0)) AS revenue
            FROM events e
            JOIN menu_items mi ON mi.menu_item_id = e.menu_item_id
            LEFT JOIN variants v ON e.variant_id = v.variant_id
            WHERE e.menu_item_id IN ({placeholders})
            {date_sql}
            GROUP BY e.menu_item_id, mi.name, e.business_date
            ORDER BY e.business_date ASC, mi.name ASC
        """
        params: list = list(menu_item_ids) + date_params
        cursor = conn.execute(query, params)
        return pd.DataFrame([dict(row) for row in cursor.fetchall()]), None
    except Exception as e:
        return None, str(e)
