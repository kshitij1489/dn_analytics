import argparse
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


def _headers(api_key: str, restaurant_id: str) -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Restaurant-ID": restaurant_id,
    }


def verify(api_key: str, restaurant_id: str) -> bool:
    all_success = True
    print(f"Testing connection to: {_PLACEHOLDER_BASE}")

    test_url = f"{_PLACEHOLDER_BASE}/desktop-analytics-sync/menu-bootstrap/latest"
    print(f"Target URL 1: {test_url}")

    try:
        response = requests.get(test_url, headers=_headers(api_key, restaurant_id), timeout=5)
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
        response = requests.get(
            CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL,
            headers=_headers(api_key, restaurant_id),
            timeout=5,
        )
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify scoped desktop-sync endpoints")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DACHNONA_E2E_API_KEY"),
        help="Desktop sync Bearer token (or DACHNONA_E2E_API_KEY)",
    )
    parser.add_argument(
        "--restaurant-id",
        default=os.environ.get("DACHNONA_E2E_RESTAURANT_ID"),
        help="Physical restaurant ID (required; or DACHNONA_E2E_RESTAURANT_ID)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    api_key = str(args.api_key or "").strip()
    restaurant_id = str(args.restaurant_id or "").strip()
    if not api_key or not restaurant_id or restaurant_id == "__all__":
        print("FAILURE: --api-key and one physical --restaurant-id are required")
        raise SystemExit(2)
    sys.exit(0 if verify(api_key, restaurant_id) else 1)
