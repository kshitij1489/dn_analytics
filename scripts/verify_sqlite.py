import argparse
import os
import sys

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

try:
    from src.core.db.connection import get_profile_connection
    from src.core.profiles import get_profile
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

def test_connection(restaurant_id: str):
    print(f"Testing SQLite Connection for restaurant {restaurant_id}...")
    try:
        conn, msg = get_profile_connection(get_profile(restaurant_id))
    except Exception as exc:
        print(f"❌ Connection Failed: {exc}")
        return False

    print(f"✅ Connection Successful: {msg}")
    
    # Test Schema Presence
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [row['name'] for row in tables] # accessing by name via Row factory
        
        print(f"found {len(table_names)} tables.")
        
        required = ['orders', 'customers', 'menu_items']
        missing = [t for t in required if t not in table_names]
        
        if missing:
            print(f"⚠️ Missing expected tables: {missing}")
            print("Run the application/profile initialization flow to apply the schema.")
            return False
        else:
            print("✅ Core tables found.")
            
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return False
    finally:
        conn.close()
        
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify one restaurant profile database")
    parser.add_argument("--restaurant-id", required=True, help="Bound restaurant profile to verify")
    sys.exit(0 if test_connection(parser.parse_args().restaurant_id) else 1)
