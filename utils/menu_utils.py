"""
Menu Management Utilities
"""

import csv
import os
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from scripts.seed_from_backups import export_to_backups
from utils.id_generator import generate_deterministic_id



def merge_menu_items(conn, source_id: str, target_id: str, adopt_source_prices: bool = False) -> Dict[str, Any]:
    """
    Merge source_id (UUID) into target_id (UUID).
    
    Actions:
    1. Transfer stats (revenue, sold count)
    2. Re-link order_items
    3. Re-link order_item_addons
    4. Re-link item mappings (menu_item_variants)
    5. Delete source item
    """
    if source_id == target_id:
        return {"status": "error", "message": "Cannot merge item into itself"}
        
    cursor = conn.cursor()
    try:
        # 1. Get Details
        cursor.execute("SELECT name, type, total_sold, total_revenue, sold_as_item, sold_as_addon FROM menu_items WHERE menu_item_id = %s", (source_id,))
        source = cursor.fetchone()
        
        cursor.execute("SELECT name, type, total_sold, total_revenue, sold_as_item, sold_as_addon FROM menu_items WHERE menu_item_id = %s", (target_id,))
        target = cursor.fetchone()
        
        if not source or not target:
            return {"status": "error", "message": "Source or Target item not found"}
            
        source_name, source_type, source_sold, source_revenue, source_as_item, source_as_addon = source
        target_name, target_type, target_sold, target_revenue, target_as_item, target_as_addon = target
        
        # 1.5 Record History (Collect affected order_item_ids from mappings)
        cursor.execute("SELECT order_item_id FROM menu_item_variants WHERE menu_item_id = %s", (source_id,))
        affected_ids = [row[0] for row in cursor.fetchall()]
        
        cursor.execute("""
            INSERT INTO merge_history (source_id, target_id, source_name, source_type, affected_order_items)
            VALUES (%s, %s, %s, %s, %s)
        """, (source_id, target_id, source_name, source_type, json.dumps(affected_ids)))
        
        # 2. Update Target Stats
        cursor.execute("""
            UPDATE menu_items 
            SET total_sold = COALESCE(total_sold, 0) + %s,
                sold_as_item = COALESCE(sold_as_item, 0) + %s,
                sold_as_addon = COALESCE(sold_as_addon, 0) + %s,
                total_revenue = COALESCE(total_revenue, 0) + %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = %s
        """, (source_sold or 0, source_as_item or 0, source_as_addon or 0, source_revenue or 0, target_id))
        
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
        
        # 5. Relink Mappings
        cursor.execute("""
            UPDATE menu_item_variants 
            SET menu_item_id = %s 
            WHERE menu_item_id = %s
        """, (target_id, source_id))
        mappings_updated = cursor.rowcount

        # 6. Delete Source Item
        cursor.execute("DELETE FROM menu_items WHERE menu_item_id = %s", (source_id,))
        
        conn.commit()
        
        # 7. Update Backups
        export_to_backups(conn)

        return {
            "status": "success", 
            "message": f"Merged '{source_name}' into '{target_name}'",
            "stats": {
                "orders_relinked": relinked_count,
                "mappings_updated": mappings_updated,
                "revenue_added": float(source_revenue or 0)
            }
        }
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()


def remap_order_item_cluster(conn, order_item_id: str, new_menu_item_id: str, new_variant_id: str) -> Dict[str, Any]:
    """
    Remap an individual order item to a different menu_item cluster.
    """
    cursor = conn.cursor()
    try:
        # 1. Update Mapping
        cursor.execute("""
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (order_item_id) DO UPDATE SET
                menu_item_id = EXCLUDED.menu_item_id,
                variant_id = EXCLUDED.variant_id,
                is_verified = TRUE
        """, (order_item_id, new_menu_item_id, new_variant_id))
        
        conn.commit()
        
        # 2. Update Backups
        export_to_backups(conn)
        
        return {"status": "success", "message": "Order item remapped successfully"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        cursor.close()

def resolve_item_rename(conn, source_id: str, new_name: str, new_type: str) -> Dict[str, Any]:
    """
    Handle resolution where an item is renamed.
    This effectively means:
    1. Generate ID for new name/type
    2. Check if that target item exists
    3. If not, create it (verified)
    4. Merge source into target
    """
    cursor = conn.cursor()
    try:
        target_id = generate_deterministic_id(new_name, new_type)
        
        # Check existence
        cursor.execute("SELECT menu_item_id FROM menu_items WHERE menu_item_id = %s", (target_id,))
        exists = cursor.fetchone()
        
        if not exists:
            # Create Target
            cursor.execute("""
                INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                VALUES (%s, %s, %s, TRUE)
            """, (target_id, new_name, new_type))
            conn.commit() # Commit creation effectively
            
        # Merge Source -> Target
        # Note: This handles the full merge logic including deletions
        return merge_menu_items(conn, source_id, target_id)
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Resolution failed: {e}"}
    finally:
        cursor.close()

def undo_merge(conn, merge_id: int) -> Dict[str, Any]:
    """
    Reverse a merge operation.
    1. Re-insert source menu item
    2. Point affected mappings back to source item
    3. Point affected order_items/addons back to source item
    4. Deduct stats from target item
    5. Delete history record
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Get History Entry
        cursor.execute("SELECT * FROM merge_history WHERE merge_id = %s", (merge_id,))
        history = cursor.fetchone()
        if not history:
            return {"status": "error", "message": "Merge history record not found"}
        
        source_id = history['source_id']
        target_id = history['target_id']
        affected_ids = history['affected_order_items'] # List of strings
        
        # 2. Re-insert Source Item
        cursor.execute("""
            INSERT INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (menu_item_id) DO NOTHING
        """, (source_id, history['source_name'], history['source_type']))
        
        # 3. Relink Mappings (menu_item_variants)
        if affected_ids:
            cursor.execute("""
                UPDATE menu_item_variants 
                SET menu_item_id = %s 
                WHERE order_item_id = ANY(%s)
            """, (source_id, affected_ids))
            
            # 4. Relink Order Items
            cursor.execute("""
                UPDATE order_items 
                SET menu_item_id = %s 
                WHERE petpooja_itemid::text = ANY(%s)
            """, (source_id, affected_ids))

            # 5. Relink Order Item Addons
            cursor.execute("""
                UPDATE order_item_addons 
                SET menu_item_id = %s 
                WHERE petpooja_addonid = ANY(%s)
            """, (source_id, affected_ids))
        
        # 6. Recalculate Stats (Deducting from target is tricky, let's just refresh both)
        # Actually, let's just deduct what we transferred if we knew it, 
        # but recalculating is safer if we have the orders.
        # For now, let's just do a simple reverse of the stats transfer.
        # We didn't store the exact counts transferred at that moment, 
        # but we can query them now from the re-linked items.
        
        # 6. Recalculate Stats (Filtered by Success status)
        for mid in [target_id, source_id]:
            cursor.execute("""
                UPDATE menu_items 
                SET total_sold = (
                        (SELECT COALESCE(SUM(oi.quantity), 0) 
                         FROM order_items oi 
                         JOIN orders o ON oi.order_id = o.order_id 
                         WHERE oi.menu_item_id = %s AND o.order_status = 'Success') +
                        (SELECT COALESCE(SUM(oia.quantity), 0) 
                         FROM order_item_addons oia 
                         JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                         JOIN orders o ON oi.order_id = o.order_id
                         WHERE oia.menu_item_id = %s AND o.order_status = 'Success')
                    ),
                    total_revenue = (
                        (SELECT COALESCE(SUM(oi.total_price), 0) 
                         FROM order_items oi 
                         JOIN orders o ON oi.order_id = o.order_id 
                         WHERE oi.menu_item_id = %s AND o.order_status = 'Success') +
                        (SELECT COALESCE(SUM(oia.price * oia.quantity), 0) 
                         FROM order_item_addons oia 
                         JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                         JOIN orders o ON oi.order_id = o.order_id
                         WHERE oia.menu_item_id = %s AND o.order_status = 'Success')
                    ),
                    sold_as_item = (SELECT COALESCE(SUM(oi.quantity), 0) 
                                   FROM order_items oi 
                                   JOIN orders o ON oi.order_id = o.order_id 
                                   WHERE oi.menu_item_id = %s AND o.order_status = 'Success'),
                    sold_as_addon = (SELECT COALESCE(SUM(oia.quantity), 0) 
                                    FROM order_item_addons oia 
                                    JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                                    JOIN orders o ON oi.order_id = o.order_id
                                    WHERE oia.menu_item_id = %s AND o.order_status = 'Success'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = %s
            """, (mid, mid, mid, mid, mid, mid, mid))


        
        # 7. Delete History
        cursor.execute("DELETE FROM merge_history WHERE merge_id = %s", (merge_id,))
        
        conn.commit()
        
        # 8. Update Backups
        export_to_backups(conn)
        
        return {"status": "success", "message": f"Successfully reversed merge of '{history['source_name']}'"}
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Undo failed: {e}"}
    finally:
        cursor.close()
