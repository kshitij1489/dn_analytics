
import json
import os
import sys
import uuid
import sqlite3
from pathlib import Path

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.core.db.connection import get_db_connection
from src.core.utils.path_helper import get_resource_path

def perform_seeding(conn):
    """Restore menu data from JSON backups in data/ (cluster_state_backup.json, id_maps_backup.json)."""
    # Paths
    archive_dir = Path(get_resource_path("data"))
    cluster_state_path = archive_dir / "cluster_state_backup.json"
    id_maps_path = archive_dir / "id_maps_backup.json"
    
    if not cluster_state_path.exists() or not id_maps_path.exists():
        print(f"Error: Backup files not found in {archive_dir}")
        return False

    # Load JSON files
    with open(id_maps_path, 'r') as f:
        id_maps = json.load(f)
    
    with open(cluster_state_path, 'r') as f:
        cluster_state = json.load(f)

    cursor = conn.cursor()
    
    try:
        # 1. Insert Menu Items
        print("Seeding Menu Items...")
        menu_items_count = 0
        for menu_item_id, clean_name in id_maps.get("menu_id_to_str", {}).items():
            # For each menu item, we need to know its type.
            item_type = "Dessert" # Default
            for key in cluster_state.keys():
                if key.startswith(menu_item_id + ":"):
                    parts = key.split(":")
                    if len(parts) > 1:
                        type_id = parts[1]
                        item_type = id_maps.get("type_id_to_str", {}).get(type_id, "Dessert")
                    break
            
            cursor.execute("""
                INSERT INTO menu_items (menu_item_id, name, type, is_verified)
                VALUES (?, ?, ?, 1)
                ON CONFLICT (menu_item_id) DO UPDATE SET 
                    name = excluded.name,
                    type = excluded.type,
                    is_verified = 1
            """, (menu_item_id, clean_name, item_type))
            menu_items_count += 1
        
        # 2. Insert Variants
        print("Seeding Variants...")
        
        # Hardcoded updates for units and values
        variant_updates = {
            "4398d6ab-f481-5179-9903-084067113cc0": {"unit": "GMS", "value": 1000},
            "f8b92f1e-8f3b-5a1c-8615-215dd0b3a4cc": {"unit": "COUNT", "value": 1},
            "41b9844c-f8f1-543a-8f80-fec32f9f18e3": {"unit": "GMS", "value": 250},
            "5f354550-0f38-58c3-ad16-97672a66817d": {"unit": "COUNT", "value": 2},
            "4442990c-72cc-5474-a4f2-1a429caac09a": {"unit": "GMS", "value": 400},
            "d755256f-e108-56e3-b125-be7736f091af": {"unit": "ML", "value": 400},
            "190eb2e5-5671-55be-ac33-57907b87a2ce": {"unit": "COUNT", "value": 1},
            "c5bd4518-50da-59ee-b63d-50c4904a9912": {"unit": "ML", "value": 600},
            "ad6587f3-b7c1-536c-88cb-c48b0b610ada": {"unit": "ML", "value": 700},
            "b43993c2-8f3b-541e-af64-c9599eba6e7d": {"unit": "ML", "value": 725},
            "e4d57a7d-d262-5fd8-98cb-62ae69804b8d": {"unit": "GMS", "value": 60},
            "a1df2a57-b94a-56db-b890-3cba1e7aa15c": {"unit": "GMS", "value": 160},
            "74f43046-a2ff-5e69-9b78-1724b6f0a030": {"unit": "ML", "value": 200},
            "0a41ed3b-37e4-540b-bc5c-407631835802": {"unit": "GMS", "value": 200},
            "c6438ece-1c0e-5db1-860f-27f45090a616": {"unit": "ML", "value": 300},
            "b747b32a-ee01-59b9-b443-75581bb57863": {"unit": "GMS", "value": 120},
            "e1b8037f-345a-52d6-ae94-cc115490705a": {"unit": "GMS", "value": 220},
            "95cd7af2-383e-5449-893f-83f53bb658bf": {"unit": "ML", "value": 300},
            "f2a1ea5a-2b0b-562d-8c50-808760640024": {"unit": "COUNT", "value": 1}
        }

        variants_count = 0
        for variant_id, variant_name in id_maps.get("variant_id_to_str", {}).items():
            extra = variant_updates.get(variant_id, {"unit": None, "value": None})
            
            cursor.execute("""
                INSERT INTO variants (variant_id, variant_name, unit, value, is_verified)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT (variant_id) DO UPDATE SET 
                    variant_name = excluded.variant_name,
                    unit = excluded.unit,
                    value = excluded.value,
                    is_verified = 1
            """, (variant_id, variant_name, extra['unit'], extra['value']))
            variants_count += 1
            
        # 3. Insert Mappings (menu_item_variants)
        print("Seeding Mappings...")
        mappings_count = 0
        for key, orders in cluster_state.items():
            menu_item_id = key.split(":")[0]
            for order_item_id, items in orders.items():
                seen_variants = set()
                for _, variant_id in items:
                    if variant_id not in seen_variants:
                        cursor.execute("""
                            INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
                            VALUES (?, ?, ?, 1)
                            ON CONFLICT (order_item_id) DO UPDATE SET
                                menu_item_id = excluded.menu_item_id,
                                variant_id = excluded.variant_id,
                                is_verified = 1
                        """, (str(order_item_id), menu_item_id, variant_id))
                        seen_variants.add(variant_id)
                        mappings_count += 1
        
        conn.commit()
        print(f"Successfully seeded: {menu_items_count} items, {variants_count} variants, {mappings_count} mappings")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"Error during seeding: {e}")
        return False
    finally:
        cursor.close()


def export_to_backups(conn):
    """Dump database state to JSON backups in data/ (cluster_state_backup.json, id_maps_backup.json)."""
    archive_dir = Path(get_resource_path("data"))
    archive_dir.mkdir(parents=True, exist_ok=True)
    
    cluster_state_path = archive_dir / "cluster_state_backup.json"
    id_maps_path = archive_dir / "id_maps_backup.json"
    
    cursor = conn.cursor()
    
    try:
        # 1. Generate id_maps
        print("Exporting ID Maps...")
        id_maps = {
            "menu_id_to_str": {},
            "variant_id_to_str": {},
            "type_id_to_str": {}
        }
        
        # Menu Items
        cursor.execute("SELECT menu_item_id, name, type FROM menu_items")
        type_to_id = {}
        for mid, name, mtype in cursor.fetchall():
            id_maps["menu_id_to_str"][str(mid)] = name
            if mtype not in type_to_id:
                # We need deterministic type IDs if we want consistency, 
                # but for seeding back, simple mapping is enough.
                # However, cluster_state uses type IDs. 
                # Let's try to find existing ones or generate.
                from utils.id_generator import generate_deterministic_id
                tid = generate_deterministic_id(mtype)
                type_to_id[mtype] = tid
                id_maps["type_id_to_str"][tid] = mtype
                
        # Variants
        cursor.execute("SELECT variant_id, variant_name FROM variants")
        for vid, vname in cursor.fetchall():
            id_maps["variant_id_to_str"][str(vid)] = vname

        # 2. Generate cluster_state
        print("Exporting Cluster State...")
        cluster_state = {}
        cursor.execute("""
            SELECT mv.menu_item_id, mi.type, mv.order_item_id, mv.variant_id
            FROM menu_item_variants mv
            JOIN menu_items mi ON mv.menu_item_id = mi.menu_item_id
        """)
        for mid, mtype, oid, vid in cursor.fetchall():
            tid = type_to_id.get(mtype, "unknown")
            key = f"{mid}:{tid}"
            if key not in cluster_state:
                cluster_state[key] = {}
            if str(oid) not in cluster_state[key]:
                cluster_state[key][str(oid)] = []
            
            # The original format was list of [original_name, variant_id]
            # We don't have original_name easily here unless we join with order_items,
            # but for restoration, variant_id is the key part.
            cluster_state[key][str(oid)].append([str(oid), str(vid)])

        # Save files
        with open(id_maps_path, 'w') as f:
            json.dump(id_maps, f, indent=2)
        with open(cluster_state_path, 'w') as f:
            json.dump(cluster_state, f, indent=2)
            
        print(f"Successfully exported backups to {archive_dir}")
        return True

    except Exception as e:
        print(f"Error during export: {e}")
        return False
    finally:
        cursor.close()

if __name__ == "__main__":
    # If run directly, offer to seed or export
    conn, msg = get_db_connection()
    if conn:
        print(f"Connected: {msg}")
        if len(sys.argv) > 1 and sys.argv[1] == "--export":
            export_to_backups(conn)
        else:
            perform_seeding(conn)
        conn.close()
    else:
        print(f"Connection failed: {msg}")
