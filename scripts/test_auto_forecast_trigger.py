import sys
import os
import logging

# Configure logging to see the output
logging.basicConfig(level=logging.INFO)

# Add src to path
sys.path.append(os.path.abspath(os.getcwd()))

from src.core.db.connection import get_db_connection
from src.api.routers import forecast_training_status

# Import the function to test
# We need to mock/patch _full_retrain_task if we don't want to actually run the full training, 
# but for a true integration test, let's let it run or at least start.
# Actually, running full training might take time. 
# Let's just verify it *would* trigger. 
# But the user wants it fixed, so running it is actually ensuring the fix works end-to-end.

from src.core.services.cloud_sync_scheduler import check_and_trigger_auto_forecast

def test_trigger():
    conn, _ = get_db_connection()
    if not conn:
        print("Failed to connect to DB")
        return

    print("--- Starting Auto-Forecast Trigger Test ---")
    
    # reset status just in case
    if forecast_training_status.is_training():
        print("Training is already active! Aborting test to avoid interference.")
        print(forecast_training_status.get_status())
        return

    print("Calling check_and_trigger_auto_forecast...")
    check_and_trigger_auto_forecast(conn)
    
    print("--- Check Complete ---")
    
    # Check status
    status = forecast_training_status.get_status()
    print("Final Training Status:", status)
    
    if status['active'] or status['progress'] == 100:
        print("SUCCESS: Training was triggered/completed.")
    else:
        print("FAILURE: Training was NOT triggered (or failed immediately).")

    conn.close()

if __name__ == "__main__":
    test_trigger()
