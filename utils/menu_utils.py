"""
Menu Management Utilities (SQLite Version)
"""

import csv
import os
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
import json
from scripts.seed_from_backups import export_to_backups
from utils.id_generator import generate_deterministic_id


def _build_in_clause(items: List[str]) -> Tuple[str, List[str]]:
    """Helper to build IN (?, ?, ...) clause for SQLite."""
    if not items:
        return "IN ('')", []  # Return something that matches nothing
    placeholders = ",".join("?" * len(items))
    return f"IN ({placeholders})", list(items)


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
        cursor.execute("SELECT name, type, total_sold, total_revenue, sold_as_item, sold_as_addon FROM menu_items WHERE menu_item_id = ?", (source_id,))
        source = cursor.fetchone()
        
        cursor.execute("SELECT name, type, total_sold, total_revenue, sold_as_item, sold_as_addon FROM menu_items WHERE menu_item_id = ?", (target_id,))
        target = cursor.fetchone()
        
        if not source or not target:
            return {"status": "error", "message": "Source or Target item not found"}
            
        source_name, source_type, source_sold, source_revenue, source_as_item, source_as_addon = source
        target_name, target_type, target_sold, target_revenue, target_as_item, target_as_addon = target
        
        # 1.5 Record History (Collect affected order_item_ids from mappings)
        cursor.execute("SELECT order_item_id FROM menu_item_variants WHERE menu_item_id = ?", (source_id,))
        affected_ids = [row[0] for row in cursor.fetchall()]
        
        cursor.execute("""
            INSERT INTO merge_history (source_id, target_id, source_name, source_type, affected_order_items)
            VALUES (?, ?, ?, ?, ?)
        """, (source_id, target_id, source_name, source_type, json.dumps(affected_ids)))
        
        # 2. Update Target Stats
        cursor.execute("""
            UPDATE menu_items 
            SET total_sold = COALESCE(total_sold, 0) + ?,
                sold_as_item = COALESCE(sold_as_item, 0) + ?,
                sold_as_addon = COALESCE(sold_as_addon, 0) + ?,
                total_revenue = COALESCE(total_revenue, 0) + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE menu_item_id = ?
        """, (source_sold or 0, source_as_item or 0, source_as_addon or 0, source_revenue or 0, target_id))
        
        # 3. Relink Order Items
        cursor.execute("""
            UPDATE order_items 
            SET menu_item_id = ? 
            WHERE menu_item_id = ?
        """, (target_id, source_id))
        relinked_count = cursor.rowcount
        
        # 4. Relink Order Item Addons
        cursor.execute("""
            UPDATE order_item_addons 
            SET menu_item_id = ? 
            WHERE menu_item_id = ?
        """, (target_id, source_id))
        
        # 5. Relink Mappings
        cursor.execute("""
            UPDATE menu_item_variants 
            SET menu_item_id = ? 
            WHERE menu_item_id = ?
        """, (target_id, source_id))
        mappings_updated = cursor.rowcount

        # 6. Delete Source Item
        cursor.execute("DELETE FROM menu_items WHERE menu_item_id = ?", (source_id,))
        
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
        # 1. Update Mapping (SQLite UPSERT)
        cursor.execute("""
            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
            VALUES (?, ?, ?, 1)
            ON CONFLICT (order_item_id) DO UPDATE SET
                menu_item_id = excluded.menu_item_id,
                variant_id = excluded.variant_id,
                is_verified = 1
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
        cursor.execute("SELECT menu_item_id FROM menu_items WHERE menu_item_id = ?", (target_id,))
        exists = cursor.fetchone()
        
        if not exists:
            # Create Target
            cursor.execute("""
                INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                VALUES (?, ?, ?, 1)
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
    4. Recalculate stats for both items
    5. Delete history record
    """
    cursor = conn.cursor()
    try:
        # 1. Get History Entry
        cursor.execute("SELECT * FROM merge_history WHERE merge_id = ?", (merge_id,))
        history = cursor.fetchone()
        if not history:
            return {"status": "error", "message": "Merge history record not found"}
        
        # Convert Row to dict for easier access
        history_dict = dict(history)
        source_id = history_dict['source_id']
        target_id = history_dict['target_id']
        affected_ids_json = history_dict['affected_order_items']
        
        # Parse JSON list
        affected_ids = json.loads(affected_ids_json) if isinstance(affected_ids_json, str) else affected_ids_json
        
        # 2. Re-insert Source Item (SQLite UPSERT with INSERT OR IGNORE)
        cursor.execute("""
            INSERT OR IGNORE INTO menu_items (menu_item_id, name, type, is_verified)
            VALUES (?, ?, ?, 1)
        """, (source_id, history_dict['source_name'], history_dict['source_type']))
        
        # 3. Relink Mappings (menu_item_variants)
        if affected_ids:
            in_clause, params = _build_in_clause(affected_ids)
            cursor.execute(f"""
                UPDATE menu_item_variants 
                SET menu_item_id = ? 
                WHERE order_item_id {in_clause}
            """, [source_id] + params)
            
            # 4. Relink Order Items (affected_ids are menu_item_variants.order_item_id; match order_items.order_item_id)
            cursor.execute(f"""
                UPDATE order_items 
                SET menu_item_id = ? 
                WHERE order_item_id {in_clause}
            """, [source_id] + params)

            # 5. Relink Order Item Addons (same order_item_id set)
            cursor.execute(f"""
                UPDATE order_item_addons 
                SET menu_item_id = ? 
                WHERE order_item_id {in_clause}
            """, [source_id] + params)
        
        # 6. Recalculate Stats (Filtered by Success status)
        for mid in [target_id, source_id]:
            cursor.execute("""
                UPDATE menu_items 
                SET total_sold = (
                        (SELECT COALESCE(SUM(oi.quantity), 0) 
                         FROM order_items oi 
                         JOIN orders o ON oi.order_id = o.order_id 
                         WHERE oi.menu_item_id = ? AND o.order_status = 'Success') +
                        (SELECT COALESCE(SUM(oia.quantity), 0) 
                         FROM order_item_addons oia 
                         JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                         JOIN orders o ON oi.order_id = o.order_id
                         WHERE oia.menu_item_id = ? AND o.order_status = 'Success')
                    ),
                    total_revenue = (
                        (SELECT COALESCE(SUM(oi.total_price), 0) 
                         FROM order_items oi 
                         JOIN orders o ON oi.order_id = o.order_id 
                         WHERE oi.menu_item_id = ? AND o.order_status = 'Success') +
                        (SELECT COALESCE(SUM(oia.price * oia.quantity), 0) 
                         FROM order_item_addons oia 
                         JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                         JOIN orders o ON oi.order_id = o.order_id
                         WHERE oia.menu_item_id = ? AND o.order_status = 'Success')
                    ),
                    sold_as_item = (SELECT COALESCE(SUM(oi.quantity), 0) 
                                   FROM order_items oi 
                                   JOIN orders o ON oi.order_id = o.order_id 
                                   WHERE oi.menu_item_id = ? AND o.order_status = 'Success'),
                    sold_as_addon = (SELECT COALESCE(SUM(oia.quantity), 0) 
                                    FROM order_item_addons oia 
                                    JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                                    JOIN orders o ON oi.order_id = o.order_id
                                    WHERE oia.menu_item_id = ? AND o.order_status = 'Success'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE menu_item_id = ?
            """, (mid, mid, mid, mid, mid, mid, mid))

        
        # 7. Delete History
        cursor.execute("DELETE FROM merge_history WHERE merge_id = ?", (merge_id,))
        
        conn.commit()
        
        # 8. Update Backups
        export_to_backups(conn)
        
        return {"status": "success", "message": f"Successfully reversed merge of '{history_dict['source_name']}'"}
        
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Undo failed: {e}"}
    finally:
        cursor.close()

def verify_item(conn, item_id: str, new_name: str = None, new_type: str = None) -> Dict[str, Any]:
    """Mark an item as verified, optionally updating name/type"""
    cursor = conn.cursor()
    try:
        if new_name and new_type:
            # Check if this new name+type triggers a collision
            new_id = generate_deterministic_id(new_name, new_type)
            
            # If ID changes, we need to handle merge/move logic
            # For simplicity, if ID matches existing, we merge. If not, we rename.
            
            cursor.execute("SELECT menu_item_id FROM menu_items WHERE menu_item_id = ?", (new_id,))
            exists = cursor.fetchone()
            
            if exists and exists[0] != item_id:
                # Merge current item into the existing target
                return merge_menu_items(conn, item_id, new_id)
            else:
                 # Just rename and verify
                cursor.execute("""
                    UPDATE menu_items 
                    SET name = ?, type = ?, is_verified = 1
                    WHERE menu_item_id = ?
                """, (new_name, new_type, item_id))
        else:
            cursor.execute("UPDATE menu_items SET is_verified = 1 WHERE menu_item_id = ?", (item_id,))
        
        conn.commit()
        export_to_backups(conn)
        return {"status": "success", "message": "Item verified successfully"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Verification failed: {e}"}
    finally:
        cursor.close()
