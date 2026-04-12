from src.core.queries.customer_query_utils import (
    active_customer_filter,
    format_customer_address,
    normalize_phone,
    normalize_text,
)


def fetch_customer_summary(conn, customer_id: str):
    row = conn.execute(
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
            (SELECT COUNT(*) FROM customer_addresses ca WHERE ca.customer_id = c.customer_id) as address_count,
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
        LEFT JOIN primary_address pa ON pa.customer_id = c.customer_id AND pa.rn = 1
        WHERE c.customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
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
        "name_norm": normalize_text(summary.get("name")),
        "phone_norm": normalize_phone(summary.get("phone")),
        "address_norm": normalize_text(address_summary),
        "feature_text": " | ".join(
            part for part in [
                normalize_text(summary.get("name")),
                normalize_phone(summary.get("phone")),
                normalize_text(address_summary),
            ] if part
        ) or f"customer_{summary['customer_id']}",
    }


def fetch_active_similarity_population(conn):
    rows = conn.execute(
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
        LEFT JOIN primary_address pa ON pa.customer_id = c.customer_id AND pa.rn = 1
        WHERE {active_customer_filter("c")}
        """
    ).fetchall()
    population = []
    for row in rows:
        record = dict(row)
        address_summary = format_customer_address(record) or record.get("legacy_address") or None
        population.append({
            "customer_id": str(record["customer_id"]),
            "name": record.get("name") or f"Customer {record['customer_id']}",
            "phone": record.get("phone"),
            "address": address_summary,
            "total_orders": int(record.get("total_orders") or 0),
            "total_spent": float(record.get("total_spent") or 0.0),
            "last_order_date": record.get("last_order_date"),
            "is_verified": bool(record.get("is_verified")),
            "name_norm": normalize_text(record.get("name")),
            "phone_norm": normalize_phone(record.get("phone")),
            "address_norm": normalize_text(address_summary),
            "feature_text": " | ".join(
                part for part in [
                    normalize_text(record.get("name")),
                    normalize_phone(record.get("phone")),
                    normalize_text(address_summary),
                ] if part
            ) or f"customer_{record['customer_id']}",
        })
    return population

