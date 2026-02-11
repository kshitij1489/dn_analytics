
import sys
import os
import requests

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.config.client_learning_config import (
    CLIENT_LEARNING_MENU_BOOTSTRAP_INGEST_URL, 
    CLIENT_LEARNING_FORECAST_BOOTSTRAP_URL,
    _PLACEHOLDER_BASE
)

def test_connection():
    all_success = True
    print(f"Testing connection to: {_PLACEHOLDER_BASE}")
    
    # ---------------------------------------------------------
    # Test 1: Menu Bootstrap (Latest)
    # ---------------------------------------------------------
    test_url = f"{_PLACEHOLDER_BASE}/desktop-analytics-sync/menu-bootstrap/latest"
    print(f"Target URL 1: {test_url}")

    try:
        response = requests.get(test_url, timeout=5)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("SUCCESS: Connection established and valid response received.")
            print("Response:", response.text[:200]) # First 200 chars
        elif response.status_code == 404:
             print("WARNING: Server reached, but endpoint not found (404).")
             all_success = False
        else:
            print(f"FAILURE: Server returned error status {response.status_code}")
            all_success = False

    except requests.exceptions.ConnectionError:
        print("FAILURE: Could not connect to the server. Is it running?")
        print("Make sure 'uvicorn src.api.main:app --port 8000' is running in another terminal.")
        return False # Fatal error, stop here
    except Exception as e:
        print(f"ERROR: An unexpected error occurred: {e}")
        all_success = False

    print("-" * 30)
    
    # ---------------------------------------------------------
    # Test 2: Forecast Bootstrap
    # ---------------------------------------------------------
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
    success = test_connection()
    sys.exit(0 if success else 1)
