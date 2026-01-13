"""
Test Menu Data Loading Script - PostgreSQL Version

This script tests loading menu data into a PostgreSQL database.

Usage:
    # Using connection string
    python3 database/test_load_menu_postgresql.py --db-url "postgresql://user:password@localhost:5432/analytics"
    
    # Using individual parameters
    python3 database/test_load_menu_postgresql.py --host localhost --port 5432 --database analytics --user postgres --password mypassword
"""

import sys
import os
import argparse
from pathlib import Path

# Add parent directory to path to import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    from psycopg2.extras import execute_values
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    print("⚠️  psycopg2 not installed. Install it with: pip install psycopg2-binary")

from database.load_menu_data import (
    load_cleaned_menu,
    extract_unique_menu_items,
    extract_unique_variants,
    generate_python_dicts
)


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


def create_postgresql_schema(conn):
    """Create database schema in PostgreSQL"""
    cursor = conn.cursor()
    
    # Read schema files and execute
    schema_dir = Path(__file__).parent / "schema"
    
    # Create menu_items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            menu_item_id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            type VARCHAR(50) NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            petpooja_itemid BIGINT,
            itemcode VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, type)
        )
    """)
    
    # Create variants table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS variants (
            variant_id SERIAL PRIMARY KEY,
            variant_name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create menu_item_variants table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_item_variants (
            menu_item_variant_id SERIAL PRIMARY KEY,
            menu_item_id INTEGER NOT NULL,
            variant_id INTEGER NOT NULL,
            price DECIMAL(10,2),
            is_active BOOLEAN DEFAULT TRUE,
            addon_eligible BOOLEAN DEFAULT FALSE,
            delivery_eligible BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(menu_item_id, variant_id),
            FOREIGN KEY (menu_item_id) REFERENCES menu_items(menu_item_id) ON DELETE CASCADE,
            FOREIGN KEY (variant_id) REFERENCES variants(variant_id) ON DELETE CASCADE
        )
    """)
    
    # Create indexes
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_menu_items_name_type 
        ON menu_items(name, type)
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_menu_item_variants_menu_variant 
        ON menu_item_variants(menu_item_id, variant_id)
    """)
    
    conn.commit()
    return conn


def clear_existing_data(conn, confirm=False):
    """Clear existing menu data from database"""
    if not confirm:
        return
    
    cursor = conn.cursor()
    
    print("   Clearing existing menu data...")
    cursor.execute("DELETE FROM menu_item_variants")
    cursor.execute("DELETE FROM variants")
    cursor.execute("DELETE FROM menu_items")
    
    # Reset sequences
    cursor.execute("ALTER SEQUENCE menu_items_menu_item_id_seq RESTART WITH 1")
    cursor.execute("ALTER SEQUENCE variants_variant_id_seq RESTART WITH 1")
    cursor.execute("ALTER SEQUENCE menu_item_variants_menu_item_variant_id_seq RESTART WITH 1")
    
    conn.commit()
    print("   ✓ Cleared existing data")


def insert_menu_items(conn, menu_items_data):
    """Insert menu items into PostgreSQL database"""
    cursor = conn.cursor()
    
    # Use ON CONFLICT to handle duplicates
    values = [(item['name'], item['type'], item['is_active']) for item in menu_items_data]
    
    execute_values(
        cursor,
        """
        INSERT INTO menu_items (name, type, is_active)
        VALUES %s
        ON CONFLICT (name, type) DO NOTHING
        """,
        values
    )
    
    conn.commit()
    
    # Now query all menu items to get their IDs in the order we need
    inserted_ids = []
    for item in menu_items_data:
        cursor.execute(
            "SELECT menu_item_id FROM menu_items WHERE name = %s AND type = %s",
            (item['name'], item['type'])
        )
        result = cursor.fetchone()
        if result:
            inserted_ids.append(result[0])
        else:
            raise ValueError(f"Could not find menu_item_id for {item['name']} ({item['type']})")
    
    return inserted_ids


def insert_variants(conn, variants_data):
    """Insert variants into PostgreSQL database"""
    cursor = conn.cursor()
    
    # Use ON CONFLICT to handle duplicates
    values = [(variant['variant_name'],) for variant in variants_data]
    
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
    
    # Now query all variants to get their IDs in the order we need
    inserted_ids = []
    for variant in variants_data:
        cursor.execute(
            "SELECT variant_id FROM variants WHERE variant_name = %s",
            (variant['variant_name'],)
        )
        result = cursor.fetchone()
        if result:
            inserted_ids.append(result[0])
        else:
            raise ValueError(f"Could not find variant_id for {variant['variant_name']}")
    
    return inserted_ids


def insert_menu_item_variants(conn, menu_item_variants_data, menu_item_ids, variant_ids):
    """Insert menu item variants into PostgreSQL database"""
    cursor = conn.cursor()
    
    values = []
    for miv in menu_item_variants_data:
        # Map indices to actual IDs (indices are 1-based in the data structure)
        menu_item_idx = miv['menu_item_id'] - 1  # Convert to 0-based
        variant_idx = miv['variant_id'] - 1
        
        # Validate indices
        if menu_item_idx < 0 or menu_item_idx >= len(menu_item_ids):
            print(f"   ⚠️  Warning: Invalid menu_item_id index {miv['menu_item_id']} (max: {len(menu_item_ids)})")
            continue
        if variant_idx < 0 or variant_idx >= len(variant_ids):
            print(f"   ⚠️  Warning: Invalid variant_id index {miv['variant_id']} (max: {len(variant_ids)})")
            continue
        
        menu_item_id = menu_item_ids[menu_item_idx]
        variant_id = variant_ids[variant_idx]
        
        values.append((
            menu_item_id,
            variant_id,
            miv['price'],
            miv['is_active'],
            miv['addon_eligible'],
            miv['delivery_eligible']
        ))
    
    if not values:
        print("   ⚠️  No valid menu item variants to insert")
        return 0
    
    # Use ON CONFLICT to handle duplicates
    execute_values(
        cursor,
        """
        INSERT INTO menu_item_variants 
        (menu_item_id, variant_id, price, is_active, addon_eligible, delivery_eligible)
        VALUES %s
        ON CONFLICT (menu_item_id, variant_id) DO NOTHING
        """,
        values
    )
    
    inserted_count = len(values)
    conn.commit()
    return inserted_count


def validate_data(conn):
    """Validate that data was loaded correctly"""
    cursor = conn.cursor()
    
    # Count records
    cursor.execute("SELECT COUNT(*) FROM menu_items")
    menu_items_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM variants")
    variants_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM menu_item_variants")
    menu_item_variants_count = cursor.fetchone()[0]
    
    # Check for duplicates
    cursor.execute("""
        SELECT name, type, COUNT(*) as count
        FROM menu_items
        GROUP BY name, type
        HAVING COUNT(*) > 1
    """)
    duplicates = cursor.fetchall()
    
    # Check for addon eligible items
    cursor.execute("""
        SELECT COUNT(*) 
        FROM menu_item_variants 
        WHERE addon_eligible = TRUE
    """)
    addon_eligible_count = cursor.fetchone()[0]
    
    # Check for non-delivery eligible items
    cursor.execute("""
        SELECT COUNT(*) 
        FROM menu_item_variants 
        WHERE delivery_eligible = FALSE
    """)
    non_delivery_count = cursor.fetchone()[0]
    
    # Sample data
    cursor.execute("""
        SELECT mi.name, mi.type, v.variant_name, miv.addon_eligible, miv.delivery_eligible
        FROM menu_items mi
        JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        LIMIT 5
    """)
    samples = cursor.fetchall()
    
    return {
        'menu_items_count': menu_items_count,
        'variants_count': variants_count,
        'menu_item_variants_count': menu_item_variants_count,
        'duplicates': duplicates,
        'addon_eligible_count': addon_eligible_count,
        'non_delivery_count': non_delivery_count,
        'samples': samples
    }


def main():
    parser = argparse.ArgumentParser(description='Test menu data loading into PostgreSQL')
    parser.add_argument('--db-url', help='PostgreSQL connection URL (postgresql://user:pass@host:port/db)')
    parser.add_argument('--host', default='localhost', help='Database host (default: localhost)')
    parser.add_argument('--port', type=int, default=5432, help='Database port (default: 5432)')
    parser.add_argument('--database', help='Database name (required if not using --db-url)')
    parser.add_argument('--user', help='Database user (required if not using --db-url)')
    parser.add_argument('--password', help='Database password (required if not using --db-url)')
    parser.add_argument('--csv', default='cleaned_menu.csv', help='Path to cleaned_menu.csv')
    parser.add_argument('--clear', action='store_true', help='Clear existing menu data before loading')
    args = parser.parse_args()
    
    if not PSYCOPG2_AVAILABLE:
        print("❌ ERROR: psycopg2 is not installed.")
        print("   Install it with: pip install psycopg2-binary")
        return
    
    print("=" * 80)
    print("Testing Menu Data Loading - PostgreSQL")
    print("=" * 80)
    
    # Load CSV data
    print(f"\n1. Loading {args.csv}...")
    menu_data = load_cleaned_menu(args.csv)
    print(f"   Loaded {len(menu_data)} menu entries")
    
    # Extract unique data
    print(f"\n2. Extracting unique data...")
    unique_items = extract_unique_menu_items(menu_data)
    unique_variants = extract_unique_variants(menu_data)
    print(f"   - {len(unique_items)} unique menu items")
    print(f"   - {len(unique_variants)} unique variants")
    
    # Generate Python data structures
    print(f"\n3. Generating data structures...")
    menu_items_data, variants_data, menu_item_variants_data = generate_python_dicts(menu_data)
    print(f"   - {len(menu_items_data)} menu_items records")
    print(f"   - {len(variants_data)} variants records")
    print(f"   - {len(menu_item_variants_data)} menu_item_variants records")
    
    # Create database connection
    print(f"\n4. Connecting to PostgreSQL database...")
    try:
        if args.db_url:
            conn = create_postgresql_connection(None, None, None, None, None, db_url=args.db_url)
            print(f"   ✓ Connected using connection URL")
        else:
            if not args.database or not args.user:
                print("   ❌ ERROR: --database and --user are required if not using --db-url")
                return
            conn = create_postgresql_connection(
                args.host,
                args.port,
                args.database,
                args.user,
                args.password
            )
            print(f"   ✓ Connected to {args.host}:{args.port}/{args.database}")
    except Exception as e:
        print(f"   ❌ ERROR: Failed to connect to database: {e}")
        return
    
    # Create schema
    print(f"\n5. Creating database schema...")
    try:
        create_postgresql_schema(conn)
        print(f"   ✓ Schema created/verified")
    except Exception as e:
        print(f"   ❌ ERROR: Failed to create schema: {e}")
        conn.close()
        return
    
    # Clear existing data if requested
    if args.clear:
        clear_existing_data(conn, confirm=True)
    
    # Insert data
    print(f"\n6. Inserting data into database...")
    
    try:
        # Handle Aliases (Version 2)
        # 1. First, import the parsing table from CSV to ensure we have the latest mappings
        from utils.menu_utils import import_parsing_table_from_csv
        import_parsing_table_from_csv(conn)
        
        # 2. Then check for items that are marked as aliases
        cursor = conn.cursor()
        aliases = []
        try:
            cursor.execute("SELECT raw_name FROM item_parsing_table WHERE raw_name != cleaned_name")
            aliases = [row[0].lower() for row in cursor.fetchall()]
            if aliases:
                print(f"   Found {len(aliases)} merged aliases in parsing table. Filtering...")
        except Exception:
            # Table might not exist yet, that's fine
            conn.rollback()
        
        filtered_menu_items = [
            item for item in menu_items_data 
            if item['name'].lower() not in aliases
        ]
        
        print(f"   Inserting {len(filtered_menu_items)} menu items...")
        menu_item_ids = insert_menu_items(conn, filtered_menu_items)
        print(f"   ✓ Inserted {len(menu_item_ids)} menu items")
        
        print(f"   Inserting {len(variants_data)} variants...")
        variant_ids = insert_variants(conn, variants_data)
        print(f"   ✓ Inserted {len(variant_ids)} variants")
        
        print(f"   Inserting {len(menu_item_variants_data)} menu item variants...")
        miv_count = insert_menu_item_variants(conn, menu_item_variants_data, menu_item_ids, variant_ids)
        print(f"   ✓ Inserted {miv_count} menu item variants")
    except Exception as e:
        print(f"   ❌ ERROR: Failed to insert data: {e}")
        conn.rollback()
        conn.close()
        return
    
    # Validate data
    print(f"\n7. Validating data...")
    validation = validate_data(conn)
    
    print(f"\n" + "=" * 80)
    print("VALIDATION RESULTS")
    print("=" * 80)
    print(f"Menu Items: {validation['menu_items_count']} (expected: {len(menu_items_data)})")
    print(f"Variants: {validation['variants_count']} (expected: {len(variants_data)})")
    print(f"Menu Item Variants: {validation['menu_item_variants_count']} (expected: {len(menu_item_variants_data)})")
    print(f"Addon Eligible: {validation['addon_eligible_count']}")
    print(f"Non-Delivery Eligible: {validation['non_delivery_count']}")
    
    if validation['duplicates']:
        print(f"\n⚠️  WARNING: Found {len(validation['duplicates'])} duplicate menu items!")
        for name, type, count in validation['duplicates']:
            print(f"   - {name} ({type}): {count} occurrences")
    else:
        print(f"\n✓ No duplicate menu items found")
    
    print(f"\nSample Data:")
    for name, type, variant, addon_eligible, delivery_eligible in validation['samples']:
        print(f"   - {name} ({type}) - {variant}")
        print(f"     addon_eligible: {addon_eligible}, delivery_eligible: {delivery_eligible}")
    
    # Test queries
    print(f"\n8. Running test queries...")
    cursor = conn.cursor()
    
    # Query 1: Get all variants for a specific menu item
    cursor.execute("""
        SELECT v.variant_name, miv.price, miv.addon_eligible, miv.delivery_eligible
        FROM menu_items mi
        JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        WHERE mi.name = %s
        ORDER BY v.variant_name
    """, ('Banoffee Ice Cream',))
    banoffee_variants = cursor.fetchall()
    print(f"\n   Banoffee Ice Cream variants:")
    for variant, price, addon, delivery in banoffee_variants:
        print(f"     - {variant} (price: {price}, addon: {addon}, delivery: {delivery})")
    
    # Query 2: Get all addon-eligible items
    cursor.execute("""
        SELECT mi.name, mi.type, v.variant_name
        FROM menu_items mi
        JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        WHERE miv.addon_eligible = TRUE
        LIMIT 5
    """)
    addon_items = cursor.fetchall()
    print(f"\n   Addon-eligible items (sample):")
    for name, type, variant in addon_items:
        print(f"     - {name} ({type}) - {variant}")
    
    # Query 3: Count items by type
    cursor.execute("""
        SELECT type, COUNT(DISTINCT menu_item_id) as count
        FROM menu_items
        GROUP BY type
        ORDER BY count DESC
    """)
    type_counts = cursor.fetchall()
    print(f"\n   Items by type:")
    for type, count in type_counts:
        print(f"     - {type}: {count}")
    
    conn.close()
    
    print(f"\n" + "=" * 80)
    print("✅ TEST COMPLETE")
    print("=" * 80)
    print(f"All data loaded and validated successfully in PostgreSQL!")


if __name__ == "__main__":
    main()

