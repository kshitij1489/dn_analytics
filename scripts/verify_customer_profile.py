
import argparse
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.core.db.connection import get_profile_connection
from src.core.profiles import get_profile
from src.core.queries import customer_queries

def test_customer_profile(restaurant_id: str):
    print(f"Testing Customer Profile Backend for {restaurant_id}...")

    try:
        conn, msg = get_profile_connection(get_profile(restaurant_id))
    except Exception as exc:
        print(f"Profile connection failed: {exc}")
        return
    print(f"DB Connection: {msg}")

    # 1. Test Search
    print("\n1. Testing Search ('a')...")
    results = customer_queries.search_customers(conn, 'a', limit=5)
    print(f"Found {len(results)} customers.")
    if results:
        first_cust = results[0]
        print(f"Sample Customer: {first_cust['name']} ({first_cust['customer_id']})")
        
        # 2. Test Profile Fetch
        cid = first_cust['customer_id']
        print(f"\n2. Testing Profile Fetch for ID: {cid}...")
        customer, orders, addresses = customer_queries.fetch_customer_profile_data(conn, cid)
        
        if customer:
            print(f"Customer Details: {customer['name']}, Total Spent: {customer['total_spent']}")
            print(f"Saved Address: {customer.get('address') or 'None'}")
            print(f"Structured Addresses: {len(addresses)}")
            print(f"Orders Found: {len(orders)}")
            if orders:
                print(f"Latest Order: {orders[0]['order_number']} - {orders[0]['total_amount']}")
                print(f"Items Summary: {orders[0]['items_summary']}")
        else:
            print("Failed to fetch profile!")
            
    else:
        print("No customers found matching 'a'. Skipping profile test.")

    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smoke-check customer profile queries for one restaurant")
    parser.add_argument("--restaurant-id", required=True, help="Bound restaurant profile to read")
    test_customer_profile(parser.parse_args().restaurant_id)
