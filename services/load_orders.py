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
import logging
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
    """Apply the one canonical profile schema/migration owner."""
    from src.core.db.connection import apply_analytics_schema

    apply_analytics_schema(conn)


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


def normalize_optional_text(value: Optional[str]) -> Optional[str]:
    """Trim a value and collapse empty strings to None."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def identity_key_implies_verified(identity_key: str) -> bool:
    return not str(identity_key or "").startswith("anon:")


def upsert_customer_address(conn, customer_id: int, address: Optional[str], label: str = "Primary") -> None:
    """
    Persist a structured address row for a customer.

    Current ingestion only receives a single free-form address string, so it is
    stored in address_line_1 until richer source fields are available.
    """
    address_line_1 = normalize_optional_text(address)
    if not address_line_1:
        return

    cursor = conn.cursor()
    cursor.execute("""
        SELECT address_id
        FROM customer_addresses
        WHERE customer_id = ?
          AND lower(trim(COALESCE(address_line_1, ''))) = lower(trim(?))
          AND trim(COALESCE(address_line_2, '')) = ''
          AND trim(COALESCE(city, '')) = ''
          AND trim(COALESCE(state, '')) = ''
          AND trim(COALESCE(postal_code, '')) = ''
          AND trim(COALESCE(country, '')) = ''
        ORDER BY is_default DESC, address_id ASC
        LIMIT 1
    """, (customer_id, address_line_1))
    existing = cursor.fetchone()

    if existing:
        cursor.execute("""
            UPDATE customer_addresses
            SET label = COALESCE(label, ?),
                updated_at = CURRENT_TIMESTAMP
            WHERE address_id = ?
        """, (label, existing[0]))
        return

    cursor.execute("""
        SELECT 1
        FROM customer_addresses
        WHERE customer_id = ?
          AND is_default = 1
        LIMIT 1
    """, (customer_id,))
    has_default = cursor.fetchone() is not None

    cursor.execute("""
        INSERT OR IGNORE INTO customer_addresses (
            customer_id,
            label,
            address_line_1,
            is_default
        )
        VALUES (?, ?, ?, ?)
    """, (customer_id, label, address_line_1, 0 if has_default else 1))


def resolve_active_customer_target(conn, customer_id: int) -> int:
    """Follow active customer merge history so new orders land on the surviving customer."""
    current_customer_id = int(customer_id)
    visited = set()

    while current_customer_id not in visited:
        visited.add(current_customer_id)
        row = conn.execute("""
            SELECT target_customer_id
            FROM customer_merge_history
            WHERE source_customer_id = ?
              AND undone_at IS NULL
            ORDER BY merge_id DESC
            LIMIT 1
        """, (current_customer_id,)).fetchone()
        if not row:
            break
        current_customer_id = int(row[0])

    return current_customer_id

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
    
    phone = normalize_optional_text(customer_data.get('phone'))
    name = normalize_optional_text(customer_data.get('name')) or 'Anonymous'
    address = normalize_optional_text(customer_data.get('address'))
    gstin = normalize_optional_text(customer_data.get('gstin'))
    
    # Normalize name for deduplication (lowercase, trimmed)
    name_normalized = name.lower().strip()
    
    # Check if customer exists by normalized name
    identity_key = compute_customer_identity_key(customer_data)
    cursor.execute("SELECT customer_id FROM customers WHERE customer_identity_key = ?", (identity_key,))
    
    result = cursor.fetchone()
    
    # Format date for storage
    order_date_str = order_date.strftime('%Y-%m-%d %H:%M:%S') if order_date else None
    
    if result:
        customer_id = resolve_active_customer_target(conn, result[0])
        
        # Determine update logic
        update_fields = []
        update_values = []
        
        update_fields.append("last_order_date = ?")
        update_values.append(order_date_str)
        
        update_fields.append("total_orders = COALESCE(total_orders, 0) + 1")
        
        update_fields.append("total_spent = COALESCE(total_spent, 0) + ?")
        update_values.append(float(order_total))
        
        # Calculate verification status
        derived_is_verified = identity_key_implies_verified(identity_key)
        update_fields.append("is_verified = CASE WHEN is_verified = 1 OR ? = 1 THEN 1 ELSE 0 END")
        update_values.append(1 if derived_is_verified else 0)
        
        # Update phone if provided and currently NULL
        if phone:
            update_fields.append("phone = COALESCE(phone, ?)")
            update_values.append(phone)

        # Preserve the first legacy address for compatibility, while the structured
        # address book stores additional addresses separately.
        if address:
            update_fields.append("address = COALESCE(address, ?)")
            update_values.append(address)
        
        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        
        update_values.append(customer_id)
        
        sql = f"UPDATE customers SET {', '.join(update_fields)} WHERE customer_id = ?"
        cursor.execute(sql, update_values)
        upsert_customer_address(conn, customer_id, address)
        conn.commit()
    else:
        # Insert new customer
        is_verified = identity_key_implies_verified(identity_key)
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
        upsert_customer_address(conn, customer_id, address)
        conn.commit()
    
    return customer_id


def _ensure_customer_no_stats(
    conn, customer_data: Dict, order_date: datetime, fallback_customer_id: Optional[int] = None
) -> Optional[int]:
    """Resolve (or create) a customer WITHOUT touching aggregates or committing.

    Used on order replay. The caller recomputes customer aggregates from the
    orders table afterwards, so seeding total_orders/total_spent here would
    double-count, and a mid-order commit would break process_order()'s
    single-transaction guarantee.

    fallback_customer_id is the order's current owner. For anonymous customers
    the identity key is minted fresh per call (anon:<uuid4>), so the lookup
    below can never match the row created on first ingest — recomputing would
    insert a duplicate customer and strand the previous owner as a husk on
    every replay. Reuse the current owner instead; deterministic keys
    (phone:/addr:) still resolve normally so legitimate re-keying (e.g. an
    order that gained a phone number) keeps working.
    """
    cursor = conn.cursor()
    identity_key = compute_customer_identity_key(customer_data)
    if identity_key.startswith("anon:") and fallback_customer_id is not None:
        return resolve_active_customer_target(conn, fallback_customer_id)
    cursor.execute("SELECT customer_id FROM customers WHERE customer_identity_key = ?", (identity_key,))
    result = cursor.fetchone()
    if result:
        return resolve_active_customer_target(conn, result[0])

    phone = normalize_optional_text(customer_data.get('phone'))
    name = normalize_optional_text(customer_data.get('name')) or 'Anonymous'
    address = normalize_optional_text(customer_data.get('address'))
    gstin = normalize_optional_text(customer_data.get('gstin'))
    name_normalized = name.lower().strip()
    order_date_str = order_date.strftime('%Y-%m-%d %H:%M:%S') if order_date else None
    is_verified = identity_key_implies_verified(identity_key)

    cursor.execute("""
        INSERT INTO customers (
            customer_identity_key,
            name, name_normalized, phone, address, gstin,
            first_order_date, last_order_date,
            total_orders, total_spent, is_verified
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
        RETURNING customer_id
    """, (
        identity_key,
        name, name_normalized, phone, address, gstin,
        order_date_str, order_date_str,
        1 if is_verified else 0,
    ))
    customer_id = cursor.fetchone()[0]
    upsert_customer_address(conn, customer_id, address)
    return customer_id


def sweep_orphan_customers(cursor) -> List[int]:
    """State-scoped GC for customer husks (see MENU_SYNC_ARCHITECTURE.md §2.4.1).

    Deletes customers that hold no references anywhere: zero orders and no
    customer_merge_history row (source or target — merge lineage must survive,
    the history FKs have no cascade and undo needs the snapshots). A 7-day
    created_at grace window protects rows created moments before their first
    order lands (NULL created_at = legacy row = old enough). Schemas without
    customers.created_at skip the sweep entirely.

    Returns the deleted customer_ids.
    """
    cursor.execute("PRAGMA table_info(customers)")
    if not any(row[1] == "created_at" for row in cursor.fetchall()):
        return []
    cursor.execute(
        """
        SELECT c.customer_id FROM customers c
        WHERE (c.created_at IS NULL OR c.created_at < datetime('now', '-7 days'))
            AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)
            AND NOT EXISTS (
                SELECT 1 FROM customer_merge_history m
                WHERE m.source_customer_id = c.customer_id
                    OR m.target_customer_id = c.customer_id
            )
        """
    )
    husk_ids = [int(row[0]) for row in cursor.fetchall()]
    if not husk_ids:
        return []
    placeholders = ",".join("?" for _ in husk_ids)
    # customer_addresses declares ON DELETE CASCADE, but not every connection
    # enforces foreign keys — delete explicitly so no orphan addresses remain.
    cursor.execute(
        f"DELETE FROM customer_addresses WHERE customer_id IN ({placeholders})", husk_ids
    )
    cursor.execute(f"DELETE FROM customers WHERE customer_id IN ({placeholders})", husk_ids)
    return husk_ids


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
        cursor.execute("SELECT order_id, customer_id FROM orders WHERE petpooja_order_id = ?", (petpooja_order_id,))
        exists = cursor.fetchone()
        previous_customer_id = exists[1] if exists else None
        
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
            # Replay: resolve/create the customer WITHOUT seeding aggregates or
            # committing. The order upsert below may re-point this order to a
            # different customer, and the customer recompute at the end rebuilds
            # both the old and new owners' aggregates from the orders table.
            # Incrementing here (or committing mid-order) would double-count and
            # break process_order()'s single-transaction guarantee.
            customer_id = _ensure_customer_no_stats(
                conn, customer_data, created_on, fallback_customer_id=previous_customer_id
            )
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
                aggregate_id = excluded.aggregate_id,
                customer_id = excluded.customer_id,
                restaurant_id = excluded.restaurant_id,
                occurred_at = excluded.occurred_at,
                created_on = excluded.created_on,
                order_type = excluded.order_type,
                order_from = excluded.order_from,
                sub_order_type = excluded.sub_order_type,
                order_from_id = excluded.order_from_id,
                order_status = excluded.order_status,
                biller = excluded.biller,
                assignee = excluded.assignee,
                table_no = excluded.table_no,
                token_no = excluded.token_no,
                no_of_persons = excluded.no_of_persons,
                customer_invoice_id = excluded.customer_invoice_id,
                core_total = excluded.core_total,
                tax_total = excluded.tax_total,
                discount_total = excluded.discount_total,
                delivery_charges = excluded.delivery_charges,
                packaging_charge = excluded.packaging_charge,
                service_charge = excluded.service_charge,
                round_off = excluded.round_off,
                total = excluded.total,
                comment = excluded.comment,
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
        # Do NOT commit the header here. Header + items/addons + taxes + discounts
        # must land as one atomic unit; a premature header commit advances the
        # incremental watermark (MAX(stream_id)) even if item loading later fails,
        # stranding the order partially loaded. Single commit at the end;
        # stats['orders'] is set only once that commit succeeds.

        # Re-ingest / event-replay projection. The orders stream is append-only:
        # PetPooja re-sends the same orderID on edits/cancels, each as a new
        # stream_id. We treat the latest event as full truth (header refreshed by
        # the upsert above), so wipe this order's children before rebuilding them
        # to avoid duplicate order_items/addons. Capture the menu_items this order
        # previously touched so their aggregate stats get recomputed afterwards.
        touched_menu_items = set()
        if exists:
            cursor.execute("""
                SELECT menu_item_id FROM order_items
                WHERE order_id = ? AND menu_item_id IS NOT NULL
                UNION
                SELECT oia.menu_item_id
                FROM order_item_addons oia
                JOIN order_items oi ON oia.order_item_id = oi.order_item_id
                WHERE oi.order_id = ? AND oia.menu_item_id IS NOT NULL
            """, (order_id, order_id))
            touched_menu_items = {r[0] for r in cursor.fetchall()}

            cursor.execute("""
                DELETE FROM order_item_addons
                WHERE order_item_id IN (
                    SELECT order_item_id FROM order_items WHERE order_id = ?
                )
            """, (order_id,))
            cursor.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
            cursor.execute("DELETE FROM order_taxes WHERE order_id = ?", (order_id,))
            cursor.execute("DELETE FROM order_discounts WHERE order_id = ?", (order_id,))

        # Order Items
        for item_data in order_items_data:
            raw_name = item_data.get('name', '')
            # Pass both parent-key inputs: itemid stays the first-priority key,
            # itemcode enables parent-level routing for unseen variants.
            menu_item_id, _, variant_id, _item_type, match_method, match_confidence = item_cluster.add(
                raw_name,
                item_data.get('itemid'),
                itemcode=item_data.get('itemcode'),
                restaurant_id=restaurant_id,
            )
            
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
            if menu_item_id:
                touched_menu_items.add(menu_item_id)

            # New order: increment counters inline (fast path). Re-ingested order:
            # skip — counters are recomputed from source rows at the end so replays
            # don't double-count.
            if menu_item_id and order_data.get('status') == 'Success' and not exists:
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
                addon_menu_item_id, _, addon_variant_id, _addon_item_type, addon_match_method, addon_match_confidence = item_cluster.add(addon_raw_name, addon_data.get('addonid'), is_addon=True)
                
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
                if addon_menu_item_id:
                    touched_menu_items.add(addon_menu_item_id)

                if addon_menu_item_id and order_data.get('status') == 'Success' and not exists:
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

        # Re-ingested order: children were deleted+rebuilt, so incremental counters
        # would be stale. Recompute the affected menu_items from source rows
        # (status-aware, idempotent) — covers items dropped by this edit (captured
        # before delete) and items now present.
        if exists and touched_menu_items:
            from utils.menu_utils import _recalculate_menu_item_stats
            for menu_item_id in touched_menu_items:
                _recalculate_menu_item_stats(cursor, menu_item_id)

        # Re-ingested order may have changed its total and/or moved to a different
        # customer. Incremental customer counters only run on first ingest, so
        # recompute affected customers' aggregates from the orders table (mirrors
        # the menu_item recompute above). Covers the previous owner (may have lost
        # this order) and the current owner. The order upsert already re-pointed
        # orders.customer_id, so SUM(total) sees the corrected state.
        if exists:
            from src.core.queries.customer_merge_helpers import recompute_customer_aggregates
            for cid in {previous_customer_id, customer_id}:
                if cid is not None:
                    recompute_customer_aggregates(conn, cid)

        conn.commit()
        stats['orders'] = 1

        # Drain deferred mapping-verification retries now that the order is
        # durably committed (moved out of OrderItemCluster.add() so it no longer
        # commits mid-order). Runs on its own transaction; a failure here must
        # not fail the already-committed order, hence the guarded catch.
        try:
            from src.core.menu_mapping_verification_sync import (
                flush_deferred_menu_mapping_verifications,
            )
            flush_deferred_menu_mapping_verifications(conn)
        except Exception:
            logging.getLogger(__name__).debug(
                "Deferred mapping verification flush skipped after order commit",
                exc_info=True,
            )

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

def _sweep_husks_cli(conn) -> None:
    """Husk GC for every CLI exit (MENU_SYNC_ARCHITECTURE.md §2.4.1).

    Best-effort: a sweep failure must not mask an already-committed ingest.
    """
    try:
        cursor = conn.cursor()
        try:
            swept = sweep_orphan_customers(cursor)
            conn.commit()
        finally:
            cursor.close()
    except Exception as e:
        conn.rollback()
        print(f"⚠️ Orphan customer sweep failed: {e}")
        return
    if swept:
        print(f"🧹 Swept {len(swept)} orphan customer record(s).")

def main():
    parser = argparse.ArgumentParser(description="Load order data into SQLite database")
    parser.add_argument('--input-file', type=str, help="Path to JSON file with orders")
    parser.add_argument('--incremental', action='store_true', help="Only load new orders")
    parser.add_argument('--limit', type=int, help="Limit number of orders")
    parser.add_argument('--restaurant-id', type=str, help="Bound restaurant profile for scoped central pulls")
    
    # Legacy args kept for compatibility. --db-url is an explicit standalone
    # SQLite path; Electron/runtime callers use --restaurant-id profiles.
    parser.add_argument('--db-url', type=str, help="Explicit standalone SQLite path")
    parser.add_argument('--host', type=str, help="Ignored")
    parser.add_argument('--port', type=int, help="Ignored")
    parser.add_argument('--database', type=str, help="Ignored")
    parser.add_argument('--user', type=str, help="Ignored")
    parser.add_argument('--password', type=str, help="Ignored")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Order Data Loading Script (SQLite)")
    print("=" * 60)
    
    if args.restaurant_id:
        from src.core.db.connection import get_profile_connection
        from src.core.profiles import get_profile

        try:
            conn, msg = get_profile_connection(get_profile(args.restaurant_id, require_authorized=True))
        except Exception as exc:
            print(f"❌ Profile connection failed: {exc}")
            return
    else:
        conn, msg = get_db_connection(args.db_url)
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
        orders, _ = fetch_stream_raw(conn, endpoint="orders", start_cursor=last_id)
    else:
        print("  Fetching all orders...")
        orders, _ = fetch_stream_raw(conn, endpoint="orders", max_records=args.limit)
    
    if not orders:
        print("No orders to load.")
        _sweep_husks_cli(conn)
        conn.close()
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

    _sweep_husks_cli(conn)
    conn.close()

if __name__ == "__main__":
    main()
