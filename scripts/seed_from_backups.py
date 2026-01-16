import json
import os
import sys
import uuid
import psycopg2
from pathlib import Path

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.db_utils import create_postgresql_connection

def perform_seeding(conn):
    """Restore menu data from JSON backups in data/archive/"""
    # Paths
    archive_dir = Path("data/archive")
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
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (menu_item_id) DO UPDATE SET 
                    name = EXCLUDED.name,
                    type = EXCLUDED.type,
                    is_verified = TRUE
            """, (menu_item_id, clean_name, item_type))
            menu_items_count += 1
        
        # 2. Insert Variants
        print("Seeding Variants...")
        variants_count = 0
        for variant_id, variant_name in id_maps.get("variant_id_to_str", {}).items():
            cursor.execute("""
                INSERT INTO variants (variant_id, variant_name, is_verified)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (variant_id) DO UPDATE SET 
                    variant_name = EXCLUDED.variant_name,
                    is_verified = TRUE
            """, (variant_id, variant_name))
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
                            VALUES (%s, %s, %s, TRUE)
                            ON CONFLICT (order_item_id) DO UPDATE SET
                                menu_item_id = EXCLUDED.menu_item_id,
                                variant_id = EXCLUDED.variant_id,
                                is_verified = TRUE
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
    """Dump database state to JSON backups in data/archive/"""
    archive_dir = Path("data/archive")
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
    db_url = os.environ.get("DB_URL", "postgresql://postgres:postgres@localhost:5432/analytics")
    connection = create_postgresql_connection(db_url=db_url)
    
    if connection:
        if len(sys.argv) > 1 and sys.argv[1] == "--export":
            export_to_backups(connection)
        else:
            perform_seeding(connection)
        connection.close()
