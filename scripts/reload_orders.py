#!/usr/bin/env python3
"""
Reload all orders to repopulate customers table after migration.
This script fetches all orders from the API and reprocesses them.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.load_orders import (
    create_postgresql_connection,
    process_order,
)
from utils.api_client import fetch_stream_raw
from data_cleaning.item_matcher import ItemMatcher
import psycopg2

def main():
    print("=" * 80)
    print("Reloading All Orders (Customer Migration)")
    print("=" * 80)
    
    # Connect to database
    print("\n1. Connecting to database...")
    db_url = "postgresql://postgres@localhost:5432/analytics"
    try:
        conn = psycopg2.connect(db_url)
        print("  ✓ Connected successfully")
    except Exception as e:
        print(f"  ✗ Connection failed: {e}")
        return
    
    # Initialize ItemMatcher
    print("\n2. Initializing ItemMatcher...")
    try:
        item_matcher = ItemMatcher(conn)
        print("  ✓ ItemMatcher ready")
    except Exception as e:
        print(f"  ✗ ItemMatcher initialization failed: {e}")
        conn.close()
        return
    
    # Fetch all orders
    print("\n3. Fetching all orders from API...")
    try:
        orders = fetch_stream_raw(endpoint="orders", start_cursor=0)
        print(f"  ✓ Fetched {len(orders)} orders")
    except Exception as e:
        print(f"  ✗ Failed to fetch orders: {e}")
        conn.close()
        return
    
    # Process each order
    print("\n4. Processing orders...")
    stats = {
        'orders': 0,
        'order_items': 0,
        'order_item_addons': 0,
        'order_taxes': 0,
        'order_discounts': 0,
        'errors': []
    }
    
    for i, order_payload in enumerate(orders, 1):
        if i % 100 == 0:
            print(f"  Processing order {i}/{len(orders)}...")
        
        order_stats = process_order(conn, order_payload, item_matcher)
        for key in stats:
            if key == 'errors':
                stats[key].extend(order_stats[key])
            else:
                stats[key] += order_stats[key]
    
    # Print summary
    print("\n" + "=" * 80)
    print("RELOAD SUMMARY")
    print("=" * 80)
    print(f"Orders processed: {stats['orders']}")
    print(f"Order items: {stats['order_items']}")
    print(f"Order item addons: {stats['order_item_addons']}")
    print(f"Taxes: {stats['order_taxes']}")
    print(f"Discounts: {stats['order_discounts']}")
    
    if stats['errors']:
        print(f"\n⚠️  Errors encountered: {len(stats['errors'])}")
        for error in stats['errors'][:10]:
            print(f"  - {error}")
        if len(stats['errors']) > 10:
            print(f"  ... and {len(stats['errors']) - 10} more errors")
    
    # Check customer count
    print("\n" + "=" * 80)
    print("CUSTOMER STATS")
    print("=" * 80)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            COUNT(*) as total_customers,
            COUNT(CASE WHEN phone IS NOT NULL THEN 1 END) as with_phone,
            COUNT(CASE WHEN phone IS NULL THEN 1 END) as without_phone
        FROM customers
    """)
    result = cursor.fetchone()
    print(f"Total customers: {result[0]}")
    print(f"  - With phone: {result[1]}")
    print(f"  - Without phone: {result[2]}")
    cursor.close()
    
    conn.close()
    print("\n✅ Reload complete!")

if __name__ == "__main__":
    main()
