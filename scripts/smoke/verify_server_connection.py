import os
import sys

import requests

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.core.config.client_learning_config import (
    CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL,
    _PLACEHOLDER_BASE,
)


def verify() -> bool:
    all_success = True
    print(f"Testing connection to: {_PLACEHOLDER_BASE}")

    test_url = f"{_PLACEHOLDER_BASE}/desktop-analytics-sync/menu-bootstrap/latest"
    print(f"Target URL 1: {test_url}")

    try:
        response = requests.get(test_url, timeout=5)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            print("SUCCESS: Connection established and valid response received.")
            print("Response:", response.text[:200])
        elif response.status_code == 404:
            print("WARNING: Server reached, but endpoint not found (404).")
            all_success = False
        else:
            print(f"FAILURE: Server returned error status {response.status_code}")
            all_success = False

    except requests.exceptions.ConnectionError:
        print("FAILURE: Could not connect to the server. Is it running?")
        print("Make sure 'uvicorn src.api.main:app --port 8000' is running in another terminal.")
        return False
    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {e}")
        all_success = False

    print("-" * 30)

    print(f"Testing Forecast Bootstrap: {CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL}")
    try:
        response = requests.get(CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL, timeout=5)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            print("SUCCESS: Forecast Bootstrap endpoint reachable.")
        else:
            print(f"FAILURE: Forecast Bootstrap returned {response.status_code}")
            all_success = False
    except Exception as e:
        print(f"ERROR: Could not connect to bootstrap endpoint: {e}")
        all_success = False

    return all_success


if __name__ == "__main__":
    sys.exit(0 if verify() else 1)
