"""
Test Item Matching Logic

This script tests the item matching functionality with the loaded menu data.

Usage:
    python3 data_cleaning/test_item_matcher.py --db-url "postgresql://user@localhost:5432/analytics"
"""

import sys
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False
    print("❌ psycopg2 not installed. Install with: pip install psycopg2-binary")
    sys.exit(1)

from data_cleaning.item_matcher import ItemMatcher


def get_sample_order_items():
    """Get sample order item names from actual order data"""
    return [
        # Exact matches (should match perfectly)
        "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))",
        "Banoffee Ice Cream (Mini Tub)",
        "Boston Cream Pie Dessert(2pcs)",
        "Waffle Cone",
        "Cup",
        
        # Items with typos (should still match)
        "Fig Orange Ice Cream (Regular Tub (300ml))",  # Missing "&"
        "Bean-to-bar Chocolate Dark Ice Cream (Mini tub (200ml))",  # Word order issue
        "Eggles Cherry & Chocolate 200ml",  # "Eggles" -> "Eggless"
        
        # Items with variations
        "Eggless Chocolate Overload (Regular Scoop)",
        "Eggless Cherry & Chocolate Ice Cream (Regular Scoop)",
        "Coffee Mascarpone Ice Cream (Perfect Plenty (300ml))",
        "Monkey Business Ice Cream (Regular Tub (300ml))",
        
        # Addons
        "Cup",
        "Waffle Cone",
        "Butter Waffle Cone (2pcs)",
        
        # Drinks
        "Affogato",
        "Americano",
        "Cappuccino",
        
        # Desserts
        "Classic Tiramisu",
        "Employee Dessert ( Any 1 )",
        "Fudgy Chocolate Brownie (1pc)",
        
        # Edge cases
        "Chocolate & Orange (contains Alcohol) (Regular Tub (220gms))",
        "Orange Ice Cream (with Alcohol) (Mini tub (160gms))",
    ]


def test_matching(matcher, test_items):
    """Test matching for a list of items"""
    results = []
    
    print("=" * 80)
    print("Testing Item Matching")
    print("=" * 80)
    
    for raw_name in test_items:
        result = matcher.match_item(raw_name, use_fuzzy=True, fuzzy_threshold=80)
        results.append({
            'raw_name': raw_name,
            **result
        })
    
    return results


def display_results(results):
    """Display matching results"""
    print("\n" + "=" * 80)
    print("MATCHING RESULTS")
    print("=" * 80)
    
    # Categorize results
    exact_matches = [r for r in results if r['match_method'] == 'exact']
    fuzzy_matches = [r for r in results if r['match_method'] == 'fuzzy']
    partial_matches = [r for r in results if r['match_method'] == 'partial']
    no_matches = [r for r in results if r['match_method'] is None]
    
    print(f"\n📊 Summary:")
    print(f"   Exact matches: {len(exact_matches)}")
    print(f"   Fuzzy matches: {len(fuzzy_matches)}")
    print(f"   Partial matches: {len(partial_matches)}")
    print(f"   No matches: {len(no_matches)}")
    print(f"   Total tested: {len(results)}")
    
    # Show exact matches
    if exact_matches:
        print(f"\n✅ Exact Matches ({len(exact_matches)}):")
        for r in exact_matches[:10]:
            print(f"   ✓ {r['raw_name']}")
            print(f"     → {r['cleaned_name']} ({r['cleaned_type']}) - {r['cleaned_variant']}")
            print(f"     → menu_item_id: {r['menu_item_id']}, variant_id: {r['variant_id']}")
    
    # Show fuzzy matches
    if fuzzy_matches:
        print(f"\n🔍 Fuzzy Matches ({len(fuzzy_matches)}):")
        for r in fuzzy_matches:
            print(f"   ~ {r['raw_name']}")
            print(f"     → {r['cleaned_name']} ({r['cleaned_type']}) - {r['cleaned_variant']}")
            print(f"     → menu_item_id: {r['menu_item_id']}, variant_id: {r['variant_id']}")
            print(f"     → Confidence: {r['match_confidence']}%")
    
    # Show partial matches
    if partial_matches:
        print(f"\n⚠️  Partial Matches ({len(partial_matches)}):")
        for r in partial_matches:
            print(f"   ⚠  {r['raw_name']}")
            print(f"     → {r['cleaned_name']} ({r['cleaned_type']}) - {r['cleaned_variant']}")
            print(f"     → menu_item_id: {r['menu_item_id']}, variant_id: {r['variant_id']}")
            print(f"     → Confidence: {r['match_confidence']}%")
    
    # Show no matches
    if no_matches:
        print(f"\n❌ No Matches ({len(no_matches)}):")
        for r in no_matches:
            print(f"   ✗ {r['raw_name']}")
            if 'cleaned_name' in r:
                print(f"     → Cleaned: {r['cleaned_name']} ({r['cleaned_type']}) - {r['cleaned_variant']}")
            if 'error' in r:
                print(f"     → Error: {r['error']}")


def verify_matches_in_db(conn, results):
    """Verify that matched IDs actually exist in database"""
    cursor = conn.cursor()
    
    print("\n" + "=" * 80)
    print("VERIFYING MATCHES IN DATABASE")
    print("=" * 80)
    
    verified = 0
    failed = 0
    
    for r in results:
        if r['menu_item_id'] and r['variant_id']:
            # Verify menu_item_id exists
            cursor.execute(
                "SELECT name, type FROM menu_items WHERE menu_item_id = %s",
                (r['menu_item_id'],)
            )
            menu_item = cursor.fetchone()
            
            # Verify variant_id exists
            cursor.execute(
                "SELECT variant_name FROM variants WHERE variant_id = %s",
                (r['variant_id'],)
            )
            variant = cursor.fetchone()
            
            # Verify menu_item_variant combination exists
            cursor.execute("""
                SELECT COUNT(*) FROM menu_item_variants
                WHERE menu_item_id = %s AND variant_id = %s
            """, (r['menu_item_id'], r['variant_id']))
            miv_exists = cursor.fetchone()[0] > 0
            
            if menu_item and variant and miv_exists:
                verified += 1
            else:
                failed += 1
                print(f"\n❌ Verification failed for: {r['raw_name']}")
                print(f"   menu_item_id: {r['menu_item_id']} → {menu_item if menu_item else 'NOT FOUND'}")
                print(f"   variant_id: {r['variant_id']} → {variant if variant else 'NOT FOUND'}")
                print(f"   menu_item_variant exists: {miv_exists}")
    
    print(f"\n✅ Verified: {verified} matches")
    if failed > 0:
        print(f"❌ Failed: {failed} matches")
    else:
        print(f"✅ All matches verified in database!")


def test_addon_matching(matcher):
    """Test addon matching"""
    print("\n" + "=" * 80)
    print("TESTING ADDON MATCHING")
    print("=" * 80)
    
    addons = [
        "Cup",
        "Waffle Cone",
        "Butter Waffle Cone (2pcs)",
        "Takeaway Cup",
    ]
    
    for addon in addons:
        result = matcher.match_addon(addon)
        print(f"\n{addon}")
        print(f"  → menu_item_id: {result['menu_item_id']}, variant_id: {result['variant_id']}")
        print(f"  → Confidence: {result['match_confidence']}%, Method: {result['match_method']}")


def main():
    parser = argparse.ArgumentParser(description='Test item matching logic')
    parser.add_argument('--db-url', help='PostgreSQL connection URL')
    parser.add_argument('--host', default='localhost', help='Database host')
    parser.add_argument('--port', type=int, default=5432, help='Database port')
    parser.add_argument('--database', help='Database name')
    parser.add_argument('--user', help='Database user')
    parser.add_argument('--password', help='Database password')
    args = parser.parse_args()
    
    if not PSYCOPG2_AVAILABLE:
        return
    
    # Connect to database
    print("=" * 80)
    print("Connecting to Database")
    print("=" * 80)
    
    try:
        if args.db_url:
            conn = psycopg2.connect(args.db_url)
            print(f"✓ Connected using connection URL")
        else:
            if not args.database or not args.user:
                print("❌ ERROR: --database and --user are required if not using --db-url")
                return
            conn = psycopg2.connect(
                host=args.host,
                port=args.port,
                database=args.database,
                user=args.user,
                password=args.password
            )
            print(f"✓ Connected to {args.host}:{args.port}/{args.database}")
    except Exception as e:
        print(f"❌ ERROR: Failed to connect: {e}")
        return
    
    # Create matcher
    print("\nCreating ItemMatcher...")
    matcher = ItemMatcher(conn)
    print("✓ ItemMatcher created")
    
    # Get test items
    test_items = get_sample_order_items()
    print(f"\nTesting with {len(test_items)} sample order items...")
    
    # Test matching
    results = test_matching(matcher, test_items)
    
    # Display results
    display_results(results)
    
    # Verify matches in database
    verify_matches_in_db(conn, results)
    
    # Test addon matching
    test_addon_matching(matcher)
    
    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)
    
    total = len(results)
    matched = len([r for r in results if r['menu_item_id'] is not None])
    exact = len([r for r in results if r['match_method'] == 'exact'])
    fuzzy = len([r for r in results if r['match_method'] == 'fuzzy'])
    partial = len([r for r in results if r['match_method'] == 'partial'])
    unmatched = len([r for r in results if r['menu_item_id'] is None])
    
    print(f"Total items tested: {total}")
    print(f"Successfully matched: {matched} ({matched/total*100:.1f}%)")
    print(f"  - Exact matches: {exact}")
    print(f"  - Fuzzy matches: {fuzzy}")
    print(f"  - Partial matches: {partial}")
    print(f"Unmatched: {unmatched} ({unmatched/total*100:.1f}%)")
    
    if unmatched > 0:
        print(f"\n⚠️  {unmatched} items need manual review or menu updates")
    else:
        print(f"\n✅ All items matched successfully!")
    
    conn.close()
    
    print("\n" + "=" * 80)
    print("✅ TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()

