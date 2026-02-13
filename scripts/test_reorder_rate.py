import sys
import os
import sqlite3

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.db.connection import get_db_connection
from src.core.queries.customer_queries import fetch_reorder_rate_trend

def test_reorder_rate():
    conn, _ = get_db_connection()
    try:
        print("Testing fetch_reorder_rate_trend(granularity='day')...")
        data_day = fetch_reorder_rate_trend(conn, granularity='day')
        print(f"Returned {len(data_day)} rows.")
        if data_day:
            print("Sample row:", data_day[0])
        
        print("\nTesting fetch_reorder_rate_trend(granularity='week')...")
        data_week = fetch_reorder_rate_trend(conn, granularity='week')
        print(f"Returned {len(data_week)} rows.")
        if data_week:
            print("Sample row:", data_week[0])

        print("\nTesting fetch_reorder_rate_trend(granularity='month')...")
        data_month = fetch_reorder_rate_trend(conn, granularity='month')
        print(f"Returned {len(data_month)} rows.")
        if data_month:
            print("Sample row:", data_month[0])

    finally:
        conn.close()

if __name__ == "__main__":
    test_reorder_rate()
