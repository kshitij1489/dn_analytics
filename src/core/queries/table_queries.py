import pandas as pd


TABLE_QUERY_CONFIG = {
    "orders": {
        "select_sql": "SELECT t.*",
        "from_sql": "FROM orders t",
        "default_sort": "created_on",
        "default_direction": "DESC",
        "sort_columns": {
            "order_id": "t.order_id",
            "petpooja_order_id": "t.petpooja_order_id",
            "stream_id": "t.stream_id",
            "event_id": "t.event_id",
            "aggregate_id": "t.aggregate_id",
            "customer_id": "t.customer_id",
            "restaurant_id": "t.restaurant_id",
            "occurred_at": "t.occurred_at",
            "created_on": "t.created_on",
            "order_type": "t.order_type",
            "order_from": "t.order_from",
            "sub_order_type": "t.sub_order_type",
            "order_from_id": "t.order_from_id",
            "order_status": "t.order_status",
            "biller": "t.biller",
            "assignee": "t.assignee",
            "table_no": "t.table_no",
            "token_no": "t.token_no",
            "no_of_persons": "t.no_of_persons",
            "customer_invoice_id": "t.customer_invoice_id",
            "core_total": "t.core_total",
            "tax_total": "t.tax_total",
            "discount_total": "t.discount_total",
            "delivery_charges": "t.delivery_charges",
            "packaging_charge": "t.packaging_charge",
            "service_charge": "t.service_charge",
            "round_off": "t.round_off",
            "total": "t.total",
            "comment": "t.comment",
            "created_at": "t.created_at",
            "updated_at": "t.updated_at",
        },
        "filter_columns": {
            "order_id": "t.order_id",
            "petpooja_order_id": "t.petpooja_order_id",
            "stream_id": "t.stream_id",
            "event_id": "t.event_id",
            "aggregate_id": "t.aggregate_id",
            "customer_id": "t.customer_id",
            "restaurant_id": "t.restaurant_id",
            "created_on": "t.created_on",
            "order_type": "t.order_type",
            "order_from": "t.order_from",
            "sub_order_type": "t.sub_order_type",
            "order_from_id": "t.order_from_id",
            "order_status": "t.order_status",
            "biller": "t.biller",
            "assignee": "t.assignee",
            "table_no": "t.table_no",
            "token_no": "t.token_no",
            "customer_invoice_id": "t.customer_invoice_id",
            "comment": "t.comment",
        },
        "search_columns": [
            "t.order_id",
            "t.petpooja_order_id",
            "t.stream_id",
            "t.event_id",
            "t.aggregate_id",
            "t.customer_id",
            "t.restaurant_id",
            "t.created_on",
            "t.order_type",
            "t.order_from",
            "t.sub_order_type",
            "t.order_from_id",
            "t.order_status",
            "t.biller",
            "t.assignee",
            "t.table_no",
            "t.token_no",
            "t.no_of_persons",
            "t.customer_invoice_id",
            "t.core_total",
            "t.tax_total",
            "t.discount_total",
            "t.delivery_charges",
            "t.packaging_charge",
            "t.service_charge",
            "t.round_off",
            "t.total",
            "t.comment",
        ],
    },
    "order_items": {
        "select_sql": "SELECT t.*, o.created_on",
        "from_sql": "FROM order_items t JOIN orders o ON t.order_id = o.order_id",
        "default_sort": "created_at",
        "default_direction": "DESC",
        "sort_columns": {
            "order_item_id": "t.order_item_id",
            "order_id": "t.order_id",
            "created_on": "o.created_on",
            "created_at": "t.created_at",
            "menu_item_id": "t.menu_item_id",
            "variant_id": "t.variant_id",
            "petpooja_itemid": "t.petpooja_itemid",
            "itemcode": "t.itemcode",
            "name_raw": "t.name_raw",
            "category_name": "t.category_name",
            "quantity": "t.quantity",
            "unit_price": "t.unit_price",
            "total_price": "t.total_price",
            "tax_amount": "t.tax_amount",
            "discount_amount": "t.discount_amount",
            "specialnotes": "t.specialnotes",
            "sap_code": "t.sap_code",
            "vendoritemcode": "t.vendoritemcode",
            "match_confidence": "t.match_confidence",
            "match_method": "t.match_method",
            "updated_at": "t.updated_at",
        },
        "filter_columns": {
            "order_item_id": "t.order_item_id",
            "order_id": "t.order_id",
            "created_on": "o.created_on",
            "menu_item_id": "t.menu_item_id",
            "variant_id": "t.variant_id",
            "petpooja_itemid": "t.petpooja_itemid",
            "itemcode": "t.itemcode",
            "name_raw": "t.name_raw",
            "category_name": "t.category_name",
            "specialnotes": "t.specialnotes",
            "sap_code": "t.sap_code",
            "vendoritemcode": "t.vendoritemcode",
            "match_method": "t.match_method",
        },
        "search_columns": [
            "t.order_item_id",
            "t.order_id",
            "o.created_on",
            "t.menu_item_id",
            "t.variant_id",
            "t.petpooja_itemid",
            "t.itemcode",
            "t.name_raw",
            "t.category_name",
            "t.quantity",
            "t.unit_price",
            "t.total_price",
            "t.tax_amount",
            "t.discount_amount",
            "t.specialnotes",
            "t.sap_code",
            "t.vendoritemcode",
            "t.match_confidence",
            "t.match_method",
        ],
    },
    "customers": {
        "select_sql": "SELECT t.*",
        "from_sql": """
            FROM (
                SELECT c.*
                FROM customers c
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM customer_merge_history cmh
                    WHERE cmh.source_customer_id = c.customer_id
                      AND cmh.undone_at IS NULL
                )
            ) t
        """,
        "default_sort": "last_order_date",
        "default_direction": "DESC",
        "sort_columns": {
            "customer_id": "t.customer_id",
            "customer_identity_key": "t.customer_identity_key",
            "name": "t.name",
            "name_normalized": "t.name_normalized",
            "phone": "t.phone",
            "address": "t.address",
            "gstin": "t.gstin",
            "first_order_date": "t.first_order_date",
            "last_order_date": "t.last_order_date",
            "total_orders": "t.total_orders",
            "total_spent": "t.total_spent",
            "is_verified": "t.is_verified",
            "created_at": "t.created_at",
            "updated_at": "t.updated_at",
        },
        "filter_columns": {
            "customer_id": "t.customer_id",
            "customer_identity_key": "t.customer_identity_key",
            "name": "t.name",
            "name_normalized": "t.name_normalized",
            "phone": "t.phone",
            "address": "t.address",
            "gstin": "t.gstin",
        },
        "search_columns": [
            "t.customer_id",
            "t.customer_identity_key",
            "t.name",
            "t.name_normalized",
            "t.phone",
            "t.address",
            "t.gstin",
        ],
    },
    "restaurants": {
        "select_sql": "SELECT t.*",
        "from_sql": "FROM restaurants t",
        "default_sort": "restaurant_id",
        "default_direction": "DESC",
        "sort_columns": {
            "restaurant_id": "t.restaurant_id",
            "petpooja_restid": "t.petpooja_restid",
            "name": "t.name",
            "address": "t.address",
            "contact_information": "t.contact_information",
            "is_active": "t.is_active",
            "created_at": "t.created_at",
            "updated_at": "t.updated_at",
        },
        "filter_columns": {
            "restaurant_id": "t.restaurant_id",
            "petpooja_restid": "t.petpooja_restid",
            "name": "t.name",
            "address": "t.address",
            "contact_information": "t.contact_information",
        },
        "search_columns": [
            "t.restaurant_id",
            "t.petpooja_restid",
            "t.name",
            "t.address",
            "t.contact_information",
        ],
    },
    "order_taxes": {
        "select_sql": "SELECT t.*, o.created_on",
        "from_sql": "FROM order_taxes t JOIN orders o ON t.order_id = o.order_id",
        "default_sort": "created_at",
        "default_direction": "DESC",
        "sort_columns": {
            "order_tax_id": "t.order_tax_id",
            "order_id": "t.order_id",
            "created_on": "o.created_on",
            "created_at": "t.created_at",
            "tax_title": "t.tax_title",
            "tax_rate": "t.tax_rate",
            "tax_type": "t.tax_type",
            "tax_amount": "t.tax_amount",
        },
        "filter_columns": {
            "order_tax_id": "t.order_tax_id",
            "order_id": "t.order_id",
            "created_on": "o.created_on",
            "tax_title": "t.tax_title",
            "tax_rate": "t.tax_rate",
            "tax_type": "t.tax_type",
            "tax_amount": "t.tax_amount",
        },
        "search_columns": [
            "t.order_tax_id",
            "t.order_id",
            "o.created_on",
            "t.tax_title",
            "t.tax_rate",
            "t.tax_type",
            "t.tax_amount",
        ],
    },
    "order_discounts": {
        "select_sql": "SELECT t.*, o.created_on",
        "from_sql": "FROM order_discounts t JOIN orders o ON t.order_id = o.order_id",
        "default_sort": "created_at",
        "default_direction": "DESC",
        "sort_columns": {
            "order_discount_id": "t.order_discount_id",
            "order_id": "t.order_id",
            "created_on": "o.created_on",
            "created_at": "t.created_at",
            "discount_title": "t.discount_title",
            "discount_type": "t.discount_type",
            "discount_rate": "t.discount_rate",
            "discount_amount": "t.discount_amount",
        },
        "filter_columns": {
            "order_discount_id": "t.order_discount_id",
            "order_id": "t.order_id",
            "created_on": "o.created_on",
            "discount_title": "t.discount_title",
            "discount_type": "t.discount_type",
            "discount_rate": "t.discount_rate",
            "discount_amount": "t.discount_amount",
        },
        "search_columns": [
            "t.order_discount_id",
            "t.order_id",
            "o.created_on",
            "t.discount_title",
            "t.discount_type",
            "t.discount_rate",
            "t.discount_amount",
        ],
    },
    "menu_items_summary_view": {
        "select_sql": "SELECT t.*",
        "from_sql": "FROM menu_items_summary_view t",
        "default_sort": "name",
        "default_direction": "ASC",
        "sort_columns": {
            "menu_item_id": "t.menu_item_id",
            "name": "t.name",
            "type": "t.type",
            "total_revenue": "t.total_revenue",
            "total_sold": "t.total_sold",
            "sold_as_item": "t.sold_as_item",
            "sold_as_addon": "t.sold_as_addon",
            "is_active": "t.is_active",
        },
        "filter_columns": {
            "menu_item_id": "t.menu_item_id",
            "name": "t.name",
            "type": "t.type",
            "is_active": "t.is_active",
        },
        "search_columns": [
            "t.menu_item_id",
            "t.name",
            "t.type",
        ],
    },
    "variants": {
        "select_sql": "SELECT t.*",
        "from_sql": "FROM variants t",
        "default_sort": "variant_name",
        "default_direction": "ASC",
        "sort_columns": {
            "variant_id": "t.variant_id",
            "variant_name": "t.variant_name",
            "description": "t.description",
            "unit": "t.unit",
            "value": "t.value",
            "is_verified": "t.is_verified",
            "created_at": "t.created_at",
            "updated_at": "t.updated_at",
        },
        "filter_columns": {
            "variant_id": "t.variant_id",
            "variant_name": "t.variant_name",
            "description": "t.description",
            "unit": "t.unit",
        },
        "search_columns": [
            "t.variant_id",
            "t.variant_name",
            "t.description",
            "t.unit",
            "t.value",
        ],
    },
}


def _build_like_condition(expression):
    return f"UPPER(CAST({expression} AS TEXT)) LIKE ?"


def _build_where_clause(config, filters=None, search=None):
    conditions = []
    params = []

    for column, value in (filters or {}).items():
        if value in (None, ""):
            continue
        expression = config["filter_columns"].get(column)
        if not expression:
            continue
        conditions.append(_build_like_condition(expression))
        params.append(f"%{str(value).upper()}%")

    normalized_search = (search or "").strip()
    if normalized_search and config.get("search_columns"):
        search_conditions = [_build_like_condition(column) for column in config["search_columns"]]
        conditions.append("(" + " OR ".join(search_conditions) + ")")
        search_param = f"%{normalized_search.upper()}%"
        params.extend([search_param] * len(search_conditions))

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return where_clause, params


def fetch_paginated_table(
    conn,
    table_name,
    page=1,
    page_size=50,
    sort_column=None,
    sort_direction="DESC",
    filters=None,
    search=None,
):
    """
    Get paginated table data with optional column filters and global search.
    Returns (DataFrame, TotalCount, ErrorMessage)
    """
    try:
        config = TABLE_QUERY_CONFIG.get(table_name)
        if not config:
            return None, 0, f"Unsupported table: {table_name}"

        requested_sort = sort_column or config["default_sort"]
        sort_expression = config["sort_columns"].get(
            requested_sort,
            config["sort_columns"][config["default_sort"]],
        )
        safe_sort_direction = "ASC" if str(sort_direction).upper() == "ASC" else "DESC"

        where_clause, params = _build_where_clause(config, filters=filters, search=search)

        count_query = f"SELECT COUNT(*) as count {config['from_sql']} {where_clause}"
        cursor = conn.execute(count_query, params)
        total_count = cursor.fetchone()[0]

        offset = (page - 1) * page_size
        data_query = f"""
            {config['select_sql']}
            {config['from_sql']}
            {where_clause}
            ORDER BY {sort_expression} {safe_sort_direction}
            LIMIT ? OFFSET ?
        """

        data_params = [*params, page_size, offset]
        cursor = conn.execute(data_query, data_params)
        df = pd.DataFrame([dict(row) for row in cursor.fetchall()])

        return df, total_count, None
    except Exception as e:
        return None, 0, str(e)


def execute_raw_query(conn, query, limit=None):
    """Execute generic SQL query"""
    try:
        query_type = query.strip().split()[0].upper() if query.strip() else ""
        is_read_only = query_type in ("SELECT", "WITH", "PRAGMA", "EXPLAIN")

        if is_read_only:
            if limit and "LIMIT" not in query.upper():
                query = f"{query.rstrip(';').strip()} LIMIT {limit}"

            df = pd.read_sql_query(query, conn)
            return df, None

        conn.execute(query)
        conn.commit()
        return pd.DataFrame([{"Status": "Success", "Message": f"{query_type} command completed successfully"}]), None
    except Exception as e:
        return None, str(e)
