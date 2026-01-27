import psycopg2
import os

def get_db_connection(host=None, port=None, database=None, user=None, password=None, db_url=None):
    """
    Create PostgreSQL database connection.
    Prioritizes explicit arguments, then db_url arg, then DB_URL env var, then defaults.
    Returns (connection, status_message) tuple.
    """
    try:
        # 1. Use explicit URL if provided
        if db_url:
            return psycopg2.connect(db_url), "Connected via URL"
        
        # 2. Try environment variable
        env_url = os.environ.get("DB_URL")
        if env_url:
            return psycopg2.connect(env_url), "Connected via DB_URL"
            
        # 3. Use individual params with defaults
        conn = psycopg2.connect(
            host=host or "localhost",
            port=port or 5432,
            database=database or "analytics",
            user=user or "postgres",
            password=password or "postgres"
        )
        return conn, "Connected with default credentials"
        
    except Exception as e:
        return None, str(e)
