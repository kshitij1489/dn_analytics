"""
Task 2: Analyze PetPooja Order Payloads and Design Database Schema

This script analyzes the order payload structure and generates schema documentation.
"""

import json
import os
from collections import defaultdict, Counter
from typing import Dict, List, Any, Set
from datetime import datetime
import pandas as pd

def load_sample_orders(filepath: str = "sample_payloads/sample_orders_100.json") -> List[Dict]:
    """Load sample orders from JSON file"""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        print("Please run fetch_orders.py first to get sample data")
        return []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_order_fields(order: Dict) -> Dict[str, Any]:
    """Extract all fields from an order record"""
    try:
        raw_payload = order.get('raw_event', {}).get('raw_payload', {})
        props = raw_payload.get('properties', {})
        
        return {
            'stream_id': order.get('stream_id'),
            'event_id': order.get('event_id'),
            'aggregate_id': order.get('aggregate_id'),
            'occurred_at': order.get('occurred_at'),
            'order': props.get('Order', {}),
            'customer': props.get('Customer', {}),
            'order_items': props.get('OrderItem', []),
            'taxes': props.get('Tax', []),
            'discounts': props.get('Discount', []),
            'restaurant': props.get('Restaurant', {}),
        }
    except Exception as e:
        print(f"Error extracting fields: {e}")
        return {}


def analyze_order_structure(orders: List[Dict]) -> Dict[str, Any]:
    """Analyze the structure of orders"""
    analysis = {
        'total_orders': len(orders),
        'order_fields': defaultdict(set),
        'order_types': Counter(),
        'order_sources': Counter(),
        'payment_types': Counter(),
        'order_statuses': Counter(),
        'item_fields': defaultdict(set),
        'addon_fields': defaultdict(set),
        'tax_fields': defaultdict(set),
        'discount_fields': defaultdict(set),
        'customer_fields': defaultdict(set),
        'sample_item_names': [],
        'sample_addon_names': [],
    }
    
    for order in orders:
        fields = extract_order_fields(order)
        
        # Order level
        order_data = fields.get('order', {})
        for key, value in order_data.items():
            analysis['order_fields'][key].add(type(value).__name__)
            if key == 'order_type':
                analysis['order_types'][value] += 1
            elif key == 'order_from':
                analysis['order_sources'][value] += 1
            elif key == 'payment_type':
                analysis['payment_types'][value] += 1
            elif key == 'status':
                analysis['order_statuses'][value] += 1
        
        # Customer level
        customer_data = fields.get('customer', {})
        for key, value in customer_data.items():
            analysis['customer_fields'][key].add(type(value).__name__)
        
        # Order Items
        items = fields.get('order_items', [])
        for item in items:
            for key, value in item.items():
                analysis['item_fields'][key].add(type(value).__name__)
                if key == 'name' and len(analysis['sample_item_names']) < 20:
                    analysis['sample_item_names'].append(value)
            
            # Addons
            addons = item.get('addon', [])
            for addon in addons:
                for key, value in addon.items():
                    analysis['addon_fields'][key].add(type(value).__name__)
                    if key == 'name' and len(analysis['sample_addon_names']) < 20:
                        analysis['sample_addon_names'].append(value)
        
        # Taxes
        taxes = fields.get('taxes', [])
        for tax in taxes:
            for key, value in tax.items():
                analysis['tax_fields'][key].add(type(value).__name__)
        
        # Discounts
        discounts = fields.get('discounts', [])
        for discount in discounts:
            for key, value in discount.items():
                analysis['discount_fields'][key].add(type(value).__name__)
    
    return analysis


def generate_schema_documentation(analysis: Dict[str, Any]) -> str:
    """Generate markdown documentation of the schema"""
    doc = []
    doc.append("# PetPooja Order Schema Analysis\n")
    
    doc.append(f"**Total Orders Analyzed:** {analysis['total_orders']}\n")
    
    # Order Types
    doc.append("## Order Types Distribution\n")
    for order_type, count in analysis['order_types'].most_common():
        doc.append(f"- {order_type}: {count}")
    doc.append("")
    
    # Order Sources
    doc.append("## Order Sources Distribution\n")
    for source, count in analysis['order_sources'].most_common():
        doc.append(f"- {source}: {count}")
    doc.append("")
    
    # Order Fields
    doc.append("## Order Fields\n")
    doc.append("| Field | Type(s) | Description |")
    doc.append("|-------|---------|-------------|")
    for field, types in sorted(analysis['order_fields'].items()):
        doc.append(f"| `{field}` | {', '.join(types)} | |")
    doc.append("")
    
    # Customer Fields
    doc.append("## Customer Fields\n")
    doc.append("| Field | Type(s) | Description |")
    doc.append("|-------|---------|-------------|")
    for field, types in sorted(analysis['customer_fields'].items()):
        doc.append(f"| `{field}` | {', '.join(types)} | |")
    doc.append("")
    
    # Order Item Fields
    doc.append("## Order Item Fields\n")
    doc.append("| Field | Type(s) | Description |")
    doc.append("|-------|---------|-------------|")
    for field, types in sorted(analysis['item_fields'].items()):
        doc.append(f"| `{field}` | {', '.join(types)} | |")
    doc.append("")
    
    # Addon Fields
    doc.append("## Addon Fields\n")
    doc.append("| Field | Type(s) | Description |")
    doc.append("|-------|---------|-------------|")
    for field, types in sorted(analysis['addon_fields'].items()):
        doc.append(f"| `{field}` | {', '.join(types)} | |")
    doc.append("")
    
    # Tax Fields
    doc.append("## Tax Fields\n")
    doc.append("| Field | Type(s) | Description |")
    doc.append("|-------|---------|-------------|")
    for field, types in sorted(analysis['tax_fields'].items()):
        doc.append(f"| `{field}` | {', '.join(types)} | |")
    doc.append("")
    
    # Discount Fields
    doc.append("## Discount Fields\n")
    doc.append("| Field | Type(s) | Description |")
    doc.append("|-------|---------|-------------|")
    for field, types in sorted(analysis['discount_fields'].items()):
        doc.append(f"| `{field}` | {', '.join(types)} | |")
    doc.append("")
    
    # Sample Item Names
    doc.append("## Sample Item Names (for menu matching)\n")
    for name in analysis['sample_item_names'][:10]:
        doc.append(f"- `{name}`")
    doc.append("")
    
    # Sample Addon Names
    doc.append("## Sample Addon Names\n")
    for name in analysis['sample_addon_names'][:10]:
        doc.append(f"- `{name}`")
    doc.append("")
    
    return "\n".join(doc)


def analyze_item_name_patterns(orders: List[Dict]) -> Dict[str, Any]:
    """Analyze item name patterns to understand variant structure"""
    item_names = []
    
    for order in orders:
        fields = extract_order_fields(order)
        items = fields.get('order_items', [])
        for item in items:
            item_names.append(item.get('name', ''))
    
    # Find common patterns
    patterns = {
        'with_variant': [],
        'with_size': [],
        'simple_names': [],
    }
    
    for name in item_names:
        if '(' in name and ')' in name:
            patterns['with_variant'].append(name)
        elif any(size in name.lower() for size in ['ml', 'gm', 'gms', 'pcs', 'pc']):
            patterns['with_size'].append(name)
        else:
            patterns['simple_names'].append(name)
    
    return {
        'total_items': len(item_names),
        'unique_items': len(set(item_names)),
        'with_variant_count': len(patterns['with_variant']),
        'with_size_count': len(patterns['with_size']),
        'simple_names_count': len(patterns['simple_names']),
        'sample_variant_names': patterns['with_variant'][:20],
    }


def main():
    """Main analysis function"""
    print("=" * 80)
    print("PetPooja Order Schema Analysis")
    print("=" * 80)
    
    # Load sample orders
    orders = load_sample_orders()
    
    if not orders:
        print("\nNo orders found. Please run fetch_orders.py first.")
        return
    
    print(f"\nLoaded {len(orders)} orders for analysis\n")
    
    # Analyze structure
    print("Analyzing order structure...")
    analysis = analyze_order_structure(orders)
    
    # Analyze item names
    print("Analyzing item name patterns...")
    item_analysis = analyze_item_name_patterns(orders)
    
    # Generate documentation
    print("Generating schema documentation...")
    schema_doc = generate_schema_documentation(analysis)
    
    # Save documentation
    os.makedirs('docs', exist_ok=True)
    with open('docs/schema_analysis.md', 'w', encoding='utf-8') as f:
        f.write(schema_doc)
    
    print(f"\nSchema documentation saved to: docs/schema_analysis.md")
    
    # Print summary
    print("\n" + "=" * 80)
    print("ANALYSIS SUMMARY")
    print("=" * 80)
    print(f"Total Orders: {analysis['total_orders']}")
    print(f"\nOrder Types: {dict(analysis['order_types'])}")
    print(f"\nOrder Sources: {dict(analysis['order_sources'])}")
    print(f"\nOrder Fields: {len(analysis['order_fields'])} unique fields")
    print(f"Customer Fields: {len(analysis['customer_fields'])} unique fields")
    print(f"Order Item Fields: {len(analysis['item_fields'])} unique fields")
    print(f"Addon Fields: {len(analysis['addon_fields'])} unique fields")
    print(f"\nItem Name Analysis:")
    print(f"  Total Items: {item_analysis['total_items']}")
    print(f"  Unique Items: {item_analysis['unique_items']}")
    print(f"  Items with Variants: {item_analysis['with_variant_count']}")
    print(f"  Items with Size Info: {item_analysis['with_size_count']}")


if __name__ == "__main__":
    main()

