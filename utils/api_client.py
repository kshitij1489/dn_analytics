"""
API Client for fetching data from the webhook server.
"""

import requests
import time
import json
import os
from typing import List, Dict, Any, Optional

BASE_URL = "https://your-api-url.com/analytics"
API_KEY = "your_api_key_here"

HEADERS = {
    "X-API-Key": API_KEY,
}

REQUEST_DELAY = 1.0  # seconds between requests

def fetch_stream_raw(
    endpoint: str = "orders",
    limit: int = 500,
    start_cursor: Optional[int] = 0,
    max_records: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch all records from the stream endpoint with pagination.
    
    Args:
        endpoint: API endpoint (default: "orders")
        limit: Number of records per page (max 500)
        start_cursor: Starting stream_id (0 for beginning)
        max_records: Maximum total records to fetch (None = all)
    
    Returns:
        List of all fetched records
    """
    results = []
    last_stream_id = start_cursor or 0
    page_count = 0
    
    print(f"Fetching from {endpoint} endpoint...")
    
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
                f"{BASE_URL}/{endpoint}/",
                headers=HEADERS,
                params=params,
                timeout=60,
            )
            resp.raise_for_status()
            
            payload = resp.json()
            batch = payload.get("data", [])
            
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
            print(f"Error fetching data: {e}")
            print(f"Retrying in 5 seconds...")
            time.sleep(5)
            continue
            
    return results


def load_orders_from_file(filepath: str) -> List[Dict]:
    """Load orders from JSON file (replaces functionality from fetch_orders.py)"""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return []
        
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)
