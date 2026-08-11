"""
API Client for fetching data from the webhook server.
"""

import requests
import time
import json
import os
from typing import List, Dict, Any, Optional

REQUEST_DELAY = 1.0  # seconds between requests


def normalize_integration_orders_base_url(raw: str) -> str:
    """
    Build the base URL used for GET {base}/orders/...

    Strips trailing slashes and a trailing /orders segment (so pasting the full stream
    URL ending in .../orders does not become .../orders/orders/).

    The path prefix before /orders/ is kept as configured (e.g. .../analytics for
    webhooks behind /analytics/orders/).
    """
    base = (raw or "").strip().rstrip("/")
    if not base:
        return base
    lower = base.lower()
    if lower.endswith("/orders"):
        base = base[: -len("/orders")].rstrip("/")
    return base


def orders_integration_request_headers(api_key: str) -> dict:
    """Unscoped analytics headers (only for the allowed-restaurants endpoint)."""
    from src.core.central_api import unscoped_analytics_headers

    return unscoped_analytics_headers(api_key)


def fetch_stream_raw(
    conn,
    endpoint: str = "orders",
    limit: int = 500,
    start_cursor: Optional[int] = 0,
    max_records: Optional[int] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """
    Fetch all records from the stream endpoint with pagination.
    
    Args:
        conn: Database connection to fetch configuration
        endpoint: API endpoint (default: "orders")
        limit: Number of records per page (max 500)
        start_cursor: Starting stream_id (0 for beginning)
        max_records: Maximum total records to fetch (None = all)
    
    Returns:
        Tuple of (List of all fetched records, Total available records at source)
    """
    # Fetch Config
    try:
        from src.core.db.control import resolve_config_values

        config = resolve_config_values(
            conn, ("integration_orders_url", "integration_orders_key")
        )
    except Exception as e:
        print(f"Error fetching integration config: {e}")
        return [], 0

    base_url = config.get("integration_orders_url")
    api_key = config.get("integration_orders_key")

    if not base_url or not api_key:
        print("❌ Orders Integration not configured. Please check Configuration.")
        return [], 0

    base_url = normalize_integration_orders_base_url(base_url)

    from src.core.central_api import CentralAPIError, error_from_response, scoped_headers

    headers = scoped_headers(conn, auth_kind="analytics", credential=api_key)

    results = []
    last_stream_id = start_cursor or 0
    page_count = 0
    total_available_count = 0
    retries = 0
    MAX_RETRIES = 3
    page_limit = min(max(int(limit), 1), 500)
    
    print(f"Fetching from {endpoint} endpoint at {base_url}...")
    
    while True:
        # Rate limiting
        if page_count > 0:
            time.sleep(REQUEST_DELAY)
        
        params = {
            "limit": page_limit,
            "cursor": last_stream_id,
        }
        
        try:
            resp = requests.get(
                f"{base_url}/{endpoint}/",
                headers=headers,
                params=params,
                timeout=60,
            )
            if resp.status_code >= 400:
                raise error_from_response(resp, conn=conn)
            
            # Reset retries on success
            retries = 0
            
            payload = resp.json()
            from src.core.analytics_stream_contract import parse_stream_page

            page = parse_stream_page(payload, endpoint)
            batch = page.data
            
            # Try to get total from first page
            if page_count == 0:
                total_available_count = page.total

            if not batch:
                break
            
            results.extend(batch)
            page_cursor = page.next_cursor
            if page_cursor is not None:
                last_stream_id = int(page_cursor)
            else:
                last_stream_id = int(batch[-1]["stream_id"])
            page_count += 1
            
            print(f"Page {page_count}: Fetched {len(batch)} records (Total: {len(results)})")
            
            # Check max records limit
            if max_records and len(results) >= max_records:
                results = results[:max_records]
                break
            
            # Check if we got fewer records than requested (last page)
            if len(batch) < page_limit:
                break
                
        except CentralAPIError as e:
            if not e.retryable:
                raise
            retries += 1
            print(f"Error fetching data (Attempt {retries}/{MAX_RETRIES}): {e}")

            if retries >= MAX_RETRIES:
                print("❌ Max retries reached. Aborting.")
                raise Exception(
                    f"Failed to connect to {endpoint} after {MAX_RETRIES} attempts: {str(e)}"
                ) from e

            print("Retrying in 5 seconds...")
            time.sleep(5)
            continue

        except requests.exceptions.RequestException as e:
            retries += 1
            print(f"Error fetching data (Attempt {retries}/{MAX_RETRIES}): {e}")
            
            if retries >= MAX_RETRIES:
                print("❌ Max retries reached. Aborting.")
                raise Exception(f"Failed to connect to {endpoint} after {MAX_RETRIES} attempts: {str(e)}")
            
            print(f"Retrying in 5 seconds...")
            time.sleep(5)
            continue
            
    return results, total_available_count


def load_orders_from_file(filepath: str) -> List[Dict]:
    """Load orders from JSON file (replaces functionality from fetch_orders.py)"""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return []
        
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)
