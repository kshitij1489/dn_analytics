"""
Rebuild cleaned_menu.csv from full_menu.txt

This script processes all items from full_menu.txt (or API history) and generates
a comprehensive cleaned_menu.csv with all items, types, and variants.

REFACTORED: Now uses clean_order_item.py as the single source of truth for 
cleaning logic.
"""

import csv
import re
from typing import Dict, List, Set, Tuple
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.api_client import fetch_stream_raw
from data_cleaning.clean_order_item import clean_order_item_name

def load_full_menu(filepath: str) -> List[str]:
    """Load items from full_menu.txt"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Warning: {filepath} not found.")
        return []
    
    # Parse the set-like format - items are between single or double quotes
    items = []
    lines = content.split('\n')
    for line in lines:
        line = line.strip()
        # Skip empty lines and pure braces
        if not line or line in ['{', '}']:
            continue
        
        # Handle first line with opening brace: {'400 Pidge/porter...',
        if line.startswith("{'"):
            line = line[1:]  # Remove opening brace
        
        # Handle last line with closing brace: 'Water Bottle'}
        if line.endswith("'}"):
            line = line[:-1]  # Remove closing brace
        
        # Try double quotes first (for items with apostrophes)
        match = re.match(r'[\s]*"(.+)"[,]?$', line)
        if match:
            items.append(match.group(1))
            continue
        
        # Then try single quotes
        # Handle items that end with ', (trailing comma)
        if line.endswith("',"):
            line = line[:-2]
        if line.endswith("'"):
            line = line[:-1]
        if line.startswith("'"):
            line = line[1:]
        
        if line and not line.startswith('{'):
            items.append(line)
    
    return items


def main():
    print("=" * 80)
    print("Rebuilding cleaned_menu.csv")
    print("=" * 80)
    
    # Load items from full_menu.txt if it exists
    items = load_full_menu('full_menu.txt')
    print(f"\nLoaded {len(items)} items from full_menu.txt")

    # Optionally fetch from API if full_menu.txt is empty
    if not items:
        print("Fetching from API...")
        records = fetch_stream_raw("orders")
        for record in records:
            try:
                raw_event = record.get('raw_event', {})
                raw_payload = raw_event.get('raw_payload', {})
                properties = raw_payload.get('properties', {})
                item_lst = properties.get('OrderItem', [])
                for item in item_lst:
                    if item.get('name'):
                        items.append(item.get('name'))
                    for addon in item.get('addon', []):
                        if addon.get('name'):
                            items.append(addon.get('name'))
            except:
                pass
        print(f"Fetched {len(items)} items from API")

    # Process all items using the unified cleaning logic
    processed = []
    for raw_name in items:
        if not raw_name:
            continue
        
        # USE THE SHARED LOGIC
        result = clean_order_item_name(raw_name)
        processed.append(result)
    
    # Deduplicate (name, type, variant) combinations
    seen = set()
    unique = []
    for item in processed:
        key = (item['name'], item['type'], item['variant'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    
    print(f"Processed into {len(unique)} unique (name, type, variant) combinations")
    
    # Sort by name, then type, then variant
    unique.sort(key=lambda x: (x['name'], x['type'], x['variant']))
    
    # Count by type
    type_counts = {}
    for item in unique:
        t = item['type']
        type_counts[t] = type_counts.get(t, 0) + 1
    
    print("\nItems by type:")
    for t, count in sorted(type_counts.items()):
        print(f"  {t}: {count}")
    
    with open('data/cleaned_menu.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['name', 'type', 'variant'])
        writer.writeheader()
        for item in unique:
            writer.writerow(item)
    
    print(f"\nSaved to data/cleaned_menu.csv")
    
    # Show samples
    print("\n" + "=" * 80)
    print("Sample items:")
    print("=" * 80)
    
    # Show drinks
    drinks = [i for i in unique if i['type'] == 'Drinks']
    print("\nDrinks:")
    for d in drinks[:5]:
        print(f"  {d['name']}, {d['type']}, {d['variant']}")
    
    # Show extras
    extras = [i for i in unique if i['type'] == 'Extra']
    print("\nExtras:")
    for e in extras[:5]:
        print(f"  {e['name']}, {e['type']}, {e['variant']}")


if __name__ == "__main__":
    main()
