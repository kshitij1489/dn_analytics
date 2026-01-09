"""
Rebuild cleaned_menu.csv from full_menu.txt

This script processes all items from full_menu.txt and generates
a comprehensive cleaned_menu.csv with all items, types, and variants.
"""

import re
import csv
from typing import Dict, List, Set, Tuple

# ============================================================
# CONSTANTS
# ============================================================

# Drinks
DRINKS = {
    'Affogato',
    'Americano',
    'Cappuccino',
}

# Extras
EXTRAS_PATTERNS = [
    'Cup',
    'Takeaway Cup', 
    'Waffle Cone',
    'Butter Waffle',
    'Delivery Charges',
    'Pidge/Porter',
    'Packaging',
    'Thermocol',
    'Dry Ice',
    'Water Bottle',
    'Hot Chocolate Fudge Sauce',
]

# Desserts
DESSERTS_PATTERNS = [
    'Boston Cream Pie',
    'Brownie',
    'Cheesecake',
    'Lamington',
    'Tiramisu',
    'Plum Cake',
    'Cookie',
    'Tres Leches',
    'Employee Dessert',
    'Cream Cheese Fruit Medley Cake',
    'Ice Cream Cake',
    'Customised Ice Cream Cake',
]

# Combos
COMBOS_PATTERNS = [
    'Duo',
    'Family Pack',
    'Half In Half',
    'Combo',
]

# Services
SERVICES_PATTERNS = [
    'Factory Visit',
    'School Kids',
]

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def fix_html_entities(text: str) -> str:
    """Fix HTML entities"""
    return text.replace('&amp;', '&')


def fix_typos(name: str) -> str:
    """Fix common typos"""
    # Fix Piec -> Pie
    name = re.sub(r'\bPiec\b', 'Pie', name)
    # Fix Vanila -> Vanilla
    name = re.sub(r'\bVanila\b', 'Vanilla', name)
    # Fix Alphanso -> Alphonso
    name = re.sub(r'\bAlphanso\b', 'Alphonso', name)
    # Fix Factor -> Factory (School Kids Factor Visit)
    name = re.sub(r'\bFactor Visit\b', 'Factory Visit', name)
    # Fix Pidge/porter -> Pidge/Porter
    name = re.sub(r'Pidge/porter', 'Pidge/Porter', name)
    # Standardize Bean-to-bar capitalization
    name = re.sub(r'\bBean-to-bar\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
    name = re.sub(r'\bBean To Bar\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
    # Fix "Chocolate Dark" -> "Dark Chocolate"
    name = re.sub(r'\bChocolate Dark\b', 'Dark Chocolate', name)
    name = re.sub(r'\bChocolate 70% Dark\b', '70% Dark Chocolate', name)
    # Fix D&n -> D&N
    name = re.sub(r'\bD&n\b', 'D&N', name)
    # Standardize "contains Alcohol" -> "(Contains Alcohol)"
    name = re.sub(r'\(contains Alcohol\)', '(Contains Alcohol)', name, flags=re.IGNORECASE)
    name = re.sub(r'With Alcohol', '(Contains Alcohol)', name, flags=re.IGNORECASE)
    name = re.sub(r'\(with Alcohol\)', '(Contains Alcohol)', name, flags=re.IGNORECASE)
    # Fix "Fig Orange" -> "Fig & Orange"
    name = re.sub(r'\bFig Orange\b', 'Fig & Orange', name)
    return name


def extract_variant(raw_name: str) -> Tuple[str, str]:
    """Extract variant from raw name and return (clean_name, variant)"""
    name = raw_name
    name_lower = name.lower()
    
    # Special single-item patterns
    if 'any 1' in name_lower:
        return name.strip(), '1_PIECE'
    
    # Combo patterns first
    if '200ml+200ml' in name_lower or '200ml + 200ml' in name_lower:
        name = re.sub(r'\s*\(200ml\s*\+\s*200ml\)', '', name, flags=re.IGNORECASE)
        return name.strip(), 'DUO_200ML_200ML'
    
    if '200+200+200' in name_lower:
        name = re.sub(r'\s*\(200\+200\+200[^)]*\)', '', name, flags=re.IGNORECASE)
        return name.strip(), 'FAMILY_PACK_3X200ML'
    
    # Size patterns with nested parentheses like "(Perfect Plenty (300ml))"
    # Family Feast patterns
    if 'family feast' in name_lower:
        if '725ml' in name_lower:
            variant = 'FAMILY_TUB_725ML'
        elif '700ml' in name_lower:
            variant = 'FAMILY_TUB_700ML'
        elif '550gms' in name_lower or '550gm' in name_lower:
            variant = 'FAMILY_TUB_550GMS'
        else:
            variant = 'FAMILY_TUB_725ML'
        name = re.sub(r'\s*\(Family Feast\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Family Feast\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Family Tub patterns
    if 'family tub' in name_lower:
        if '725ml' in name_lower:
            variant = 'FAMILY_TUB_725ML'
        elif '700ml' in name_lower:
            variant = 'FAMILY_TUB_700ML'
        elif '500gms' in name_lower or '500gm' in name_lower:
            variant = 'FAMILY_TUB_500GMS'
        else:
            variant = 'FAMILY_TUB_500GMS'
        name = re.sub(r'\s*\(Family Tub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Family Tub\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Perfect Plenty patterns
    if 'perfect plenty' in name_lower:
        if '350ml' in name_lower:
            variant = 'PERFECT_PLENTY_350ML'
        elif '325ml' in name_lower:
            variant = 'PERFECT_PLENTY_325ML'
        elif '300ml' in name_lower:
            variant = 'PERFECT_PLENTY_300ML'
        elif '200ml' in name_lower:
            variant = 'PERFECT_PLENTY_200ML'
        elif '200gms' in name_lower or '200gm' in name_lower:
            variant = 'PERFECT_PLENTY_200GMS'
        else:
            variant = 'PERFECT_PLENTY_200GMS'
        name = re.sub(r'\s*\(Perfect Plenty\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Perfect Plenty\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Mini Indulgence patterns
    if 'mini indulgence' in name_lower:
        variant = 'MINI_TUB_200ML'
        name = re.sub(r'\s*\(Mini Indulgence\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Mini Indulgence\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Regular Tub patterns
    if 'regular tub' in name_lower:
        if '300ml' in name_lower:
            variant = 'REGULAR_TUB_300ML'
        elif '220gms' in name_lower or '220gm' in name_lower:
            variant = 'REGULAR_TUB_220GMS'
        else:
            variant = 'REGULAR_TUB_220GMS'
        name = re.sub(r'\s*\(Regular Tub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Regular Tub\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Mini Tub patterns
    if 'mini tub' in name_lower:
        if '200ml' in name_lower:
            variant = 'MINI_TUB_200ML'
        elif '160gms' in name_lower or '160gm' in name_lower:
            variant = 'MINI_TUB_160GMS'
        else:
            variant = 'MINI_TUB_160GMS'
        name = re.sub(r'\s*\(Mini [Tt]ub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Mini [Tt]ub\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Regular Scoop patterns
    if 'regular scoop' in name_lower:
        variant = 'REGULAR_SCOOP_120GMS'
        name = re.sub(r'\s*\(Regular Scoop\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Regular Scoop\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Junior Scoop patterns
    if 'junior scoop' in name_lower:
        variant = 'JUNIOR_SCOOP_60GMS'
        name = re.sub(r'\s*\(Junior Scoop\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Junior Scoop\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Scoop alone
    if re.search(r'\(Scoop\)', name, flags=re.IGNORECASE):
        variant = 'REGULAR_SCOOP_120GMS'
        name = re.sub(r'\s*\(Scoop\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Regular alone (like "Alphonso Mango Ice Cream (Regular)")
    if re.search(r'\(Regular\)$', name, flags=re.IGNORECASE):
        variant = 'REGULAR_SCOOP_120GMS'
        name = re.sub(r'\s*\(Regular\)$', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Piece counts
    if re.search(r'\(2\s*pc[s]?\)', name_lower) or '(2pcs)' in name_lower:
        variant = '2_PIECES'
        name = re.sub(r'\s*\(2\s*pc[s]?\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'Dessert$', '', name).strip()  # Remove "Dessert" suffix
        return name.strip(), variant
    
    if re.search(r'\(1\s*pc[s]?\)', name_lower) or '(1pcs)' in name_lower or '(1pc)' in name_lower:
        variant = '1_PIECE'
        name = re.sub(r'\s*\(1\s*pc[s]?\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'Dessert$', '', name).strip()
        return name.strip(), variant
    
    # Weight patterns standalone
    if re.search(r'\(250gm\)', name_lower):
        variant = '250GMS'
        name = re.sub(r'\s*\(250gm\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    if re.search(r'\(310gm\)', name_lower):
        variant = '310GMS'
        name = re.sub(r'\s*\(310gm\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    if re.search(r'\(325gm\)', name_lower):
        variant = '325GMS'
        name = re.sub(r'\s*\(325gm\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Size patterns like "(400gm)" or "(1kg)"
    if '1kg' in name_lower:
        variant = '1KG'
        name = re.sub(r'\s*1kg', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    if '400gm' in name_lower:
        variant = '400GMS'
        name = re.sub(r'\s*400gm', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Factory visit patterns
    if 'single' in name_lower:
        variant = 'SINGLE'
        name = re.sub(r'\s*\(single\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    if 'family' in name_lower and 'factory visit' in name_lower:
        variant = 'FAMILY'
        name = re.sub(r'\s*\(family\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Remove Navratri tags
    name = re.sub(r'\s*\(navratri\)', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s*\(Navratri\)', '', name, flags=re.IGNORECASE)
    
    # Default
    return name.strip(), '1_PIECE'


def determine_type(name: str) -> str:
    """Determine item type"""
    name_lower = name.lower()
    
    # Check drinks first (exact match)
    for drink in DRINKS:
        if name == drink:
            return 'Drinks'
    
    # Services
    for pattern in SERVICES_PATTERNS:
        if pattern.lower() in name_lower:
            return 'Service'
    
    # Extras
    for pattern in EXTRAS_PATTERNS:
        if pattern.lower() in name_lower:
            return 'Extra'
    
    # Desserts (but not ice cream desserts)
    if 'ice cream' not in name_lower:
        for pattern in DESSERTS_PATTERNS:
            if pattern.lower() in name_lower:
                return 'Dessert'
    
    # Combos
    for pattern in COMBOS_PATTERNS:
        if pattern.lower() in name_lower:
            return 'Combo'
    
    # Default to Ice Cream
    return 'Ice Cream'


def normalize_name(name: str) -> str:
    """Final name normalization"""
    # Remove (eggless) from name if it appears in parentheses at end
    name = re.sub(r'\s*\(eggless\)\s*', ' Eggless ', name, flags=re.IGNORECASE)
    
    # Remove "Dessert" suffix from Boston Cream Pie
    if 'Boston Cream Pie' in name:
        name = re.sub(r'\s*Dessert$', '', name)
    
    # Clean up "Contains Alcohol" positioning
    if '(Contains Alcohol)' in name and 'Ice Cream' in name:
        # Move to correct position
        name = name.replace('(Contains Alcohol) Ice Cream', 'Ice Cream (Contains Alcohol)')
        name = name.replace('Ice Cream (Contains Alcohol)', '(Contains Alcohol) Ice Cream')
    
    # Clean Chocolate & Orange naming
    if 'Chocolate & Orange' in name and 'Contains Alcohol' in name:
        name = 'Chocolate & Orange (Contains Alcohol) Ice Cream'
    
    if 'Orange Ice Cream' in name and 'Alcohol' in name and 'Chocolate' not in name:
        name = 'Orange (Contains Alcohol) Ice Cream'
    
    if 'Orange & Biscuits' in name and 'Alcohol' in name:
        name = 'Orange & Biscuits (Contains Alcohol) Ice Cream'
    
    # Remove round shape description
    name = re.sub(r'\s*-\s*Round Shape', '', name)
    
    # Clean up extra whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    
    return name


def process_item(raw_name: str) -> Dict[str, str]:
    """Process a single item and return cleaned data"""
    # Step 1: Fix HTML entities
    name = fix_html_entities(raw_name)
    
    # Step 2: Fix typos
    name = fix_typos(name)
    
    # Step 3: Extract variant
    name, variant = extract_variant(name)
    
    # Step 4: Determine type
    item_type = determine_type(name)
    
    # Step 5: Normalize name
    name = normalize_name(name)
    
    # Handle special variants for specific types
    if item_type == 'Drinks':
        variant = '1_PIECE'
    
    if item_type == 'Combo' and variant == '1_PIECE':
        if 'Duo' in name:
            variant = 'DUO_200ML_200ML'
        elif 'Family Pack' in name or 'Pack Of 3' in name:
            variant = 'FAMILY_PACK_3X200ML'
        elif 'Half In Half' in name:
            variant = 'HALF_IN_HALF_REGULAR_SCOOP'
    
    return {
        'name': name,
        'type': item_type,
        'variant': variant,
    }


def load_full_menu(filepath: str) -> List[str]:
    """Load items from full_menu.txt"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
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
    print("Rebuilding cleaned_menu.csv from full_menu.txt")
    print("=" * 80)
    
    # Load items
    items = load_full_menu('full_menu.txt')
    print(f"\nLoaded {len(items)} items from full_menu.txt")
    
    # Process all items
    processed = []
    for raw_name in items:
        result = process_item(raw_name)
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
    
    # Write to CSV
    with open('cleaned_menu.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['name', 'type', 'variant'])
        writer.writeheader()
        for item in unique:
            writer.writerow(item)
    
    print(f"\nSaved to cleaned_menu.csv")
    
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
    
    # Show services
    services = [i for i in unique if i['type'] == 'Service']
    print("\nServices:")
    for s in services:
        print(f"  {s['name']}, {s['type']}, {s['variant']}")


if __name__ == "__main__":
    main()

