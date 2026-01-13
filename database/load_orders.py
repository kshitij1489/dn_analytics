"""
Order Data Loading Script

Loads order data from PetPooja API or JSON files into PostgreSQL database.

Usage:
    # Load from API (all orders)
    python3 database/load_orders.py --db-url "postgresql://user:pass@localhost:5432/analytics"
    
    # Load from JSON file
    python3 database/load_orders.py --db-url "postgresql://user:pass@localhost:5432/analytics" --input-file sample_payloads/raw_orders.json
    
    # Incremental update (only new orders)
    python3 database/load_orders.py --db-url "postgresql://user:pass@localhost:5432/analytics" --incremental
"""

import sys
import hashlib
import re
import os
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from decimal import Decimal

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    from psycopg2.extras import execute_values, RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    print("⚠️  psycopg2 not installed. Install it with: pip install psycopg2-binary")

from utils.api_client import fetch_stream_raw, load_orders_from_file
from data_cleaning.item_matcher import ItemMatcher


def create_postgresql_connection(host, port, database, user, password, db_url=None):
    """Create PostgreSQL database connection"""
    if not PSYCOPG2_AVAILABLE:
        raise ImportError("psycopg2 is required for PostgreSQL connections")
    
    if db_url:
        conn = psycopg2.connect(db_url)
    else:
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password
        )
    
    return conn


def create_schema_if_needed(conn):
    """Create all necessary tables if they don't exist"""
    cursor = conn.cursor()
    schema_dir = Path(__file__).parent / "schema"
    
    # Read and execute schema files in order (menu_items must come first)
    schema_files = [
        "menu_items.sql",  # Must be first - other tables depend on it
        "restaurants.sql",
        "customers.sql",
        "orders.sql",
        "order_items.sql",
        "item_parsing.sql",
        "views.sql"
    ]
    
    for schema_file in schema_files:
        schema_path = schema_dir / schema_file
        if schema_path.exists():
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_sql = f.read()
                # Execute each statement separately
                try:
                    cursor.execute(schema_sql)
                    print(f"  ✓ Created schema from {schema_file}")
                except Exception as e:
                    # Table might already exist, that's okay
                    if "already exists" not in str(e).lower():
                        print(f"  ⚠️  Warning creating {schema_file}: {e}")
    
    conn.commit()
    cursor.close()


def parse_timestamp(timestamp_str: str) -> Optional[datetime]:
    """Parse timestamp string to datetime object"""
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
    
    for fmt in formats:
        try:
            return datetime.strptime(timestamp_str, fmt)
        except ValueError:
            continue
    
    return None


def get_or_create_restaurant(conn, restaurant_data: Dict) -> int:
    """Get or create restaurant, return restaurant_id"""
    cursor = conn.cursor()
    
    rest_id = restaurant_data.get('restID', '')
    name = restaurant_data.get('res_name', '')
    address = restaurant_data.get('address', '')
    contact = restaurant_data.get('contact_information', '')
    
    # Check if exists
    cursor.execute("""
        SELECT restaurant_id FROM restaurants 
        WHERE petpooja_restid = %s
    """, (rest_id,))
    
    result = cursor.fetchone()
    if result:
        restaurant_id = result[0]
    else:
        # Insert new restaurant
        cursor.execute("""
            INSERT INTO restaurants (petpooja_restid, name, address, contact_information)
            VALUES (%s, %s, %s, %s)
            RETURNING restaurant_id
        """, (rest_id, name, address, contact))
        restaurant_id = cursor.fetchone()[0]
        conn.commit()
    
    cursor.close()
    return restaurant_id

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
    # This prevents false matches based on name alone.
    return "anon:" + str(uuid.uuid4())

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
    cursor.execute("""
        SELECT customer_id FROM customers
        WHERE customer_identity_key = %s
    """, (identity_key,))
    
    result = cursor.fetchone()
    if result:
        customer_id = result[0]
        # Update last order date, increment counts, and update phone if provided
        update_fields = []
        update_values = []
        
        update_fields.append("last_order_date = %s")
        update_values.append(order_date)
        
        update_fields.append("total_orders = COALESCE(total_orders, 0) + 1")
        
        update_fields.append("total_spent = COALESCE(total_spent, 0) + %s")
        update_values.append(order_total)
        
        # Update phone if provided and currently NULL
        if phone:
            update_fields.append("phone = COALESCE(phone, %s)")
            update_values.append(phone)
        
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        
        update_values.append(customer_id)
        
        cursor.execute(f"""
            UPDATE customers 
            SET {', '.join(update_fields)}
            WHERE customer_id = %s
        """, update_values)
        conn.commit()
    else:
        # Insert new customer
        cursor.execute("""
                INSERT INTO customers (
                customer_identity_key,
                name, name_normalized, phone, address, gstin,
                first_order_date, last_order_date,
                total_orders, total_spent
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
            RETURNING customer_id
        """, (
            identity_key,
            name, name_normalized, phone, address, gstin,
            order_date, order_date, order_total
        ))
        customer_id = cursor.fetchone()[0]
        conn.commit()
    
    cursor.close()
    return customer_id


def process_order(conn, order_payload: Dict, item_matcher: ItemMatcher) -> Dict[str, int]:
    """
    Process a single order payload and insert into database.
    Returns dict with counts of inserted records.
    """
    stats = {
        'orders': 0,
        'order_items': 0,
        'order_item_addons': 0,
        'order_taxes': 0,
        'order_discounts': 0,
        'errors': []
    }
    
    try:
        # Extract data from payload
        raw_event = order_payload.get('raw_event', {})
        raw_payload = raw_event.get('raw_payload', {})
        properties = raw_payload.get('properties', {})
        
        # Top-level fields
        stream_id = order_payload.get('stream_id')
        event_id = order_payload.get('event_id', '')
        aggregate_id = order_payload.get('aggregate_id', '')
        occurred_at_str = order_payload.get('occurred_at', '')
        occurred_at = parse_timestamp(occurred_at_str)
        
        # Order data
        order_data = properties.get('Order', {})
        petpooja_order_id = order_data.get('orderID')
        
        # Customer data
        customer_data = properties.get('Customer', {})
        
        # Restaurant data
        restaurant_data = properties.get('Restaurant', {})
        
        # Order items
        order_items_data = properties.get('OrderItem', [])
        
        # Taxes
        taxes_data = properties.get('Tax', [])
        
        # Discounts
        discounts_data = properties.get('Discount', [])
        
        # Get or create restaurant
        restaurant_id = get_or_create_restaurant(conn, restaurant_data)
        
        # Get or create customer
        created_on_str = order_data.get('created_on', '')
        created_on = parse_timestamp(created_on_str)
        if not created_on:
            created_on = occurred_at or datetime.now()
        
        total_amount = Decimal(str(order_data.get('total', 0)))
        customer_id = get_or_create_customer(conn, customer_data, created_on, total_amount)
        
        # Insert order
        cursor = conn.cursor()
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
                %s, %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s
            ) ON CONFLICT (petpooja_order_id) DO UPDATE SET
                stream_id = EXCLUDED.stream_id,
                event_id = EXCLUDED.event_id,
                updated_at = CURRENT_TIMESTAMP
            RETURNING order_id
        """, (
            petpooja_order_id, stream_id, event_id, aggregate_id,
            customer_id, restaurant_id,
            occurred_at, created_on,
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
            Decimal(str(order_data.get('core_total', 0))),
            Decimal(str(order_data.get('tax_total', 0))),
            Decimal(str(order_data.get('discount_total', 0))),
            Decimal(str(order_data.get('delivery_charges', 0))),
            Decimal(str(order_data.get('packaging_charge', 0))),
            Decimal(str(order_data.get('service_charge', 0))),
            Decimal(str(order_data.get('round_off', 0) or 0)),
            Decimal(str(order_data.get('total', 0))),
            order_data.get('comment')
        ))
        
        order_id = cursor.fetchone()[0]
        conn.commit()
        stats['orders'] = 1
        
        # Process order items
        for item_data in order_items_data:
            raw_name = item_data.get('name', '')
            
            # Match item using ItemMatcher
            match_result = item_matcher.match_item(raw_name)
            
            menu_item_id = match_result.get('menu_item_id')
            variant_id = match_result.get('variant_id')
            match_confidence = match_result.get('match_confidence')
            match_method = match_result.get('match_method')
            
            # Insert order item
            cursor.execute("""
                INSERT INTO order_items (
                    order_id, menu_item_id, variant_id,
                    petpooja_itemid, itemcode, name_raw, category_name,
                    quantity, unit_price, total_price,
                    tax_amount, discount_amount,
                    specialnotes, sap_code, vendoritemcode,
                    match_confidence, match_method
                ) VALUES (
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s
                ) RETURNING order_item_id
            """, (
                order_id, menu_item_id, variant_id,
                item_data.get('itemid'),
                item_data.get('itemcode'),
                raw_name,
                item_data.get('category_name'),
                item_data.get('quantity', 1),
                Decimal(str(item_data.get('price', 0))),
                Decimal(str(item_data.get('total', 0))),
                Decimal(str(item_data.get('tax', 0))),
                Decimal(str(item_data.get('discount', 0))),
                item_data.get('specialnotes'),
                item_data.get('sap_code'),
                item_data.get('vendoritemcode'),
                match_confidence,
                match_method
            ))
            
            order_item_id = cursor.fetchone()[0]
            stats['order_items'] += 1
            
            # Update menu_items stats if matched and order is successful
            if menu_item_id and order_data.get('status') == 'Success':
                cursor.execute("""
                    UPDATE menu_items 
                    SET total_sold = total_sold + %s,
                        total_revenue = total_revenue + %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE menu_item_id = %s
                """, (item_data.get('quantity', 1), Decimal(str(item_data.get('total', 0))), menu_item_id))
            
            # Process addons
            addons = item_data.get('addon', [])
            for addon_data in addons:
                addon_raw_name = addon_data.get('name', '')
                
                # Match addon
                addon_match = item_matcher.match_item(addon_raw_name)
                
                addon_menu_item_id = addon_match.get('menu_item_id')
                addon_variant_id = addon_match.get('variant_id')
                addon_match_confidence = addon_match.get('match_confidence')
                addon_match_method = addon_match.get('match_method')
                
                # Parse quantity (can be string or int)
                addon_quantity = addon_data.get('quantity', 1)
                if isinstance(addon_quantity, str):
                    try:
                        addon_quantity = int(addon_quantity)
                    except ValueError:
                        addon_quantity = 1
                
                cursor.execute("""
                    INSERT INTO order_item_addons (
                        order_item_id, menu_item_id, variant_id,
                        petpooja_addonid, name_raw, group_name,
                        quantity, price,
                        addon_sap_code,
                        match_confidence, match_method
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s,
                        %s, %s
                    )
                """, (
                    order_item_id, addon_menu_item_id, addon_variant_id,
                    addon_data.get('addonid'),
                    addon_raw_name,
                    addon_data.get('group_name'),
                    addon_quantity,
                    Decimal(str(addon_data.get('price', 0))),
                    addon_data.get('addon_sap_code'),
                    addon_match_confidence,
                    addon_match_method
                ))
                
                stats['order_item_addons'] += 1
                
                # Update menu_items stats for addons if matched and order is successful
                if addon_menu_item_id and order_data.get('status') == 'Success':
                    addon_total = Decimal(str(addon_data.get('price', 0))) * addon_quantity
                    cursor.execute("""
                        UPDATE menu_items 
                        SET total_sold = total_sold + %s,
                            total_revenue = total_revenue + %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE menu_item_id = %s
                    """, (addon_quantity, addon_total, addon_menu_item_id))
            
            conn.commit()
        
        # Insert taxes
        for tax_data in taxes_data:
            cursor.execute("""
                INSERT INTO order_taxes (
                    order_id, tax_title, tax_rate, tax_type, tax_amount
                ) VALUES (
                    %s, %s, %s, %s, %s
                )
            """, (
                order_id,
                tax_data.get('title', ''),
                Decimal(str(tax_data.get('rate', 0))),
                tax_data.get('type', 'P'),
                Decimal(str(tax_data.get('amount', 0)))
            ))
            stats['order_taxes'] += 1
        
        # Insert discounts
        for discount_data in discounts_data:
            cursor.execute("""
                INSERT INTO order_discounts (
                    order_id, discount_title, discount_type, discount_rate, discount_amount
                ) VALUES (
                    %s, %s, %s, %s, %s
                )
            """, (
                order_id,
                discount_data.get('title', ''),
                discount_data.get('type', 'F'),
                Decimal(str(discount_data.get('rate', 0))),
                Decimal(str(discount_data.get('amount', 0)))
            ))
            stats['order_discounts'] += 1
        
        conn.commit()
        cursor.close()
        
    except Exception as e:
        stats['errors'].append(f"Error processing order {petpooja_order_id}: {str(e)}")
        conn.rollback()
        if 'cursor' in locals():
            cursor.close()
    
    return stats


def get_last_stream_id(conn) -> int:
    """Get the last processed stream_id from database"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(stream_id) FROM orders
    """)
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result[0] is not None else 0


def main():
    parser = argparse.ArgumentParser(description="Load order data into PostgreSQL database")
    parser.add_argument('--db-url', type=str, help="PostgreSQL connection URL")
    parser.add_argument('--host', type=str, default='localhost', help="Database host")
    parser.add_argument('--port', type=int, default=5432, help="Database port")
    parser.add_argument('--database', type=str, default='analytics', help="Database name")
    parser.add_argument('--user', type=str, help="Database user")
    parser.add_argument('--password', type=str, help="Database password")
    parser.add_argument('--input-file', type=str, help="Path to JSON file with orders")
    parser.add_argument('--incremental', action='store_true', help="Only load new orders (incremental update)")
    parser.add_argument('--limit', type=int, help="Limit number of orders to process (for testing)")
    args = parser.parse_args()
    
    print("=" * 80)
    print("Order Data Loading Script")
    print("=" * 80)
    
    # Connect to database
    print("\n1. Connecting to database...")
    try:
        conn = create_postgresql_connection(
            host=args.host,
            port=args.port,
            database=args.database,
            user=args.user,
            password=args.password,
            db_url=args.db_url
        )
        print("  ✓ Connected successfully")
    except Exception as e:
        print(f"  ✗ Connection failed: {e}")
        return
    
    # Create schema if needed
    print("\n2. Creating schema if needed...")
    try:
        create_schema_if_needed(conn)
        print("  ✓ Schema ready")
    except Exception as e:
        print(f"  ✗ Schema creation failed: {e}")
        conn.close()
        return
    
    # Initialize ItemMatcher
    print("\n3. Initializing ItemMatcher...")
    try:
        item_matcher = ItemMatcher(conn)
        print("  ✓ ItemMatcher ready")
    except Exception as e:
        print(f"  ✗ ItemMatcher initialization failed: {e}")
        conn.close()
        return
    
    # Reset menu item counters if NOT incremental
    if not args.incremental:
        print("\n3.5 Resetting menu item analytics counters for full reload...")
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE menu_items SET total_revenue = 0, total_sold = 0;")
            conn.commit()
            cursor.close()
            print("  ✓ Counters reset")
        except Exception as e:
            print(f"  ⚠️  Failed to reset counters: {e}")
            conn.rollback()

    # Load orders
    print("\n4. Loading orders...")
    if args.input_file:
        print(f"  Loading from file: {args.input_file}")
        orders = load_orders_from_file(args.input_file)
    elif args.incremental:
        last_stream_id = get_last_stream_id(conn)
        print(f"  Incremental update: fetching orders after stream_id {last_stream_id}")
        orders = fetch_stream_raw(
            endpoint="orders",
            start_cursor=last_stream_id + 1
        )
    else:
        print("  Fetching all orders from API...")
        orders = fetch_stream_raw(endpoint="orders")
    
    if args.limit:
        orders = orders[:args.limit]
        print(f"  Limited to {args.limit} orders for testing")
    
    print(f"  Total orders to process: {len(orders)}")
    
    # Process orders
    total_stats = {
        'orders': 0,
        'order_items': 0,
        'order_item_addons': 0,
        'order_taxes': 0,
        'order_discounts': 0,
        'errors': []
    }
    
    for i, order_payload in enumerate(orders, 1):
        if i % 10 == 0:
            print(f"  Processing order {i}/{len(orders)}...")
        
        stats = process_order(conn, order_payload, item_matcher)
        
        for key in total_stats:
            if key == 'errors':
                total_stats[key].extend(stats[key])
            else:
                total_stats[key] += stats[key]
    
    # Print summary
    print("\n" + "=" * 80)
    print("LOADING SUMMARY")
    print("=" * 80)
    print(f"Orders processed: {total_stats['orders']}")
    print(f"Order items: {total_stats['order_items']}")
    print(f"Order item addons: {total_stats['order_item_addons']}")
    print(f"Taxes: {total_stats['order_taxes']}")
    print(f"Discounts: {total_stats['order_discounts']}")
    
    if total_stats['errors']:
        print(f"\n⚠️  Errors encountered: {len(total_stats['errors'])}")
        for error in total_stats['errors'][:10]:  # Show first 10 errors
            print(f"  - {error}")
        if len(total_stats['errors']) > 10:
            print(f"  ... and {len(total_stats['errors']) - 10} more errors")
    
    conn.close()
    print("\n✅ Loading complete!")


if __name__ == "__main__":
    main()

