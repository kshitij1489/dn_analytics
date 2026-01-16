import sys
import os
import argparse
from typing import List, Dict, Any
import psycopg2
from psycopg2.extras import RealDictCursor

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.db_utils import create_postgresql_connection
from utils.id_generator import generate_deterministic_id

def get_unverified_items(conn) -> List[Dict[str, Any]]:
    """Fetch all unverified menu items"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT menu_item_id, name, type, created_at, suggestion_id 
        FROM menu_items 
        WHERE is_verified = FALSE
        ORDER BY name
    """
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()
    return results

def get_unverified_variants(conn) -> List[Dict[str, Any]]:
    """Fetch all unverified variants"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    query = """
        SELECT variant_id, variant_name, created_at 
        FROM variants 
        WHERE is_verified = FALSE
        ORDER BY variant_name
    """
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()
    return results

def verify_item(conn, item_id: str, new_name: str = None, new_type: str = None):
    """Mark an item as verified, optionally updating name/type"""
    cursor = conn.cursor()
    try:
        if new_name and new_type:
            # Check if this new name+type triggers a collision
            new_id = generate_deterministic_id(new_name, new_type)
            
            # If ID changes, we need to handle merge/move logic
            # For simplicity in this CLI, we'll just update blindly if no collision
            # In a real app, we'd use merge_menu_items logic
            cursor.execute("""
                UPDATE menu_items 
                SET name = %s, type = %s, is_verified = TRUE
                WHERE menu_item_id = %s
            """, (new_name, new_type, item_id))
        else:
            cursor.execute("UPDATE menu_items SET is_verified = TRUE WHERE menu_item_id = %s", (item_id,))
        
        conn.commit()
        print(f"✓ Item verified.")
    except Exception as e:
        conn.rollback()
        print(f"Error verifying item: {e}")

def main():
    print("="*60)
    print(" UNCLUSTERED DATA RESOLVER")
    print("="*60)
    
    try:
        conn = create_postgresql_connection(db_url="postgresql://postgres:postgres@localhost:5432/analytics")
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    items = get_unverified_items(conn)
    variants = get_unverified_variants(conn)
    
    print(f"Found {len(items)} unverified items and {len(variants)} unverified variants.")
    
    if not items and not variants:
        print("All clear! No unclustered data.")
        return

    # Fetch suggestions names
    cursor = conn.cursor()
    
    # Process Items
    for item in items:
        suggestion_text = ""
        if item.get('suggestion_id'):
            cursor.execute("SELECT name FROM menu_items WHERE menu_item_id = %s", (item['suggestion_id'],))
            s_name = cursor.fetchone()
            if s_name:
                suggestion_text = f" [Suggest: {s_name[0]}]"

        print(f"\n[ITEM] {item['name']} ({item['type']}){suggestion_text}")
        choice = input("  (v)erify / (r)ename / (u)se suggestion / (s)kip: ").lower()
        
        if choice == 'v':
            verify_item(conn, item['menu_item_id'])
        elif choice == 'r':
            new_name = input(f"  New Name [{item['name']}]: ").strip() or item['name']
            new_type = input(f"  New Type [{item['type']}]: ").strip() or item['type']
            verify_item(conn, item['menu_item_id'], new_name, new_type)
        elif choice == 'u' and suggestion_text:
            # Get the suggested name and type for verification
            cursor.execute("SELECT name, type FROM menu_items WHERE menu_item_id = %s", (item['suggestion_id'],))
            s_data = cursor.fetchone()
            if s_data:
                # Instead of just verifying, we should merge this item into the suggested one
                # but for simplicity in this CLI, we'll just rename it to match.
                # A better approach would be to call merge_menu_items.
                print(f"  Merging into '{s_data[0]}'...")
                from utils.menu_utils import merge_menu_items
                merge_menu_items(conn, item['menu_item_id'], item['suggestion_id'])
        else:
            print("  Skipped.")
    
    cursor.close()

    conn.close()

if __name__ == "__main__":
    main()
