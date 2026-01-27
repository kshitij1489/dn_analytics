import os
import sys

# Add project root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

try:
    from src.core.db.connection import get_db_connection
    from src.core.db.reset import reset_database
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

def test_connection():
    print("Testing SQLite Connection...")
    conn, msg = get_db_connection()
    if not conn:
        print(f"❌ Connection Failed: {msg}")
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
            print("Attempting reset...")
            success, r_msg = reset_database()
            if success:
                print(f"✅ {r_msg}")
            else:
                print(f"❌ Reset Failed: {r_msg}")
        else:
            print("✅ Core tables found.")
            
    except Exception as e:
        print(f"❌ Query Error: {e}")
        return False
    finally:
        conn.close()
        
    return True

if __name__ == "__main__":
    test_connection()
