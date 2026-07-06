"""Manually exercise the auto-forecast scheduler against the local analytics.db."""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.api.routers import forecast_training_status
from src.core.db.connection import get_db_connection
from src.core.services.cloud_sync_scheduler import check_and_trigger_auto_forecast


def main() -> int:
    conn, _ = get_db_connection()
    if not conn:
        print("Failed to connect to DB")
        return 1

    print("--- Starting Auto-Forecast Trigger Smoke Run ---")

    if forecast_training_status.is_training():
        print("Training is already active! Aborting to avoid interference.")
        print(forecast_training_status.get_status())
        conn.close()
        return 1

    print("Calling check_and_trigger_auto_forecast...")
    check_and_trigger_auto_forecast(conn)

    print("--- Check Complete ---")

    status = forecast_training_status.get_status()
    print("Final Training Status:", status)

    conn.close()

    if status["active"] or status["progress"] == 100:
        print("SUCCESS: Training was triggered/completed.")
        return 0

    print("FAILURE: Training was NOT triggered (or failed immediately).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
