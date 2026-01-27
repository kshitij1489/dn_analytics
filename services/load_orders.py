"""
Order Data Loading Script (SQLite Version)

Loads order data from PetPooja API or JSON files into SQLite database.

Usage:
    # Load from API (all orders)
    python3 services/load_orders.py
    
    # Load from JSON file
    python3 services/load_orders.py --input-file sample_payloads/raw_orders.json
    
    # Incremental update (only new orders)
    python3 services/load_orders.py --incremental
"""

import sys
import hashlib
import re
import os
import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Any
from decimal import Decimal

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.api_client import fetch_stream_raw, load_orders_from_file
from services.clustering_service import OrderItemCluster
from src.core.db.connection import get_db_connection

def create_schema_if_needed(conn):
    """Ensure tables exist by checking for 'orders' table. If not, run schema."""
    cursor = conn.cursor()
    
    # Check if orders table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
    if cursor.fetchone():
        return # Schema exists
        
    print("  Initialize database schema...")
    schema_path = Path(__file__).parent.parent / "database" / "schema_sqlite.sql"
    
    if schema_path.exists():
        with open(schema_path, 'r', encoding='utf-8') as f:
            cursor.executescript(f.read())
            print("  ✓ Schema created")
    else:
        print(f"  ❌ Schema file not found: {schema_path}")
        
    conn.commit()


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse timestamp string to datetime object and ensure IST"""
    if not timestamp_str:
        return None
    
    # Try different formats
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ]
    
    ist = ZoneInfo('Asia/Kolkata')
    
    for fmt in formats:
        try:
            dt = datetime.strptime(timestamp_str, fmt)
            # If naive, assume IST as per user's observation that fetch returns IST
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ist)
            return dt
        except ValueError:
            continue
    
    return None

def normalize_phone(phone: str) -> str:
    """Normalize phone number to last 10 digits"""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) > 10:
        digits = digits[-10:]
    return digits


def normalize_text(value: str) -> str:
    """Lowercase, trim, collapse spaces"""
    return " ".join(value.lower().strip().split())


def make_hash(value: str) -> str:
    """Stable SHA-256 hash"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def compute_customer_identity_key(customer: dict) -> str:
    """
    Priority:
    1. phone
    2. name + address
    3. anonymous (unique per customer)
    """
    import uuid
    
    phone = customer.get("phone")
    name = customer.get("name")
    address = customer.get("address")

    if phone:
        phone_norm = normalize_phone(phone)
        if phone_norm:
            return "phone:" + make_hash(phone_norm)

    if name and address:
        base = normalize_text(name) + "|" + normalize_text(address)
        return "addr:" + make_hash(base)

    # For anonymous or name-only customers, use a unique identifier
    return "anon:" + str(uuid.uuid4())

def get_or_create_restaurant(conn, restaurant_data: Dict) -> int:
    """Get or create restaurant, return restaurant_id"""
    cursor = conn.cursor()
    
    rest_id = restaurant_data.get('restID', '')
    name = restaurant_data.get('res_name', '')
    address = restaurant_data.get('address', '')
    contact = restaurant_data.get('contact_information', '')
    
    # Check if exists
    cursor.execute("SELECT restaurant_id FROM restaurants WHERE petpooja_restid = ?", (rest_id,))
    result = cursor.fetchone()
    
    if result:
        return result[0]
    else:
        # Insert new restaurant
        cursor.execute("""
            INSERT INTO restaurants (petpooja_restid, name, address, contact_information)
            VALUES (?, ?, ?, ?)
            RETURNING restaurant_id
        """, (rest_id, name, address, contact))
        restaurant_id = cursor.fetchone()[0]
        conn.commit()
        return restaurant_id

def get_or_create_customer(conn, customer_data: Dict, order_date: datetime, order_total: Decimal = Decimal(0)) -> Optional[int]:
    """Get or create customer, return customer_id"""
    cursor = conn.cursor()
    
    phone = customer_data.get('phone', '').strip() if customer_data.get('phone') else None
    name = customer_data.get('name', '').strip() if customer_data.get('name') else 'Anonymous'
    address = customer_data.get('address', '').strip() if customer_data.get('address') else None
    gstin = customer_data.get('gstin', '').strip() if customer_data.get('gstin') else None
    
    # Normalize name for deduplication (lowercase, trimmed)
    name_normalized = name.lower().strip()
    
    # Check if customer exists by normalized name
    identity_key = compute_customer_identity_key(customer_data)
    cursor.execute("SELECT customer_id FROM customers WHERE customer_identity_key = ?", (identity_key,))
    
    result = cursor.fetchone()
    
    # Format date for storage
    order_date_str = order_date.strftime('%Y-%m-%d %H:%M:%S') if order_date else None
    
    if result:
        customer_id = result[0]
        
        # Determine update logic
        update_fields = []
        update_values = []
        
        update_fields.append("last_order_date = ?")
        update_values.append(order_date_str)
        
        update_fields.append("total_orders = COALESCE(total_orders, 0) + 1")
        
        update_fields.append("total_spent = COALESCE(total_spent, 0) + ?")
        update_values.append(float(order_total))
        
        # Calculate verification status
        is_verified = not identity_key.startswith("anon:")
        update_fields.append("is_verified = ?")
        update_values.append(1 if is_verified else 0)
        
        # Update phone if provided and currently NULL
        if phone:
            update_fields.append("phone = COALESCE(phone, ?)")
            update_values.append(phone)
        
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        
        update_values.append(customer_id)
        
        sql = f"UPDATE customers SET {', '.join(update_fields)} WHERE customer_id = ?"
        cursor.execute(sql, update_values)
        conn.commit()
    else:
        # Insert new customer
        is_verified = not identity_key.startswith("anon:")
        cursor.execute("""
            INSERT INTO customers (
                customer_identity_key,
                name, name_normalized, phone, address, gstin,
                first_order_date, last_order_date,
                total_orders, total_spent, is_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            RETURNING customer_id
        """, (
            identity_key,
            name, name_normalized, phone, address, gstin,
            order_date_str, order_date_str, 
            float(order_total), 1 if is_verified else 0
        ))
        customer_id = cursor.fetchone()[0]
        conn.commit()
    
    return customer_id

def process_order(conn, order_payload: Dict, item_cluster: OrderItemCluster) -> Dict[str, int]:
    """Process a single order payload and insert into database."""
    stats = { 'orders': 0, 'order_items': 0, 'order_item_addons': 0, 
              'order_taxes': 0, 'order_discounts': 0, 'errors': [] }
    
    try:
        # Extract data
        raw_event = order_payload.get('raw_event', {})
        raw_payload = raw_event.get('raw_payload', {})
        properties = raw_payload.get('properties', {})
        
        stream_id = order_payload.get('stream_id')
        event_id = order_payload.get('event_id', '')
        aggregate_id = order_payload.get('aggregate_id', '')
        occurred_at_str = order_payload.get('occurred_at', '')
        occurred_at = parse_timestamp(occurred_at_str)
        
        order_data = properties.get('Order', {})
        petpooja_order_id = order_data.get('orderID')
        
        # Check if exists
        cursor = conn.cursor()
        cursor.execute("SELECT order_id FROM orders WHERE petpooja_order_id = ?", (petpooja_order_id,))
        exists = cursor.fetchone()
        
        customer_data = properties.get('Customer', {})
        restaurant_data = properties.get('Restaurant', {})
        order_items_data = properties.get('OrderItem', [])
        taxes_data = properties.get('Tax', [])
        discounts_data = properties.get('Discount', [])
        
        restaurant_id = get_or_create_restaurant(conn, restaurant_data)
        
        created_on_str = order_data.get('created_on', '')
        created_on = parse_timestamp(created_on_str)
        if not created_on:
            created_on = occurred_at or datetime.now()
            
        total_amount = Decimal(str(order_data.get('total', 0)))
        
        # Customer handling
        customer_id = None
        if exists:
            # Check existing customer mapping
            # This logic is simplified; we blindly update customer if needed or just get ID.
            # Here we just want stats correct.
            # Re-calculating identity key to find the customer.
            identity_key = compute_customer_identity_key(customer_data)
            cursor.execute("SELECT customer_id FROM customers WHERE customer_identity_key = ?", (identity_key,))
            res = cursor.fetchone()
            if res:
                customer_id = res[0]
            else:
                 # Should not happen for existing order, but if so, create without incrementing stats?
                 # Ignoring stat increment logic for overwrite for simplicity, 
                 # assuming existing orders are not re-processed often or don't affect accumulation much.
                 customer_id = get_or_create_customer(conn, customer_data, created_on, Decimal(0))
        else:
            customer_id = get_or_create_customer(conn, customer_data, created_on, total_amount)
            
        # Helper to strict str
        def s(val): return str(val) if val is not None else None
        def f(val): return float(val) if val is not None else 0.0
        
        # Insert/Update Order with Upsert
        # SQLite: INSERT INTO ... ON CONFLICT(id) DO UPDATE SET ...
        # Ensure dates are strings
        occ_str = occurred_at.strftime('%Y-%m-%d %H:%M:%S') if occurred_at else None
        cre_str = created_on.strftime('%Y-%m-%d %H:%M:%S') if created_on else None
        
        cursor.execute("""
            INSERT INTO orders (
                petpooja_order_id, stream_id, event_id, aggregate_id,
                customer_id, restaurant_id,
                occurred_at, created_on,
                order_type, order_from, sub_order_type, order_from_id,
                order_status, biller, assignee,
                table_no, token_no, no_of_persons,
                customer_invoice_id,
                core_total, tax_total, discount_total,
                delivery_charges, packaging_charge, service_charge, round_off, total,
                comment
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?
            ) ON CONFLICT (petpooja_order_id) DO UPDATE SET
                stream_id = excluded.stream_id,
                event_id = excluded.event_id,
                updated_at = CURRENT_TIMESTAMP
            RETURNING order_id
        """, (
            petpooja_order_id, stream_id, event_id, aggregate_id,
            customer_id, restaurant_id,
            occ_str, cre_str,
            order_data.get('order_type', ''),
            order_data.get('order_from', ''),
            order_data.get('sub_order_type'),
            order_data.get('order_from_id'),
            order_data.get('status', ''),
            order_data.get('biller'),
            order_data.get('assignee'),
            order_data.get('table_no'),
            order_data.get('token_no'),
            order_data.get('no_of_persons', 0),
            order_data.get('customer_invoice_id'),
            f(order_data.get('core_total', 0)),
            f(order_data.get('tax_total', 0)),
            f(order_data.get('discount_total', 0)),
            f(order_data.get('delivery_charges', 0)),
            f(order_data.get('packaging_charge', 0)),
            f(order_data.get('service_charge', 0)),
            f(order_data.get('round_off', 0) or 0),
            f(order_data.get('total', 0)),
            order_data.get('comment')
        ))
        
        row = cursor.fetchone()
        order_id = row[0]
        conn.commit()
        stats['orders'] = 1
        
        # Order Items
        for item_data in order_items_data:
            raw_name = item_data.get('name', '')
            menu_item_id, _, variant_id, match_method = item_cluster.add(raw_name, item_data.get('itemid'))
            match_confidence = 100.0 if menu_item_id else 0.0
            
            cursor.execute("""
                INSERT INTO order_items (
                    order_id, menu_item_id, variant_id,
                    petpooja_itemid, itemcode, name_raw, category_name,
                    quantity, unit_price, total_price,
                    tax_amount, discount_amount,
                    specialnotes, sap_code, vendoritemcode,
                    match_confidence, match_method
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?
                ) RETURNING order_item_id
            """, (
                order_id, menu_item_id, variant_id,
                item_data.get('itemid'),
                item_data.get('itemcode'),
                raw_name,
                item_data.get('category_name'),
                item_data.get('quantity', 1),
                f(item_data.get('price', 0)),
                f(item_data.get('total', 0)),
                f(item_data.get('tax', 0)),
                f(item_data.get('discount', 0)),
                item_data.get('specialnotes'),
                item_data.get('sap_code'),
                item_data.get('vendoritemcode'),
                match_confidence,
                match_method
            ))
            order_item_id = cursor.fetchone()[0]
            stats['order_items'] += 1
            
            if menu_item_id and order_data.get('status') == 'Success':
                 cursor.execute("""
                    UPDATE menu_items 
                    SET total_sold = total_sold + ?,
                        sold_as_item = sold_as_item + ?,
                        total_revenue = total_revenue + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE menu_item_id = ?
                """, (item_data.get('quantity', 1), item_data.get('quantity', 1), f(item_data.get('total', 0)), menu_item_id))

            # Addons
            addons = item_data.get('addon', [])
            for addon_data in addons:
                addon_raw_name = addon_data.get('name', '')
                addon_menu_item_id, _, addon_variant_id, addon_match_method = item_cluster.add(addon_raw_name, addon_data.get('addonid'))
                addon_match_confidence = 100.0 if addon_menu_item_id else 0.0
                
                qty = addon_data.get('quantity', 1)
                try: qty = int(qty)
                except: qty = 1
                
                cursor.execute("""
                    INSERT INTO order_item_addons (
                        order_item_id, menu_item_id, variant_id,
                        petpooja_addonid, name_raw, group_name,
                        quantity, price,
                        addon_sap_code,
                        match_confidence, match_method
                    ) VALUES (
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?,
                        ?, ?
                    )
                """, (
                    order_item_id, addon_menu_item_id, addon_variant_id,
                    addon_data.get('addonid'),
                    addon_raw_name,
                    addon_data.get('group_name'),
                    qty,
                    f(addon_data.get('price', 0)),
                    addon_data.get('addon_sap_code'),
                    addon_match_confidence,
                    addon_match_method
                ))
                stats['order_item_addons'] += 1
                
                if addon_menu_item_id and order_data.get('status') == 'Success':
                    addon_total = f(addon_data.get('price', 0)) * qty
                    cursor.execute("""
                        UPDATE menu_items 
                        SET total_sold = total_sold + ?,
                            sold_as_addon = sold_as_addon + ?,
                            total_revenue = total_revenue + ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE menu_item_id = ?
                    """, (qty, qty, addon_total, addon_menu_item_id))

        # Insert taxes
        for tax_data in taxes_data:
            cursor.execute("""
                INSERT INTO order_taxes (
                    order_id, tax_title, tax_rate, tax_type, tax_amount
                ) VALUES (
                    ?, ?, ?, ?, ?
                )
            """, (
                order_id,
                tax_data.get('title', ''),
                float(tax_data.get('rate', 0)),
                tax_data.get('type', 'P'),
                float(tax_data.get('amount', 0))
            ))
            stats['order_taxes'] += 1
        
        # Insert discounts
        for discount_data in discounts_data:
            cursor.execute("""
                INSERT INTO order_discounts (
                    order_id, discount_title, discount_type, discount_rate, discount_amount
                ) VALUES (
                    ?, ?, ?, ?, ?
                )
            """, (
                order_id,
                discount_data.get('title', ''),
                discount_data.get('type', 'F'),
                float(discount_data.get('rate', 0)),
                float(discount_data.get('amount', 0))
            ))
            stats['order_discounts'] += 1

        conn.commit()
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        stats['errors'].append(f"Error processing order {petpooja_order_id}: {str(e)}")
        conn.rollback()

    return stats


def get_last_stream_id(conn) -> int:
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(stream_id) FROM orders")
    result = cursor.fetchone()
    return result[0] if result[0] is not None else 0

def main():
    parser = argparse.ArgumentParser(description="Load order data into SQLite database")
    parser.add_argument('--input-file', type=str, help="Path to JSON file with orders")
    parser.add_argument('--incremental', action='store_true', help="Only load new orders")
    parser.add_argument('--limit', type=int, help="Limit number of orders")
    
    # Ignored args kept for compat
    parser.add_argument('--db-url', type=str, help="Ignored (SQLite used)")
    parser.add_argument('--host', type=str, help="Ignored")
    parser.add_argument('--port', type=int, help="Ignored")
    parser.add_argument('--database', type=str, help="Ignored")
    parser.add_argument('--user', type=str, help="Ignored")
    parser.add_argument('--password', type=str, help="Ignored")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Order Data Loading Script (SQLite)")
    print("=" * 60)
    
    conn, msg = get_db_connection()
    if not conn:
        print(f"❌ Connection failed: {msg}")
        return
    print(f"✅ {msg}")
    
    create_schema_if_needed(conn)
    
    # Initialize OrderItemCluster
    try:
        item_cluster = OrderItemCluster(conn)
        print("✅ OrderItemCluster ready")
    except Exception as e:
        print(f"❌ Cluster init failed: {e}")
        conn.close()
        return

    # Reset counters if full reload
    if not args.incremental:
        print("Resetting menu item counters...")
        conn.execute("UPDATE menu_items SET total_revenue = 0, total_sold = 0, sold_as_item = 0, sold_as_addon = 0;")
        conn.commit()

    # Fetch Orders
    print("Loading orders...")
    if args.input_file:
         orders = load_orders_from_file(args.input_file)
    elif args.incremental:
        last_id = get_last_stream_id(conn)
        print(f"  Fetching orders after stream_id {last_id}")
        orders = fetch_stream_raw(endpoint="orders", start_cursor=last_id)
    else:
        print("  Fetching all orders...")
        orders, _ = fetch_stream_raw(endpoint="orders", max_records=args.limit)
    
    if not orders:
        print("No orders to load.")
        return

    print(f"  Total orders: {len(orders)}")
    
    total_stats = {
        'orders': 0, 'order_items': 0, 'order_item_addons': 0,
        'order_taxes': 0, 'order_discounts': 0, 'errors': []
    }
    
    for i, order_payload in enumerate(orders, 1):
        if i % 50 == 0:
            print(f"  Processing {i}/{len(orders)}...")
        stats = process_order(conn, order_payload, item_cluster)
        for k in total_stats:
            if k == 'errors': total_stats[k].extend(stats[k])
            else: total_stats[k] += stats[k]
        
    print("\nSUMMARY")
    print(f"Orders: {total_stats['orders']}")
    print(f"Items: {total_stats['order_items']}")
    print(f"Errors: {len(total_stats['errors'])}")
    
    if total_stats['errors']:
        for e in total_stats['errors'][:5]:
            print(f"  - {e}")

    conn.close()

if __name__ == "__main__":
    main()
