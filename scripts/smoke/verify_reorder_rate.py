"""Verify reorder-rate query output against one restaurant profile database."""

import argparse
import os
import sys

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core.db.connection import get_profile_connection
from src.core.profiles import get_profile
from src.core.queries.customer_queries import fetch_reorder_rate_trend


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restaurant-id", required=True, help="Bound restaurant profile to read")
    args = parser.parse_args()

    conn, _ = get_profile_connection(get_profile(args.restaurant_id))
    try:
        print("Testing fetch_reorder_rate_trend(granularity='day')...")
        data_day = fetch_reorder_rate_trend(conn, granularity="day")
        print(f"Returned {len(data_day)} rows.")
        if data_day:
            print("Sample row:", data_day[0])

        print("\nTesting fetch_reorder_rate_trend(granularity='week')...")
        data_week = fetch_reorder_rate_trend(conn, granularity="week")
        print(f"Returned {len(data_week)} rows.")
        if data_week:
            print("Sample row:", data_week[0])

        print("\nTesting fetch_reorder_rate_trend(granularity='month')...")
        data_month = fetch_reorder_rate_trend(conn, granularity="month")
        print(f"Returned {len(data_month)} rows.")
        if data_month:
            print("Sample row:", data_month[0])

    finally:
        conn.close()


if __name__ == "__main__":
    main()
