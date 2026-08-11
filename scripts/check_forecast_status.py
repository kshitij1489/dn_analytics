import argparse
import sys
import os

sys.path.append(os.path.abspath(os.getcwd()))

from src.core.central_forecast_projection import has_central_revenue_forecast
from src.core.db.connection import get_profile_connection
from src.core.profiles import get_profile
from src.core.utils.business_date import get_current_business_date


def check_forecast_status(restaurant_id: str):
    conn, _ = get_profile_connection(get_profile(restaurant_id))

    today_str = get_current_business_date()
    has_central = has_central_revenue_forecast(conn)

    print(f"Current Business Date: {today_str}")
    print(f"Central revenue forecast cached? {has_central}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check one restaurant's forecast cache")
    parser.add_argument("--restaurant-id", required=True)
    check_forecast_status(parser.parse_args().restaurant_id)
