"""
Task 1: Fetch Raw JSON Payloads from Django Webhook Server

This module handles fetching order data from the PetPooja webhook server.
"""

import requests
import json
import os
from typing import Optional, List, Dict, Any
from datetime import datetime
import time

BASE_URL = "https://webhooks.db1-prod-dachnona.store/analytics"
API_KEY = "f3e1753aa4c44159fa7218a31cd8db1e"

HEADERS = {
    "X-API-Key": API_KEY,
}

# Rate limiting: max requests per minute
MAX_REQUESTS_PER_MINUTE = 60
REQUEST_DELAY = 1.0  # seconds between requests


def _safe_json_load(value):
    """Safely parse JSON value, handling already-parsed dicts/lists"""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def fetch_stream_raw(
    endpoint: str = "orders",
    limit: int = 500,
    start_cursor: Optional[int] = 0,
    max_records: Optional[int] = None,
    save_to_file: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch all records from the stream endpoint with pagination.
    
    Args:
        endpoint: API endpoint (default: "orders")
        limit: Number of records per page (max 500)
        start_cursor: Starting stream_id (0 for beginning)
        max_records: Maximum total records to fetch (None = all)
        save_to_file: Optional path to save JSON file incrementally
    
    Returns:
        List of all fetched records
    """
    results = []
    last_stream_id = start_cursor or 0
    page_count = 0
    
    print(f"Fetching from {endpoint} endpoint...")
    print(f"Starting from stream_id: {last_stream_id}")
    
    while True:
        # Rate limiting
        if page_count > 0:
            time.sleep(REQUEST_DELAY)
        
        params = {
            "limit": min(limit, 500),  # API max is 500
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
                print(f"No more records. Total fetched: {len(results)}")
                break
            
            results.extend(batch)
            last_stream_id = batch[-1]["stream_id"]
            page_count += 1
            
            print(f"Page {page_count}: Fetched {len(batch)} records (Total: {len(results)})")
            
            # Save incrementally if requested
            if save_to_file and page_count % 10 == 0:
                _save_json(results, save_to_file)
            
            # Check max records limit
            if max_records and len(results) >= max_records:
                print(f"Reached max_records limit: {max_records}")
                results = results[:max_records]
                break
            
            # Check if we got fewer records than requested (last page)
            if len(batch) < limit:
                print(f"Last page reached. Total fetched: {len(results)}")
                break
                
        except requests.exceptions.RequestException as e:
            print(f"Error fetching data: {e}")
            print(f"Retrying in 5 seconds...")
            time.sleep(5)
            continue
    
    # Final save
    if save_to_file:
        _save_json(results, save_to_file)
        print(f"Saved {len(results)} records to {save_to_file}")
    
    return results


def fetch_orders_incremental(
    last_stream_id: int = 0,
    save_to_file: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch only new orders since last_stream_id (for incremental updates).
    
    Args:
        last_stream_id: Last processed stream_id
        save_to_file: Optional path to save JSON file
    
    Returns:
        List of new orders
    """
    return fetch_stream_raw(
        endpoint="orders",
        start_cursor=last_stream_id + 1,
        save_to_file=save_to_file,
    )


def fetch_sample_orders(count: int = 100) -> List[Dict[str, Any]]:
    """
    Fetch a small sample of orders for analysis.
    
    Args:
        count: Number of orders to fetch
    
    Returns:
        List of sample orders
    """
    return fetch_stream_raw(
        endpoint="orders",
        limit=min(count, 500),
        max_records=count,
    )


def _save_json(data: List[Dict], filepath: str):
    """Save data to JSON file"""
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else '.', exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def save_orders_to_file(orders: List[Dict], filepath: str):
    """Save orders to JSON file"""
    _save_json(orders, filepath)


def load_orders_from_file(filepath: str) -> List[Dict]:
    """Load orders from JSON file"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_order_statistics(orders: List[Dict]) -> Dict[str, Any]:
    """Get basic statistics about fetched orders"""
    if not orders:
        return {}
    
    order_ids = set()
    order_types = {}
    order_sources = {}
    date_range = {"min": None, "max": None}
    
    for order in orders:
        # Extract order ID
        order_id = order.get('aggregate_id')
        if order_id:
            order_ids.add(order_id)
        
        # Extract order details
        try:
            raw_payload = order.get('raw_event', {}).get('raw_payload', {})
            props = raw_payload.get('properties', {})
            order_data = props.get('Order', {})
            
            # Order type
            order_type = order_data.get('order_type', 'Unknown')
            order_types[order_type] = order_types.get(order_type, 0) + 1
            
            # Order source
            order_from = order_data.get('order_from', 'Unknown')
            order_sources[order_from] = order_sources.get(order_from, 0) + 1
            
            # Date range
            created_on = order_data.get('created_on')
            if created_on:
                try:
                    dt = datetime.strptime(created_on, '%Y-%m-%d %H:%M:%S')
                    if date_range["min"] is None or dt < date_range["min"]:
                        date_range["min"] = dt
                    if date_range["max"] is None or dt > date_range["max"]:
                        date_range["max"] = dt
                except:
                    pass
        except:
            pass
    
    return {
        "total_records": len(orders),
        "unique_orders": len(order_ids),
        "order_types": order_types,
        "order_sources": order_sources,
        "date_range": {
            "min": date_range["min"].isoformat() if date_range["min"] else None,
            "max": date_range["max"].isoformat() if date_range["max"] else None,
        }
    }


if __name__ == "__main__":
    # Example usage
    
    # Fetch sample orders for analysis
    print("=" * 80)
    print("Fetching sample orders...")
    print("=" * 80)
    
    sample_orders = fetch_sample_orders(count=100)
    
    # Save to file
    sample_file = "sample_payloads/sample_orders_100.json"
    save_orders_to_file(sample_orders, sample_file)
    
    # Print statistics
    stats = get_order_statistics(sample_orders)
    print("\n" + "=" * 80)
    print("ORDER STATISTICS")
    print("=" * 80)
    print(json.dumps(stats, indent=2))
    
    # Full fetch example (commented out - uncomment to run)
    # print("\n" + "=" * 80)
    # print("Fetching ALL orders...")
    # print("=" * 80)
    # all_orders = fetch_stream_raw(
    #     endpoint="orders",
    #     save_to_file="raw_data/all_orders.json"
    # )
    # print(f"\nFetched {len(all_orders)} total orders")

