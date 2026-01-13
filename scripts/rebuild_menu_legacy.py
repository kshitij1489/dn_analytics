"""
Rebuild item_parsing_table.csv from full_menu.txt (Legacy Logic)

This script processes all items from full_menu.txt (or API history) using the 
CURRENT (Legacy) regex logic and saves the mapping to data/item_parsing_table.csv.
This serves as the initial seed for the Version 2 parsing system.
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
    print("Generating item_parsing_table.csv (Legacy Seed)")
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
    parse_entries = []
    seen_raw = set()
    
    for raw_name in items:
        if not raw_name or raw_name in seen_raw:
            continue
        
        seen_raw.add(raw_name)
        
        # USE THE SHARED LOGIC
        result = clean_order_item_name(raw_name)
        
        parse_entries.append({
            'raw_name': raw_name,
            'cleaned_name': result['name'],
            'type': result['type'],
            'variant': result['variant'],
            'is_verified': True  # Mark legacy items as verified
        })
    
    print(f"Processed {len(parse_entries)} unique raw items")
    
    # Sort by raw_name
    parse_entries.sort(key=lambda x: x['raw_name'])
    
    output_path = 'data/item_parsing_table.csv'
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['raw_name', 'cleaned_name', 'type', 'variant', 'is_verified']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for entry in parse_entries:
            writer.writerow(entry)
    
    print(f"\nSaved to {output_path}")
    
    # Also save the old format for backward compatibility check during transition
    clean_unique = {}
    for entry in parse_entries:
        key = (entry['cleaned_name'], entry['type'], entry['variant'])
        clean_unique[key] = entry
        
    print(f"Derived {len(clean_unique)} unique canonical items")


if __name__ == "__main__":
    main()
