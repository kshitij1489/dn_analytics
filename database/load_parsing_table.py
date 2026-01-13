"""
Load item_parsing_table.csv into PostgreSQL.
Also ensures the table exists by applying the schema.
"""

import sys
import os
import csv
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Try to match the connection logic from load_orders.py
try:
    import psycopg2
    from psycopg2.extras import execute_values
except ImportError:
    print("psycopg2 not installed.")
    sys.exit(1)

import argparse

def get_db_connection(db_url=None, host=None, port=None, db_name=None, user=None, password=None):
    if db_url:
        return psycopg2.connect(db_url)
    
    # Fallback to defaults or args
    host = host or "localhost"
    port = port or 5432
    db_name = db_name or "analytics"
    user = user or "kshitijsharma"
    
    conn_params = {
        "host": host,
        "port": int(port),
        "database": db_name,
        "user": user
    }
    if password:
        conn_params["password"] = password
        
    return psycopg2.connect(**conn_params)

def main():
    parser = argparse.ArgumentParser(description="Load item_parsing_table.csv into PostgreSQL")
    parser.add_argument('--db-url', type=str, help="PostgreSQL connection URL")
    parser.add_argument('--host', type=str, default='localhost', help="Database host")
    parser.add_argument('--port', type=int, default=5432, help="Database port")
    parser.add_argument('--database', type=str, default='analytics', help="Database name")
    parser.add_argument('--user', type=str, default='kshitijsharma', help="Database user")
    parser.add_argument('--password', type=str, help="Database password")
    args = parser.parse_args()

    print("="*50)
    print("Initializing Item Parsing Table")
    print("="*50)
    
    try:
        conn = get_db_connection(
            db_url=args.db_url,
            host=args.host,
            port=args.port,
            db_name=args.database,
            user=args.user,
            password=args.password
        )
        print("Connected to database.")
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Please provide connection details via --db-url or arguments.")
        sys.exit(1)

    if apply_schema(conn):
        csv_path = Path(__file__).parent.parent / "data" / "item_parsing_table.csv"
        load_csv(conn, str(csv_path))
        
    conn.close()

if __name__ == "__main__":
    main()
