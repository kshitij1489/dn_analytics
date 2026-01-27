"""
API Client for fetching data from the webhook server.
"""

import requests
import time
import json
import os
from typing import List, Dict, Any, Optional

REQUEST_DELAY = 1.0  # seconds between requests

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
        cursor = conn.execute("SELECT key, value FROM system_config WHERE key IN ('integration_orders_url', 'integration_orders_key')")
        config = {row[0]: row[1] for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error fetching integration config: {e}")
        return [], 0

    base_url = config.get("integration_orders_url")
    api_key = config.get("integration_orders_key")

    if not base_url or not api_key:
        print("❌ Orders Integration not configured. Please check Configuration.")
        return [], 0

    # Ensure base_url doesn't have trailing slash
    base_url = base_url.rstrip('/')

    headers = {
        "X-API-Key": api_key
    }

    results = []
    last_stream_id = start_cursor or 0
    page_count = 0
    total_available_count = 0
    retries = 0
    MAX_RETRIES = 3
    
    print(f"Fetching from {endpoint} endpoint at {base_url}...")
    
    while True:
        # Rate limiting
        if page_count > 0:
            time.sleep(REQUEST_DELAY)
        
        params = {
            "limit": min(limit, 500),
            "cursor": last_stream_id,
        }
        
        try:
            resp = requests.get(
                f"{base_url}/{endpoint}/",
                headers=headers,
                params=params,
                timeout=60,
            )
            resp.raise_for_status()
            
            # Reset retries on success
            retries = 0
            
            payload = resp.json()
            batch = payload.get("data", [])
            
            # Try to get total from first page
            if page_count == 0:
                total_available_count = payload.get("total", 0) or payload.get("count", 0)

            if not batch:
                break
            
            results.extend(batch)
            last_stream_id = batch[-1]["stream_id"]
            page_count += 1
            
            print(f"Page {page_count}: Fetched {len(batch)} records (Total: {len(results)})")
            
            # Check max records limit
            if max_records and len(results) >= max_records:
                results = results[:max_records]
                break
            
            # Check if we got fewer records than requested (last page)
            if len(batch) < limit:
                break
                
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
