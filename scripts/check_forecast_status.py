import sys
import os

# Add src to path
sys.path.append(os.path.abspath(os.getcwd()))

from src.core.db.connection import get_db_connection
from src.core.utils.business_date import get_current_business_date
from src.core.learning.revenue_forecasting.forecast_cache import is_revenue_cache_fresh

def check_forecast_status():
    conn, _ = get_db_connection()
    if not conn:
        print("Failed to connect to DB")
        return

    today_str = get_current_business_date()
    is_fresh = is_revenue_cache_fresh(conn, today_str)
    
    print(f"Current Business Date: {today_str}")
    print(f"Is Forecast Fresh? {is_fresh}")
    
    conn.close()

if __name__ == "__main__":
    check_forecast_status()
