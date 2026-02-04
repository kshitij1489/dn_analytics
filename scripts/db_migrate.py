import sqlite3
import os
import sys

# Add project root to path to use existing connection logic
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.db.connection import get_db_connection
from ai_mode.cache.cache_config import CACHE_DB_PATH

def run_migrations():
    print("--- Starting Database Migrations ---")
    
    # 1. Main Database Migration
    print(f"\n1. Applying Main Schema to analytics.db...")
    try:
        conn, msg = get_db_connection()
        if not conn:
            print(f"   Error connecting to main DB: {msg}")
        else:
            schema_file = 'database/schema_sqlite.sql'
            if os.path.exists(schema_file):
                with open(schema_file, 'r') as f:
                    schema_sql = f.read()
                # Use executescript to handle multiple statements
                conn.executescript(schema_sql)
                conn.commit()
                print(f"   Successfully applied {schema_file}")
            else:
                print(f"   Warning: {schema_file} not found.")
            conn.close()
    except Exception as e:
        print(f"   Error migrating main DB: {e}")

    # 2. LLM Cache Migration
    print(f"\n2. Applying Cache Schema to {CACHE_DB_PATH}...")
    try:
        # Connect directly to cache DB
        conn = sqlite3.connect(CACHE_DB_PATH)
        cache_schema_file = 'database/schema_llm_cache.sql'
        if os.path.exists(cache_schema_file):
            with open(cache_schema_file, 'r') as f:
                cache_schema_sql = f.read()
            conn.executescript(cache_schema_sql)
            conn.commit()
            print(f"   Successfully applied {cache_schema_file}")
        else:
            print(f"   Warning: {cache_schema_file} not found.")
        conn.close()
    except Exception as e:
        print(f"   Error migrating cache DB: {e}")

    print("\n--- Migrations Complete ---")

if __name__ == "__main__":
    run_migrations()
