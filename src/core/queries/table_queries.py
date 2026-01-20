import pandas as pd
from psycopg2.extras import RealDictCursor

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
        
        # Build WHERE clause from filters
        where_clause = ""
        params = []
        if filters:
            conditions = []
            for col, val in filters.items():
                if val:
                    # Use CAST to TEXT to allow searching across IDs and numbers
                    conditions.append(f"CAST({col} AS TEXT) ILIKE %s")
                    params.append(f"%{val}%")
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)
        
        # Get total count with filters
        count_query = f"SELECT COUNT(*) as count FROM {table_name} {where_clause}"
        cursor = conn.cursor()
        cursor.execute(count_query, params)
        total_count = cursor.fetchone()[0]
        cursor.close()
        
        # Calculate offset
        offset = (page - 1) * page_size
        
        # Build query
        query = f"""
            SELECT * FROM {table_name}
            {where_clause}
            ORDER BY {sort_column} {sort_direction}
            LIMIT {page_size} OFFSET {offset}
        """
        
        # execute_query uses pd.read_sql_query which doesn't easily take params for the WHERE clause
        # so we'll use a cursor and then create the DF
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query, params)
        df = pd.DataFrame(cursor.fetchall())
        cursor.close()
        
        # Post-processing: Convert Timezones to IST
        if not df.empty:
            # 1. Convert columns that pandas detected as datetimetz (unlikely with cursor.fetchall unless converted)
            # 2. Convert object columns that hold datetime objects (common with psycopg2)
            try:
                for col in df.columns:
                    # Check if the first non-null value is a datetime with timezone
                    first_valid = df[col].dropna().iloc[0] if not df[col].dropna().empty else None
                    
                    if hasattr(first_valid, 'tzinfo') and first_valid.tzinfo is not None:
                        # It is timezone aware. Convert to IST.
                        # We use apply because directly creating a Series from mixed objects can be tricky, 
                        # but standard pandas methods are preferred if dtype is already proper.
                        
                        # If dtype is object, we can use apply
                        if df[col].dtype == 'object':
                            # ZoneInfo objects from Python 3.9+ or pytz 
                            from zoneinfo import ZoneInfo
                            ist = ZoneInfo('Asia/Kolkata')
                            df[col] = df[col].apply(lambda x: x.astimezone(ist) if x and hasattr(x, 'astimezone') else x)
                        else:
                            # If it's already a localized datetime64
                            df[col] = df[col].dt.tz_convert('Asia/Kolkata')
                            
            except Exception as e:
                # Log error but don't fail the entire fetch
                print(f"Timezone conversion warning: {e}")
        
        return df, total_count, None
    except Exception as e:
        return None, 0, str(e)

def execute_raw_query(conn, query, limit=None):
    """Execute generic SQL query"""
    try:
        # Check if it's a SELECT/WITH query
        query_type = query.strip().split()[0].upper() if query.strip() else ""
        is_read_only = query_type in ("SELECT", "WITH", "SHOW", "EXPLAIN")
        
        if is_read_only:
            # Only append LIMIT if not already present and it is a SELECT/WITH
            if limit and "LIMIT" not in query.upper():
                # Strip both semicolon and whitespace
                query = f"{query.rstrip(';').strip()} LIMIT {limit}"
            
            df = pd.read_sql_query(query, conn)
            return df, None
        else:
            # For ALTER, UPDATE, INSERT, etc.
            cursor = conn.cursor()
            cursor.execute(query)
            conn.commit()
            cursor.close()
            # Return a status message for UI
            return pd.DataFrame([{"Status": "Success", "Message": f"{query_type} command completed successfully"}]), None
            
    except Exception as e:
        try:
            conn.rollback()
        except:
            pass
        return None, str(e)
