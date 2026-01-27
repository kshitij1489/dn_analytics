import sqlite3
import os

DB_PATH = "analytics.db"

def get_db_connection(db_url=None):
    """
    Create SQLite database connection.
    Arguments like host, port, user, password are ignored but kept for signature compatibility if needed.
    """
    try:
        # Use env var or default path
        target_db = db_url or os.environ.get("DB_URL") or DB_PATH
        
        print(f"Connecting to database at: {os.path.abspath(target_db)}")
        
        conn = sqlite3.connect(target_db, check_same_thread=False)
        
        # Enable Access to Columns by Name (like RealDictCursor)
        conn.row_factory = sqlite3.Row
        
        # Enforce Foreign Keys
        conn.execute("PRAGMA foreign_keys = ON;")
        
        return conn, "Connected to SQLite"
        
    except Exception as e:
        return None, str(e)
