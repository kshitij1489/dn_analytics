import psycopg2
import os

def create_postgresql_connection(host=None, port=None, database=None, user=None, password=None, db_url=None):
    """Create PostgreSQL database connection"""
    if db_url:
        return psycopg2.connect(db_url)
    
    # Try environment variable if no URL provided
    env_url = os.environ.get("DB_URL")
    if env_url:
        return psycopg2.connect(env_url)
        
    return psycopg2.connect(
        host=host or "localhost",
        port=port or 5432,
        database=database or "analytics",
        user=user or "postgres",
        password=password or "postgres"
    )
