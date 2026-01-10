"""
Exhaustive Menu Validation Script

Fetches ALL orders from the API and extracts every unique item name
(both order items and addons) to validate against cleaned_menu.csv.

This ensures the menu is truly exhaustive before proceeding to database design.
"""

import requests
import json
import csv
import re
import os
from typing import Dict, List, Set, Tuple, Optional
from collections import Counter

# ============================================================
# API CONFIGURATION
# ============================================================

BASE_URL = "https://webhooks.db1-prod-dachnona.store/analytics"
API_KEY = "f3e1753aa4c44159fa7218a31cd8db1e"

HEADERS = {
    "X-API-Key": API_KEY,
}

# ============================================================
# FETCH FUNCTIONS
# ============================================================

def fetch_all_orders(limit: int = 500) -> List[Dict]:
    """Fetch ALL orders from the API with pagination"""
    results = []
    last_stream_id = 0
    
    print("Fetching all orders from API...")
    
    while True:
        params = {
            "limit": limit,
            "cursor": last_stream_id,
        }
        
        try:
            resp = requests.get(
                f"{BASE_URL}/orders/",
                headers=HEADERS,
                params=params,
                timeout=60,
            )
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("data", [])
            
            if not batch:
                break
            
            results.extend(batch)
            last_stream_id = batch[-1]["stream_id"]
            print(f"  Fetched {len(results)} orders so far... (last_stream_id: {last_stream_id})")
            
            if len(batch) < limit:
                break
                
        except Exception as e:
            print(f"Error fetching orders: {e}")
            break
    
    print(f"Total orders fetched: {len(results)}")
    return results


# ============================================================
# EXTRACTION FUNCTIONS
# ============================================================

def extract_all_items_from_orders(orders: List[Dict]) -> Tuple[Counter, Counter]:
    """
    Extract ALL unique item names from orders.
    
    Returns:
        Tuple of (order_items_counter, addons_counter)
    """
    order_items = Counter()
    addons = Counter()
    
    for order in orders:
        try:
            raw_payload = order.get('raw_event', {}).get('raw_payload', {})
            props = raw_payload.get('properties', {})
            
            # Process ALL order items (not just [0])
            for item in props.get('OrderItem', []):
                name = item.get('name', '').strip()
                if name:
                    order_items[name] += 1
                
                # Process ALL addons for each item
                for addon in item.get('addon', []):
                    addon_name = addon.get('name', '').strip()
                    if addon_name:
                        addons[addon_name] += 1
                        
        except Exception as e:
            # Silently skip malformed orders
            continue
    
    return order_items, addons


def normalize_for_matching(name: str) -> str:
    """Normalize name for fuzzy matching"""
    # Fix HTML entities
    name = name.replace('&amp;', '&')
    # Normalize whitespace
    name = re.sub(r'\s+', ' ', name.strip())
    return name


def extract_base_name(item_name: str) -> str:
    """
    Extract base name by removing variant/size info.
    
    Example:
        "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))" 
        -> "Old Fashion Vanilla Ice Cream"
    """
    name = normalize_for_matching(item_name)
    
    # Remove variant patterns
    patterns = [
        r'\s*\(Perfect Plenty[^)]*\)',
        r'\s*\(Family Tub[^)]*\)',
        r'\s*\(Family Feast[^)]*\)',
        r'\s*\(Regular Tub[^)]*\)',
        r'\s*\(Mini Tub[^)]*\)',
        r'\s*\(Mini tub[^)]*\)',
        r'\s*\(Regular Scoop[^)]*\)',
        r'\s*\(Junior Scoop[^)]*\)',
        r'\s*\(Scoop\)',
        r'\s*\(Regular\)',
        r'\s*\(160gm[s]?\)',
        r'\s*\(200ml\)',
        r'\s*\(300ml\)',
        r'\s*\(220gm[s]?\)',
        r'\s*\(500gm[s]?\)',
        r'\s*\(725ml\)',
        r'\s*\(700ml\)',
        r'\s*\(1pc[s]?\)',
        r'\s*\(2pc[s]?\)',
        r'\s*\(navratri\)',
        r'\s*Small Scoop',
        r'\s*200ml$',
        r'\s*Dessert$',  # "Boston Cream Pie Dessert(2pcs)" -> "Boston Cream Pie"
    ]
    
    for pattern in patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)
    
    # Remove nested parentheses patterns
    name = re.sub(r'\s*\([^)]+\s*\([^)]+\)\)', '', name)
    
    return name.strip()


def fix_common_typos(name: str) -> str:
    """Fix common typos for matching"""
    # Fig Orange -> Fig & Orange
    name = re.sub(r'\bFig Orange\b', 'Fig & Orange', name)
    # Chocolate Dark -> Dark Chocolate
    name = re.sub(r'\bChocolate Dark\b', 'Dark Chocolate', name)
    name = re.sub(r'\bChocolate 70% Dark\b', '70% Dark Chocolate', name)
    # Bean-to-bar standardization
    name = re.sub(r'\bBean-to-bar\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
    name = re.sub(r'\bBean To Bar\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
    # Piec -> Pie
    name = re.sub(r'\bPiec\b', 'Pie', name)
    # Vanila -> Vanilla
    name = re.sub(r'\bVanila\b', 'Vanilla', name)
    # Alphanso -> Alphonso
    name = re.sub(r'\bAlphanso\b', 'Alphonso', name)
    # Eggles -> Eggless
    name = re.sub(r'\bEggles\b', 'Eggless', name)
    # D&n -> D&N
    name = re.sub(r'\bD&n\b', 'D&N', name)
    # Factor -> Factory (School Kids Factor Visit)
    name = re.sub(r'\bFactor Visit\b', 'Factory Visit', name)
    # Normalize "contains Alcohol" -> "Contains Alcohol"
    name = re.sub(r'\(contains Alcohol\)', '(Contains Alcohol)', name, flags=re.IGNORECASE)
    name = re.sub(r'\(with Alcohol\)', '(Contains Alcohol)', name, flags=re.IGNORECASE)
    name = re.sub(r'With Alcohol', '(Contains Alcohol)', name)
    # Normalize (eggless) -> Eggless
    name = re.sub(r'\(eggless\)', 'Eggless', name, flags=re.IGNORECASE)
    return name


# ============================================================
# MATCHING FUNCTIONS
# ============================================================

def load_menu_items(filepath: str = "data/cleaned_menu.csv") -> Set[str]:
    """Load unique menu item names from CSV"""
    names = set()
    
    if not os.path.exists(filepath):
        return names
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('name', '').strip()
            if name:
                names.add(name.lower())
    
    return names


def add_ice_cream_suffix(name: str) -> str:
    """Add Ice Cream suffix for known ice cream items"""
    # Items that are ice creams but might not have "Ice Cream" in name
    ice_cream_bases = [
        'Chocolate & Orange (Contains Alcohol)',
        'Orange (Contains Alcohol)',
        'Orange & Biscuits (Contains Alcohol)',
        'Eggless Just Chocolate',
    ]
    
    for base in ice_cream_bases:
        if name == base or name.startswith(base + ' '):
            if 'Ice Cream' not in name:
                return base + ' Ice Cream'
    
    return name


def find_missing_items(
    items: Counter,
    menu_names: Set[str]
) -> List[Dict]:
    """Find items not matching any menu entry"""
    missing = []
    
    for raw_name, count in items.items():
        # Normalize and fix typos
        name = normalize_for_matching(raw_name)
        name = fix_common_typos(name)
        base_name = extract_base_name(name)
        base_name = fix_common_typos(base_name)
        
        # Try adding Ice Cream suffix
        base_with_suffix = add_ice_cream_suffix(base_name)
        
        # Check various matching strategies
        matched = False
        
        # 1. Exact match (normalized)
        if name.lower() in menu_names or base_name.lower() in menu_names:
            matched = True
        
        # 2. With Ice Cream suffix
        if not matched and base_with_suffix.lower() in menu_names:
            matched = True
        
        # 3. Base name is contained in menu item
        if not matched:
            for menu_name in menu_names:
                if base_name.lower() in menu_name or menu_name in base_name.lower():
                    matched = True
                    break
        
        # 4. Menu item is contained in raw name
        if not matched:
            for menu_name in menu_names:
                if menu_name in name.lower():
                    matched = True
                    break
        
        # 5. Try removing "Ice Cream" and comparing
        if not matched:
            base_no_ic = base_name.lower().replace(' ice cream', '')
            for menu_name in menu_names:
                menu_no_ic = menu_name.replace(' ice cream', '')
                if base_no_ic == menu_no_ic or base_no_ic in menu_no_ic or menu_no_ic in base_no_ic:
                    matched = True
                    break
        
        if not matched:
            missing.append({
                'raw_name': raw_name,
                'normalized': name,
                'base_name': base_name,
                'count': count,
            })
    
    return sorted(missing, key=lambda x: -x['count'])


# ============================================================
# REPORTING
# ============================================================

def generate_report(
    order_items_missing: List[Dict],
    addons_missing: List[Dict],
    total_order_items: int,
    total_addons: int,
    total_menu_items: int,
    output_file: str = "docs/exhaustive_menu_report.md"
) -> str:
    """Generate a detailed markdown report"""
    
    lines = [
        "# Exhaustive Menu Validation Report\n",
        f"**Total Orders Processed:** (see console output)",
        f"**Unique Order Items Found:** {total_order_items}",
        f"**Unique Addons Found:** {total_addons}",
        f"**Menu Items in cleaned_menu.csv:** {total_menu_items}",
        "",
        f"**Missing Order Items:** {len(order_items_missing)}",
        f"**Missing Addons:** {len(addons_missing)}",
        "",
    ]
    
    if order_items_missing:
        lines.append("## Missing Order Items\n")
        lines.append("| Raw Name | Base Name | Count |")
        lines.append("|----------|-----------|-------|")
        for item in order_items_missing[:50]:  # Top 50
            raw = item['raw_name'].replace('|', '\\|')
            base = item['base_name'].replace('|', '\\|')
            lines.append(f"| `{raw}` | `{base}` | {item['count']} |")
        if len(order_items_missing) > 50:
            lines.append(f"\n*...and {len(order_items_missing) - 50} more*\n")
        lines.append("")
    
    if addons_missing:
        lines.append("## Missing Addons\n")
        lines.append("| Raw Name | Base Name | Count |")
        lines.append("|----------|-----------|-------|")
        for item in addons_missing[:50]:  # Top 50
            raw = item['raw_name'].replace('|', '\\|')
            base = item['base_name'].replace('|', '\\|')
            lines.append(f"| `{raw}` | `{base}` | {item['count']} |")
        if len(addons_missing) > 50:
            lines.append(f"\n*...and {len(addons_missing) - 50} more*\n")
        lines.append("")
    
    if not order_items_missing and not addons_missing:
        lines.append("## ✅ All Items Matched!\n")
        lines.append("The menu is exhaustive. All order items and addons have matching entries.\n")
    
    report = "\n".join(lines)
    
    # Save report
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report)
    
    return report


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("EXHAUSTIVE MENU VALIDATION")
    print("=" * 80)
    
    # Step 1: Fetch all orders
    print("\n1. Fetching orders from API...")
    orders = fetch_all_orders()
    
    if not orders:
        print("No orders fetched. Exiting.")
        return
    
    # Step 2: Extract all items
    print("\n2. Extracting all item names...")
    order_items, addons = extract_all_items_from_orders(orders)
    print(f"   Found {len(order_items)} unique order items")
    print(f"   Found {len(addons)} unique addons")
    
    # Step 3: Load menu
    print("\n3. Loading cleaned_menu.csv...")
    menu_names = load_menu_items()
    print(f"   Loaded {len(menu_names)} unique menu item names")
    
    # Step 4: Find missing items
    print("\n4. Finding missing items...")
    order_items_missing = find_missing_items(order_items, menu_names)
    addons_missing = find_missing_items(addons, menu_names)
    
    print(f"   Missing order items: {len(order_items_missing)}")
    print(f"   Missing addons: {len(addons_missing)}")
    
    # Step 5: Generate report
    print("\n5. Generating report...")
    report = generate_report(
        order_items_missing,
        addons_missing,
        len(order_items),
        len(addons),
        len(menu_names),
    )
    print(f"   Report saved to: docs/exhaustive_menu_report.md")
    
    # Step 6: Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total orders processed: {len(orders)}")
    print(f"Unique order items: {len(order_items)}")
    print(f"Unique addons: {len(addons)}")
    print(f"Menu items: {len(menu_names)}")
    print(f"\nMissing order items: {len(order_items_missing)}")
    print(f"Missing addons: {len(addons_missing)}")
    
    if order_items_missing:
        print("\nTop 10 Missing Order Items:")
        for item in order_items_missing[:10]:
            print(f"  - {item['raw_name']} ({item['count']} occurrences)")
    
    if addons_missing:
        print("\nTop 10 Missing Addons:")
        for item in addons_missing[:10]:
            print(f"  - {item['raw_name']} ({item['count']} occurrences)")
    
    # Save raw data for analysis
    print("\n6. Saving extracted data for analysis...")
    
    # Save all unique item names
    with open('docs/all_order_item_names.txt', 'w', encoding='utf-8') as f:
        for name, count in order_items.most_common():
            f.write(f"{count}\t{name}\n")
    
    with open('docs/all_addon_names.txt', 'w', encoding='utf-8') as f:
        for name, count in addons.most_common():
            f.write(f"{count}\t{name}\n")
    
    print("   Saved: docs/all_order_item_names.txt")
    print("   Saved: docs/all_addon_names.txt")
    
    if not order_items_missing and not addons_missing:
        print("\n✅ SUCCESS: Menu is exhaustive!")
    else:
        print(f"\n⚠️  ACTION NEEDED: {len(order_items_missing) + len(addons_missing)} items need to be added to menu")


if __name__ == "__main__":
    main()

