
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from src.core.db.connection import get_db_connection
from src.core.queries import customer_queries

def test_customer_profile_enhancements():
    print("Testing Customer Profile Enhancements...")
    
    conn, msg = get_db_connection()
    if conn is None:
        print(f"DB Connection Failed: {msg}")
        return
    print(f"DB Connection: {msg}")

    # 1. Test Search
    print("\n1. Testing Search ('a')...")
    results = customer_queries.search_customers(conn, 'a', limit=1)
    if results:
        cid = results[0]['customer_id']
        print(f"Testing Profile Fetch for ID: {cid}...")
        customer, orders = customer_queries.fetch_customer_profile_data(conn, cid)
        
        if customer:
            print(f"Customer: {customer['name']}")
            print(f"Is Verified: {customer['is_verified']} (Type: {type(customer['is_verified'])})")
            
            if orders:
                print(f"Latest Order Source: {orders[0]['order_source']}")
                print(f"Latest Order ID: {orders[0]['order_id']}")
        else:
            print("Failed to fetch profile!")
    else:
        print("No customers found.")

    conn.close()

if __name__ == "__main__":
    test_customer_profile_enhancements()
