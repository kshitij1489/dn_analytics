import os
import sqlite3
from src.core.db.connection import get_db_connection, DB_PATH
from src.core.utils.path_helper import get_resource_path
from scripts.seed_from_backups import perform_seeding

def reset_database():
    """
    Resets the database by:
    1. Removing the analytics.db file (if exists)
    2. Creating a new connection (creates file)
    3. Executing schema_sqlite.sql
    4. Auto-seeding menu data from backup JSON files
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
        
        # 4. Auto-seed menu data from backups (menu_items, variants, menu_item_variants)
        try:
            if perform_seeding(conn):
                seed_msg = " Menu data seeded from backups."
            else:
                seed_msg = " Menu seeding skipped (no backup files)."
        except Exception as seed_err:
            seed_msg = f" Menu seeding failed: {str(seed_err)}"
        
        conn.close()

        return True, f"Database reset successfully.{seed_msg}"

    except Exception as e:
        return False, str(e)

