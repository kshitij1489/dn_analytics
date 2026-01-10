"""
Test Menu Data Loading Script

This script tests loading menu data into a database.
Uses SQLite by default (no setup required), but can be adapted to PostgreSQL.

Usage:
    # Test with SQLite (default)
    python3 database/test_load_menu.py
    
    # Test with PostgreSQL
    python3 database/test_load_menu.py --db postgresql --db-url "postgresql://user:pass@localhost/analytics"
"""

import sqlite3
import sys
import os
import argparse
from pathlib import Path

# Add parent directory to path to import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.load_menu_data import (
    load_cleaned_menu,
    extract_unique_menu_items,
    extract_unique_variants,
    generate_python_dicts,
    determine_addon_eligibility,
    determine_delivery_eligibility
)


def create_sqlite_schema(db_path: str):
    """Create database schema in SQLite"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Read and execute schema files
    schema_dir = Path(__file__).parent / "schema"
    
    # Create menu_items table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_items (
            menu_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(255) NOT NULL,
            type VARCHAR(50) NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            petpooja_itemid INTEGER,
            itemcode VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name, type)
        )
    """)
    
    # Create variants table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS variants (
            variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
            variant_name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create menu_item_variants table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS menu_item_variants (
            menu_item_variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
            menu_item_id INTEGER NOT NULL,
            variant_id INTEGER NOT NULL,
            price DECIMAL(10,2),
            is_active BOOLEAN DEFAULT 1,
            addon_eligible BOOLEAN DEFAULT 0,
            delivery_eligible BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(menu_item_id, variant_id),
            FOREIGN KEY (menu_item_id) REFERENCES menu_items(menu_item_id) ON DELETE CASCADE,
            FOREIGN KEY (variant_id) REFERENCES variants(variant_id) ON DELETE CASCADE
        )
    """)
    
    conn.commit()
    return conn


def insert_menu_items(conn, menu_items_data):
    """Insert menu items into database"""
    cursor = conn.cursor()
    inserted_ids = []
    
    for item in menu_items_data:
        cursor.execute("""
            INSERT INTO menu_items (name, type, is_active)
            VALUES (?, ?, ?)
        """, (item['name'], item['type'], item['is_active']))
        inserted_ids.append(cursor.lastrowid)
    
    conn.commit()
    return inserted_ids


def insert_variants(conn, variants_data):
    """Insert variants into database"""
    cursor = conn.cursor()
    inserted_ids = []
    
    for variant in variants_data:
        cursor.execute("""
            INSERT INTO variants (variant_name)
            VALUES (?)
        """, (variant['variant_name'],))
        inserted_ids.append(cursor.lastrowid)
    
    conn.commit()
    return inserted_ids


def insert_menu_item_variants(conn, menu_item_variants_data, menu_item_ids, variant_ids):
    """Insert menu item variants into database"""
    cursor = conn.cursor()
    inserted_count = 0
    
    for miv in menu_item_variants_data:
        # Map indices to actual IDs
        menu_item_id = menu_item_ids[miv['menu_item_id'] - 1]  # -1 because indices are 0-based
        variant_id = variant_ids[miv['variant_id'] - 1]
        
        cursor.execute("""
            INSERT INTO menu_item_variants 
            (menu_item_id, variant_id, price, is_active, addon_eligible, delivery_eligible)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            menu_item_id,
            variant_id,
            miv['price'],
            miv['is_active'],
            miv['addon_eligible'],
            miv['delivery_eligible']
        ))
        inserted_count += 1
    
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
        HAVING count > 1
    """)
    duplicates = cursor.fetchall()
    
    # Check for addon eligible items
    cursor.execute("""
        SELECT COUNT(*) 
        FROM menu_item_variants 
        WHERE addon_eligible = 1
    """)
    addon_eligible_count = cursor.fetchone()[0]
    
    # Check for non-delivery eligible items
    cursor.execute("""
        SELECT COUNT(*) 
        FROM menu_item_variants 
        WHERE delivery_eligible = 0
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
    parser = argparse.ArgumentParser(description='Test menu data loading')
    parser.add_argument('--db', choices=['sqlite', 'postgresql'], default='sqlite',
                       help='Database type (default: sqlite)')
    parser.add_argument('--db-url', help='Database connection URL')
    parser.add_argument('--db-path', default='test_analytics.db',
                       help='SQLite database file path (default: test_analytics.db)')
    parser.add_argument('--csv', default='cleaned_menu.csv', help='Path to cleaned_menu.csv')
    args = parser.parse_args()
    
    print("=" * 80)
    print("Testing Menu Data Loading")
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
    print(f"\n4. Creating database connection...")
    if args.db == 'sqlite':
        db_path = args.db_path
        if os.path.exists(db_path):
            print(f"   Removing existing database: {db_path}")
            os.remove(db_path)
        conn = create_sqlite_schema(db_path)
        print(f"   Created SQLite database: {db_path}")
    elif args.db == 'postgresql':
        # TODO: Implement PostgreSQL connection
        print("   PostgreSQL not yet implemented. Use SQLite for testing.")
        return
    else:
        print(f"   Unknown database type: {args.db}")
        return
    
    # Insert data
    print(f"\n5. Inserting data into database...")
    
    print(f"   Inserting {len(menu_items_data)} menu items...")
    menu_item_ids = insert_menu_items(conn, menu_items_data)
    print(f"   ✓ Inserted {len(menu_item_ids)} menu items")
    
    print(f"   Inserting {len(variants_data)} variants...")
    variant_ids = insert_variants(conn, variants_data)
    print(f"   ✓ Inserted {len(variant_ids)} variants")
    
    print(f"   Inserting {len(menu_item_variants_data)} menu item variants...")
    miv_count = insert_menu_item_variants(conn, menu_item_variants_data, menu_item_ids, variant_ids)
    print(f"   ✓ Inserted {miv_count} menu item variants")
    
    # Validate data
    print(f"\n6. Validating data...")
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
    print(f"\n7. Running test queries...")
    cursor = conn.cursor()
    
    # Query 1: Get all variants for a specific menu item
    cursor.execute("""
        SELECT v.variant_name, miv.price, miv.addon_eligible, miv.delivery_eligible
        FROM menu_items mi
        JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
        JOIN variants v ON miv.variant_id = v.variant_id
        WHERE mi.name = 'Banoffee Ice Cream'
        ORDER BY v.variant_name
    """)
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
        WHERE miv.addon_eligible = 1
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
    print(f"Database file: {db_path if args.db == 'sqlite' else 'N/A'}")
    print(f"All data loaded and validated successfully!")
    
    if args.db == 'sqlite':
        print(f"\nTo inspect the database:")
        print(f"  sqlite3 {db_path}")
        print(f"  .tables")
        print(f"  SELECT * FROM menu_items LIMIT 5;")


if __name__ == "__main__":
    main()

