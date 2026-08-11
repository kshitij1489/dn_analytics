import argparse
import sqlite3
import os
import sys

# Add project root to path to use existing connection logic
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.db.connection import get_profile_connection
from src.core.profiles import get_profile
from ai_mode.cache.cache_config import CACHE_DB_PATH

def run_migrations(restaurant_id: str):
    print("--- Starting Database Migrations ---")

    # 1. Main Database Migration — one named profile, never an implicit default.
    print(f"\n1. Applying Main Schema to restaurant profile {restaurant_id}...")
    try:
        # get_profile_connection applies the canonical schema owner and verifies
        # the identity row, so this script does not re-inline or re-run schema DDL.
        conn, _ = get_profile_connection(get_profile(restaurant_id))
        try:
            print("   Successfully applied database/schema_sqlite.sql")
        finally:
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
    parser = argparse.ArgumentParser(description="Apply schema migrations to one restaurant profile")
    parser.add_argument("--restaurant-id", required=True, help="Bound restaurant profile to migrate")
    run_migrations(parser.parse_args().restaurant_id)
