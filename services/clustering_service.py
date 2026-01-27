import sys
import os
import uuid
import logging
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
import difflib

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.id_generator import generate_deterministic_id
from utils.db_utils import create_postgresql_connection

try:
    from utils.clean_order_item import clean_order_item_name
except ImportError:
    logging.warning("Could not import clean_order_item_name")
    def clean_order_item_name(name):
        return {'name': name, 'variant': 'UNKNOWN', 'type': 'UNKNOWN'}

class OrderItemCluster:
    def __init__(self, db_conn=None):
        """
        Initialize Clustering Service.
        Args:
            db_conn: Existing psycopg2 connection. If None, tries to create one.
        """
        if db_conn:
            self.conn = db_conn
            self.own_connection = False
        else:
            try:
                # Use Docker Postgres by default
                db_url = os.environ.get("DB_URL", "postgresql://postgres:postgres@localhost:5432/analytics")
                self.conn = create_postgresql_connection(
                    host=None, port=None, database=None, user=None, password=None,
                    db_url=db_url
                )
                self.own_connection = True
            except Exception as e:
                logging.error(f"Failed to create DB connection: {e}")
                self.conn = None
                self.own_connection = False

    def __del__(self):
        if hasattr(self, 'own_connection') and self.own_connection and self.conn:
            self.conn.close()

    def predict_menu_item_name(self, clean_name: str, item_type: str = None) -> Optional[Tuple[str, str, float]]:
        """
        Suggest an existing verified menu_item_id + name for a given raw name.
        Returns: (menu_item_id, name, score) or None
        """
        cursor = self.conn.cursor()
        try:
            # Get all verified items of the same type (or all if type is None)
            if item_type:
                cursor.execute("SELECT menu_item_id, name FROM menu_items WHERE type = %s AND is_verified = TRUE", (item_type,))
            else:
                cursor.execute("SELECT menu_item_id, name FROM menu_items WHERE is_verified = TRUE")
            
            candidates = cursor.fetchall()
            if not candidates:
                return None
            
            # Use difflib to find the best match
            names = [c[1] for c in candidates]
            best_matches = difflib.get_close_matches(clean_name, names, n=1, cutoff=0.7)
            
            if best_matches:
                match_name = best_matches[0]
                # Find the ID for this name
                for mid, name in candidates:
                    if name == match_name:
                        score = difflib.SequenceMatcher(None, clean_name, match_name).ratio()
                        return str(mid), name, score
            
            return None
        finally:
            cursor.close()

    def add(self, name: str, order_item_id: str, is_addon: bool = False) -> Tuple[str, str, str, str]:
        """
        Add an order item to the cluster.
        Returns: (menu_item_id, order_item_id, variant_id, type_id)
        """
        if not self.conn:
            logging.error("No database connection available.")
            return None, None, None, None

        if not name or not name.strip():
            return None, None, None, None

        if not order_item_id:
             order_item_id = generate_deterministic_id(f"generated_{name}")

        cursor = self.conn.cursor()
        try:
            # 1. Check if mapping already exists
            cursor.execute("""
                SELECT m.menu_item_id, m.variant_id, mi.type
                FROM menu_item_variants m
                JOIN menu_items mi ON m.menu_item_id = mi.menu_item_id
                WHERE m.order_item_id = %s
            """, (str(order_item_id),))
            
            existing = cursor.fetchone()
            
            if existing:
                menu_item_id, variant_id, type_text = existing
                return str(menu_item_id), str(order_item_id), str(variant_id), type_text
            
            # 2. NEW ITEM
            clean_result = clean_order_item_name(name)
            clean_name = clean_result['name']
            variant_name = clean_result.get('variant', 'UNKNOWN') or 'UNKNOWN'
            item_type = clean_result.get('type', 'UNKNOWN') or 'UNKNOWN'
            
            # Check if this cleaned name + type already exists in the DB
            cursor.execute("""
                SELECT menu_item_id FROM menu_items 
                WHERE name = %s AND type = %s
            """, (clean_name, item_type))
            existing_item = cursor.fetchone()
            
            if existing_item:
                menu_item_id = str(existing_item[0])
            else:
                menu_item_id = generate_deterministic_id(clean_name, item_type)
            
            variant_id = generate_deterministic_id(variant_name)
            
            # Predict potential match (if we didn't find an exact name match)
            suggested_id = None
            if not existing_item:
                prediction = self.predict_menu_item_name(clean_name, item_type)
                suggested_id = prediction[0] if prediction else None
            
            # CREATE MENU ITEM (with suggestion if found)
            cursor.execute("""
                INSERT INTO menu_items (menu_item_id, name, type, is_verified, suggestion_id)
                VALUES (%s, %s, %s, FALSE, %s)
                ON CONFLICT (menu_item_id) DO UPDATE SET
                    suggestion_id = COALESCE(menu_items.suggestion_id, EXCLUDED.suggestion_id)
            """, (menu_item_id, clean_name, item_type, suggested_id))
            
            # CREATE VARIANT
            cursor.execute("""
                INSERT INTO variants (variant_id, variant_name, is_verified)
                VALUES (%s, %s, FALSE)
                ON CONFLICT (variant_id) DO NOTHING
            """, (variant_id, variant_name))
            
            # CREATE MAPPING
            cursor.execute("""
                INSERT INTO menu_item_variants (order_item_id, menu_item_id, variant_id, is_verified)
                VALUES (%s, %s, %s, FALSE)
                ON CONFLICT (order_item_id) DO NOTHING
            """, (str(order_item_id), menu_item_id, variant_id))
            
            self.conn.commit()
            return str(menu_item_id), str(order_item_id), str(variant_id), item_type

        except Exception as e:
            self.conn.rollback()
            logging.error(f"Error adding item {name} ({order_item_id}): {e}")
            raise e
        finally:
            cursor.close()


