"""
Menu Management Utilities
"""

import csv
import os
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import psycopg2
from psycopg2.extras import RealDictCursor

def get_menu_item(conn, menu_item_id: int) -> Dict[str, Any]:
    """Get menu item details"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM menu_items WHERE menu_item_id = %s", (menu_item_id,))
    result = cursor.fetchone()
    cursor.close()
    return result

def merge_menu_items(conn, source_id: int, target_id: int, adopt_source_prices: bool = False) -> Dict[str, Any]:
    """
    Merge source_id into target_id.
    
    Actions:
    1. Transfer stats (revenue, sold count)
    2. Re-link order_items
    3. Update parsing table (cleaned_name mappings)
    4. Transfer variants (Adopting source prices if requested)
    5. Delete source item
    """
    if source_id == target_id:
        return {"status": "error", "message": "Cannot merge item into itself"}
        
    cursor = conn.cursor()
    try:
        # 1. Get Details
        cursor.execute("SELECT name, type, total_sold, total_revenue FROM menu_items WHERE menu_item_id = %s", (source_id,))
        source = cursor.fetchone()
        
        cursor.execute("SELECT name, type, total_sold, total_revenue FROM menu_items WHERE menu_item_id = %s", (target_id,))
        target = cursor.fetchone()
        
        if not source or not target:
            return {"status": "error", "message": "Source or Target item not found"}
            
        source_name, source_type, source_sold, source_revenue = source
        target_name, target_type, target_sold, target_revenue = target
        
        # 2. Update Target Stats
        cursor.execute("""
            UPDATE menu_items 
            SET total_sold = COALESCE(total_sold, 0) + %s,
                total_revenue = COALESCE(total_revenue, 0) + %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = %s
        """, (source_sold or 0, source_revenue or 0, target_id))
        
        # 3. Relink Order Items
        cursor.execute("""
            UPDATE order_items 
            SET menu_item_id = %s 
            WHERE menu_item_id = %s
        """, (target_id, source_id))
        
        relinked_count = cursor.rowcount
        
        # 4. Relink Order Item Addons
        cursor.execute("""
            UPDATE order_item_addons 
            SET menu_item_id = %s 
            WHERE menu_item_id = %s
        """, (target_id, source_id))
        
        # 5. Update Parsing Table
        # Update rows that mapped to Source Name/Type to now map to Target Name/Type
        cursor.execute("""
            UPDATE item_parsing_table
            SET cleaned_name = %s,
                type = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE cleaned_name = %s AND type = %s
        """, (target_name, target_type, source_name, source_type))
        
        updated_parsing = cursor.rowcount
        
        # 6. Handle Pricing & Variants
        if adopt_source_prices:
            # Update target prices for variants that both items have
            cursor.execute("""
                UPDATE menu_item_variants tv
                SET price = sv.price,
                    updated_at = CURRENT_TIMESTAMP
                FROM menu_item_variants sv
                WHERE sv.menu_item_id = %s 
                AND tv.menu_item_id = %s
                AND sv.variant_id = tv.variant_id
            """, (source_id, target_id))
            prices_adopted = cursor.rowcount
        else:
            prices_adopted = 0

        # Transfer unique variants that the target doesn't have at all
        cursor.execute("""
            UPDATE menu_item_variants
            SET menu_item_id = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = %s
            AND variant_id NOT IN (
                SELECT variant_id FROM menu_item_variants WHERE menu_item_id = %s
            )
        """, (target_id, source_id, target_id))
        
        variants_transferred = cursor.rowcount
        
        # 7. Delete Source Item and Remaining Variant Links
        cursor.execute("DELETE FROM menu_item_variants WHERE menu_item_id = %s", (source_id,))
        cursor.execute("DELETE FROM menu_items WHERE menu_item_id = %s", (source_id,))
        
        conn.commit()
        
        # 8. Sync to CSV for persistence across rebuilds
        try:
            export_parsing_table_to_csv(conn)
        except Exception as csv_error:
            print(f"Warning: Failed to sync parsing table to CSV: {csv_error}")
            
        return {
            "status": "success", 
            "message": f"Merged '{source_name}' into '{target_name}'",
            "stats": {
                "orders_relinked": relinked_count,
                "parsing_updated": updated_parsing,
                "variants_transferred": variants_transferred,
                "prices_adopted": prices_adopted,
                "revenue_added": float(source_revenue or 0)
            }
        }
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()

def export_parsing_table_to_csv(conn, csv_path: Optional[str] = None):
    """
    Export the current item_parsing_table from DB back to the CSV file.
    Ensures that persistence is maintained across rebuilds.
    """
    if csv_path is None:
        csv_path = str(Path(__file__).parent.parent / "data" / "item_parsing_table.csv")
        
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT raw_name, cleaned_name, type, variant, is_verified 
            FROM item_parsing_table 
            ORDER BY id ASC
        """)
        rows = cursor.fetchall()
        
        if not rows:
            return
            
        # Ensure directory exists
        Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
                
        return True
    finally:
        cursor.close()

def import_parsing_table_from_csv(conn, csv_path: Optional[str] = None):
    """
    Import mappings from CSV into the database.
    This ensures that merges/verifications are restored after a 'make clean'.
    """
    if csv_path is None:
        csv_path = str(Path(__file__).parent.parent / "data" / "item_parsing_table.csv")
        
    if not os.path.exists(csv_path):
        return False
        
    cursor = conn.cursor()
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            values = []
            for row in reader:
                values.append((
                    row['raw_name'],
                    row['cleaned_name'],
                    row['type'],
                    row['variant'],
                    row['is_verified'].lower() == 'true'
                ))
                
            if values:
                from psycopg2.extras import execute_values
                execute_values(
                    cursor,
                    """
                    INSERT INTO item_parsing_table (raw_name, cleaned_name, type, variant, is_verified)
                    VALUES %s
                    ON CONFLICT (raw_name) DO UPDATE 
                    SET cleaned_name = EXCLUDED.cleaned_name,
                        type = EXCLUDED.type,
                        variant = EXCLUDED.variant,
                        is_verified = EXCLUDED.is_verified,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    values
                )
                conn.commit()
        return True
    except Exception as e:
        print(f"Error importing parsing table: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
