"""Smoke-test the reorder rate trend API against a running local backend."""

import sys

import requests

API_URL = "http://127.0.0.1:8000/api/insights/customer/reorder_rate_trend"


def main() -> int:
    try:
        print(f"Testing API endpoint: {API_URL}")

        print("\n--- Testing Metric: orders (default) ---")
        response = requests.get(API_URL, params={"granularity": "day", "metric": "orders"})
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if data:
                print(f"Sample Data: {data[0]}")
            else:
                print("Data is empty.")
        else:
            print(f"Error: {response.text}")

        print("\n--- Testing Metric: customers ---")
        response = requests.get(API_URL, params={"granularity": "day", "metric": "customers"})
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if data:
                print(f"Sample Data: {data[0]}")
            else:
                print("Data is empty.")
        else:
            print(f"Error: {response.text}")

    except Exception as e:
        print(f"Connection failed: {e}")
        print("Make sure the backend is running on http://127.0.0.1:8000")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
