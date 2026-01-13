
"""
Menu Manager
Provides functionality to sync menu data from CSV to Database.
"""
import os
import sys
from pathlib import Path
from typing import Optional, Dict

# Add parent directory to path to handle imports if run directly
# Logic: valid if this file is in database/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.load_menu_data import (
    load_cleaned_menu,
    generate_python_dicts
)

# Constants
CSV_PATH = Path(__file__).parent.parent / "data" / "cleaned_menu.csv"

def sync_menu(conn, csv_path: Optional[str] = None):
    """
    Syncs the database menu tables with the data from the CSV file.
    
    Args:
        conn: Database connection object
        csv_path: Optional path to cleaned_menu.csv. Defaults to data/cleaned_menu.csv
        
    Returns:
        Dict with stats about the sync operation
    """
    if csv_path is None:
        csv_path = str(CSV_PATH)
        
    if not os.path.exists(csv_path):
        return {"status": "error", "message": f"Menu file not found at {csv_path}"}
        
    try:
        # 0. Import parsing table from CSV first to restore any merges/verifications
        from utils.menu_utils import import_parsing_table_from_csv
        import_parsing_table_from_csv(conn)
        
        # 1. Load data
        menu_data = load_cleaned_menu(csv_path)
        
        # 2. Generate structures
        menu_items_data, variants_data, menu_item_variants_data = generate_python_dicts(menu_data)
        
        # 3. Handle Aliases (Version 2)
        # If an item in the CSV is already mapped to a different canonical name in 
        # the item_parsing_table, we should skip it to prevent duplicates re-appearing.
        cursor = conn.cursor()
        cursor.execute("SELECT raw_name FROM item_parsing_table WHERE raw_name != cleaned_name")
        aliases = {row[0].lower() for row in cursor.fetchall()}
        
        # 4. Insert Menu Items
        # Filter out items that are actually aliases
        filtered_menu_items = [
            item for item in menu_items_data 
            if item['name'].lower() not in aliases
        ]
        
        from psycopg2.extras import execute_values
        
        # Menu Items
        values = [(item['name'], item['type'], item['is_active']) for item in filtered_menu_items]
        if values:
            execute_values(
                cursor,
                """
                INSERT INTO menu_items (name, type, is_active)
                VALUES %s
                ON CONFLICT (name, type) DO UPDATE 
                SET is_active = EXCLUDED.is_active, 
                    updated_at = CURRENT_TIMESTAMP
                """,
                values
            )
        
        # Variants
        values = [(v['variant_name'],) for v in variants_data]
        if values:
            execute_values(
                cursor,
                """
                INSERT INTO variants (variant_name)
                VALUES %s
                ON CONFLICT (variant_name) DO NOTHING
                """,
                values
            )
            
        conn.commit()
        
        # Refetch IDs to map correctly
        # This is more robust than assuming ID order
        cursor.execute("SELECT name, type, menu_item_id FROM menu_items")
        menu_map = {(row[0], row[1]): row[2] for row in cursor.fetchall()}
        
        cursor.execute("SELECT variant_name, variant_id FROM variants")
        variant_map = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Menu Item Variants
        miv_values = []
        for item in menu_data:
            m_id = menu_map.get((item['name'], item['type']))
            v_id = variant_map.get(item['variant'])
            
            if m_id and v_id:
                # Re-calculate eligibility (logic reused from load_menu_data via generate_python_dicts 
                # but we need to call it again or trust the list from generate_python_dicts?)
                # Actually generate_python_dicts returns a list where 'menu_item_id' is an INDEX + 1.
                # That logic is fragile if we are merging with existing DB data.
                # So we should re-construct the list using the ACTUAL IDs we just fetched.
                
                # We can reuse the helper functions from load_menu_data if we import them
                from database.load_menu_data import determine_addon_eligibility, determine_delivery_eligibility
                
                addon = determine_addon_eligibility(item['name'], item['type'], item['variant'])
                delivery = determine_delivery_eligibility(item['name'], item['type'], item['variant'])
                
                miv_values.append((
                    m_id, v_id, 0.00, True, addon, delivery
                ))
        
        if miv_values:
            execute_values(
                cursor,
                """
                INSERT INTO menu_item_variants 
                (menu_item_id, variant_id, price, is_active, addon_eligible, delivery_eligible)
                VALUES %s
                ON CONFLICT (menu_item_id, variant_id) DO UPDATE 
                SET addon_eligible = EXCLUDED.addon_eligible,
                    delivery_eligible = EXCLUDED.delivery_eligible,
                    updated_at = CURRENT_TIMESTAMP
                """,
                miv_values
            )
            
        conn.commit()
        cursor.close()
        
        return {
            "status": "success",
            "menu_items": len(menu_items_data),
            "variants": len(variants_data),
            "combinations": len(miv_values)
        }
        
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        return {"status": "error", "message": str(e)}
