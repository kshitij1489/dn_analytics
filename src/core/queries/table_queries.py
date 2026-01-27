import pandas as pd

def fetch_paginated_table(conn, table_name, page=1, page_size=50, sort_column=None, sort_direction='DESC', filters=None):
    """
    Get paginated table data with optional multi-column filtering.
    Returns (DataFrame, TotalCount, ErrorMessage)
    """
    try:
        # Determine sort column based on table if not provided
        if sort_column is None:
            if table_name == 'orders':
                sort_column = 'created_on'
            elif table_name == 'order_items':
                sort_column = 'created_at'
            elif table_name == 'customers':
                sort_column = 'last_order_date'
            elif table_name == 'menu_items':
                sort_column = 'name'
                sort_direction = 'ASC'
            elif table_name == 'variants':
                sort_column = 'variant_name'
                sort_direction = 'ASC'
            elif table_name == 'restaurants':
                sort_column = 'restaurant_id'
            elif table_name == 'order_taxes':
                sort_column = 'created_at'
            elif table_name == 'order_discounts':
                sort_column = 'created_at'
            else:
                sort_column = 'created_at'
            
        # Build WHERE clause
        where_clause = ""
        params = []
        if filters:
            conditions = []
            for col, val in filters.items():
                if val:
                    # SQLite: CAST(? AS TEXT) LIKE ?
                    # Use LIKE for "ILIKE" behavior if case_sensitive_like OFF (default usually)
                    # Or force UPPER comparison
                    conditions.append(f"UPPER(CAST({col} AS TEXT)) LIKE ?")
                    params.append(f"%{val.upper()}%")
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)
        
        # Count
        count_query = f"SELECT COUNT(*) as count FROM {table_name} {where_clause}"
        cursor = conn.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        
        # Offset
        offset = (page - 1) * page_size
        
        # Data Query
        query = f"""
            SELECT * FROM {table_name}
            {where_clause}
            ORDER BY {sort_column} {sort_direction}
            LIMIT {page_size} OFFSET {offset}
        """
        
        cursor = conn.execute(query, params)
        df = pd.DataFrame([dict(row) for row in cursor.fetchall()])
        
        return df, total_count, None
    except Exception as e:
        return None, 0, str(e)

def execute_raw_query(conn, query, limit=None):
    """Execute generic SQL query"""
    try:
        # Check if it's a SELECT/WITH query
        query_type = query.strip().split()[0].upper() if query.strip() else ""
        is_read_only = query_type in ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
        
        if is_read_only:
            if limit and "LIMIT" not in query.upper():
                query = f"{query.rstrip(';').strip()} LIMIT {limit}"
            
            # Using pandas read_sql_query for convenience
            df = pd.read_sql_query(query, conn)
            return df, None
        else:
            # write
            conn.execute(query)
            conn.commit()
            return pd.DataFrame([{"Status": "Success", "Message": f"{query_type} command completed successfully"}]), None
            
    except Exception as e:
        return None, str(e)
