import psycopg2

def check_schema_exists(conn):
    """Check if the required schema tables (specifically 'orders') exist in the public schema."""
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'orders'
            )
        """)
        exists = cursor.fetchone()[0]
        cursor.close()
        return exists
    except:
        return False
