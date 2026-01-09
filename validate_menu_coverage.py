"""
Sanity Check: Validate that cleaned_menu.csv covers all items in order data

This script:
1. Extracts all unique item names from order data
2. Compares against cleaned_menu.csv
3. Identifies missing items
4. Suggests additions
"""

import json
import csv
import os
from collections import Counter
from typing import Set, Dict, List
import re

def load_sample_orders(filepath: str = "sample_payloads/sample_orders_100.json") -> List[Dict]:
    """Load sample orders from JSON file"""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_all_item_names(orders: List[Dict]) -> Dict[str, Counter]:
    """Extract all item names from orders (both order items and addons)"""
    order_items = Counter()
    addons = Counter()
    
    for order in orders:
        try:
            raw_payload = order.get('raw_event', {}).get('raw_payload', {})
            props = raw_payload.get('properties', {})
            
            # Order Items
            order_items_list = props.get('OrderItem', [])
            for item in order_items_list:
                name = item.get('name', '').strip()
                if name:
                    order_items[name] += 1
            
            # Addons (nested in OrderItems)
            for item in order_items_list:
                addon_list = item.get('addon', [])
                for addon in addon_list:
                    addon_name = addon.get('name', '').strip()
                    if addon_name:
                        addons[addon_name] += 1
        except Exception as e:
            print(f"Error processing order: {e}")
            continue
    
    return {
        'order_items': order_items,
        'addons': addons,
    }


def load_cleaned_menu(filepath: str = "cleaned_menu.csv") -> List[Dict]:
    """Load cleaned menu CSV"""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return []
    
    menu_items = []
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            menu_items.append(row)
    
    return menu_items


def normalize_name_for_matching(name: str) -> str:
    """Normalize name for fuzzy matching"""
    # Remove extra whitespace
    name = re.sub(r'\s+', ' ', name.strip())
    # Remove HTML entities
    name = name.replace('&amp;', '&')
    return name


def find_missing_items(
    order_items: Counter,
    addons: Counter,
    cleaned_menu: List[Dict]
) -> Dict[str, List[Dict]]:
    """Find items in orders that are not in cleaned_menu.csv"""
    
    # Get all normalized names from cleaned_menu
    menu_names = set()
    if cleaned_menu:
        for row in cleaned_menu:
            menu_name = normalize_name_for_matching(row['name'])
            menu_names.add(menu_name)
    
    missing_order_items = []
    missing_addons = []
    
    # Check order items
    for item_name, count in order_items.items():
        normalized = normalize_name_for_matching(item_name)
        # Check if it matches any menu item (exact or contains)
        found = False
        for menu_name in menu_names:
            if normalized.lower() == menu_name.lower():
                found = True
                break
            # Also check if menu name is contained in order item name
            # (e.g., "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))" contains "Old Fashion Vanilla Ice Cream")
            if menu_name.lower() in normalized.lower():
                found = True
                break
        
        if not found:
            missing_order_items.append({
                'name': item_name,
                'count': count,
                'normalized': normalized
            })
    
    # Check addons
    for addon_name, count in addons.items():
        normalized = normalize_name_for_matching(addon_name)
        found = False
        for menu_name in menu_names:
            if normalized.lower() == menu_name.lower():
                found = True
                break
            if menu_name.lower() in normalized.lower():
                found = True
                break
        
        if not found:
            missing_addons.append({
                'name': addon_name,
                'count': count,
                'normalized': normalized
            })
    
    return {
        'missing_order_items': sorted(missing_order_items, key=lambda x: x['count'], reverse=True),
        'missing_addons': sorted(missing_addons, key=lambda x: x['count'], reverse=True),
    }


def suggest_item_type(name: str) -> str:
    """Suggest item type based on name patterns"""
    name_lower = name.lower()
    
    # Extras
    if any(word in name_lower for word in ['cup', 'cone', 'waffle', 'takeaway']):
        return 'Extra'
    
    # Desserts
    if any(word in name_lower for word in ['dessert', 'pie', 'brownie', 'cheesecake', 'tiramisu', 'lamington']):
        return 'Dessert'
    
    # Combo
    if any(word in name_lower for word in ['combo', 'pack', 'duo', 'trio']):
        return 'Combo'
    
    # Drinks (if any)
    if any(word in name_lower for word in ['coffee', 'tea', 'americano', 'cappuccino', 'latte']):
        return 'Drinks'
    
    # Default to Ice Cream
    return 'Ice Cream'


def suggest_variant(name: str) -> str:
    """Suggest variant based on name patterns"""
    name_lower = name.lower()
    
    # Check for size patterns
    if '(300ml)' in name_lower or '300ml' in name_lower:
        return 'REGULAR_TUB_300ML'
    if '(200ml)' in name_lower or '200ml' in name_lower:
        return 'MINI_TUB_200ML'
    if '(160gm' in name_lower or '160gms' in name_lower:
        return 'MINI_TUB_160GMS'
    if '(220gm' in name_lower or '220gms' in name_lower:
        return 'REGULAR_TUB_220GMS'
    if '(500gm' in name_lower or '500gms' in name_lower:
        return 'FAMILY_TUB_500GMS'
    if '(120gm' in name_lower or '120gms' in name_lower or 'regular scoop' in name_lower:
        return 'REGULAR_SCOOP_120GMS'
    if '(60gm' in name_lower or '60gms' in name_lower or 'junior scoop' in name_lower or 'small scoop' in name_lower:
        return 'JUNIOR_SCOOP_60GMS'
    
    # Check for piece counts
    if '(2pc' in name_lower or '(2 pc' in name_lower:
        return '2_PIECES'
    if '(1pc' in name_lower or '(1 pc' in name_lower or 'any 1' in name_lower:
        return '1_PIECE'
    
    # Special patterns
    if 'perfect plenty' in name_lower:
        if '300ml' in name_lower:
            return 'REGULAR_TUB_300ML'
        if '200ml' in name_lower:
            return 'MINI_TUB_200ML'
        if '200gms' in name_lower or '200gm' in name_lower:
            return 'PERFECT_PLENTY_200GMS'
    
    # Default
    return '1_PIECE'


def generate_missing_items_report(missing: Dict[str, List[Dict]]) -> str:
    """Generate a report of missing items with suggestions"""
    report = []
    report.append("# Missing Items Report\n")
    report.append("Items found in order data but not in cleaned_menu.csv\n")
    
    # Missing Order Items
    report.append("## Missing Order Items\n")
    report.append("| Name | Count | Suggested Type | Suggested Variant |")
    report.append("|------|-------|----------------|-------------------|")
    
    for item in missing['missing_order_items']:
        name = item['name']
        count = item['count']
        item_type = suggest_item_type(name)
        variant = suggest_variant(name)
        report.append(f"| `{name}` | {count} | {item_type} | {variant} |")
    
    report.append("")
    
    # Missing Addons
    report.append("## Missing Addons\n")
    report.append("| Name | Count | Suggested Type | Suggested Variant |")
    report.append("|------|-------|----------------|-------------------|")
    
    for item in missing['missing_addons']:
        name = item['name']
        count = item['count']
        item_type = suggest_item_type(name)
        variant = suggest_variant(name)
        report.append(f"| `{name}` | {count} | {item_type} | {variant} |")
    
    report.append("")
    
    return "\n".join(report)


def main():
    """Main validation function"""
    print("=" * 80)
    print("Menu Coverage Validation")
    print("=" * 80)
    
    # Load data
    print("\n1. Loading sample orders...")
    orders = load_sample_orders()
    if not orders:
        print("No orders found. Please run fetch_orders.py first.")
        return
    
    print(f"   Loaded {len(orders)} orders")
    
    print("\n2. Extracting item names from orders...")
    item_data = extract_all_item_names(orders)
    print(f"   Found {len(item_data['order_items'])} unique order items")
    print(f"   Found {len(item_data['addons'])} unique addons")
    
    print("\n3. Loading cleaned_menu.csv...")
    cleaned_menu = load_cleaned_menu()
    if not cleaned_menu:
        print("   cleaned_menu.csv not found or empty")
    else:
        print(f"   Loaded {len(cleaned_menu)} menu items")
    
    print("\n4. Finding missing items...")
    missing = find_missing_items(
        item_data['order_items'],
        item_data['addons'],
        cleaned_menu
    )
    
    print(f"   Found {len(missing['missing_order_items'])} missing order items")
    print(f"   Found {len(missing['missing_addons'])} missing addons")
    
    # Generate report
    print("\n5. Generating report...")
    report = generate_missing_items_report(missing)
    
    # Save report
    os.makedirs('docs', exist_ok=True)
    with open('docs/missing_items_report.md', 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"   Report saved to: docs/missing_items_report.md")
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total unique order items in data: {len(item_data['order_items'])}")
    print(f"Total unique addons in data: {len(item_data['addons'])}")
    print(f"Items in cleaned_menu.csv: {len(cleaned_menu)}")
    print(f"\nMissing order items: {len(missing['missing_order_items'])}")
    print(f"Missing addons: {len(missing['missing_addons'])}")
    
    if missing['missing_order_items']:
        print("\nTop 10 Missing Order Items:")
        for item in missing['missing_order_items'][:10]:
            print(f"  - {item['name']} (appears {item['count']} times)")
    
    if missing['missing_addons']:
        print("\nTop 10 Missing Addons:")
        for item in missing['missing_addons'][:10]:
            print(f"  - {item['name']} (appears {item['count']} times)")


if __name__ == "__main__":
    main()

