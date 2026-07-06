import os
import sqlite3
from src.core.db.connection import get_db_connection, DB_PATH
from src.core.utils.path_helper import get_resource_path
def reset_database():
    """
    Resets the database by:
    1. Removing the analytics.db file (if exists)
    2. Creating a new connection (creates file)
    3. Executing schema_sqlite.sql
    4. Leaves catalog empty — next Sync DB pulls from cloud when configured
    """
    try:
        # 1. Delete existing DB file
        # 1. Delete existing DB file
        target_db = os.environ.get("DB_URL") or DB_PATH
        if os.path.exists(target_db):
            os.remove(target_db)
            print(f"Deleted database at {target_db}")
            
        # 2. Create new connection
        conn, _ = get_db_connection()
        if not conn:
            raise Exception("Failed to connect/create database")
            
        # 3. Apply Schema
        schema_path = get_resource_path(os.path.join("database", "schema_sqlite.sql"))
        
        if os.path.exists(schema_path):
            with open(schema_path, "r") as f:
                conn.executescript(f.read())
        else:
             raise Exception(f"Schema file not found at {schema_path}")
             
        conn.commit()
        conn.close()

        return True, "Database reset successfully. Run Sync DB to pull catalog from cloud."

    except Exception as e:
        return False, str(e)

