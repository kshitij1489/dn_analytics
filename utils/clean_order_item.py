"""
Reusable Order Item Cleaning Module

This module provides functions to clean and normalize order item names
for both initial menu creation and real-time order processing.

It consolidates logic from the original rebuild_menu.py (comprehensive rules)
and recent fixes (Belgium mapping, etc.).

Usage:
    from clean_order_item import clean_order_item_name
    
    result = clean_order_item_name("Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))")
    # Returns: {'name': 'Old Fashion Vanilla Ice Cream', 'type': 'Ice Cream', 'variant': 'REGULAR_TUB_300ML'}
"""

import re
from typing import Dict, List, Set, Tuple

# ============================================================
# CONSTANTS (Ported from rebuild_menu.py)
# ============================================================

# Drinks
DRINKS_SET = {
    'Americano',
    'Cappuccino',
    'Hot Chocolate',
    'Water Bottle',
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
    'Hot Chocolate Fudge Sauce',
]

# Desserts
DESSERTS_PATTERNS = [
    'Affogato',
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
    """Fix common typos (Consolidated from rebuild_menu.py + recent fixes)"""
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
    name = re.sub(r'\bBean[- ]to[- ]bar\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
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
    
    # Fix Eggles -> Eggless
    name = re.sub(r'\bEggles\b', 'Eggless', name)
    
    # --- Recent Fixes not originally in rebuild_menu.py ---
    
    # Fix "Belgium" -> "Bean-to-Bar"
    name = re.sub(r'\bBelgium\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
    
    # Remove redundant (Ice Cream) e.g. "Alphonso (Ice Cream)"
    name = re.sub(r'\s*\(Ice Cream\)', '', name, flags=re.IGNORECASE)
    
    # Fix double "Ice Cream Ice Cream"
    name = re.sub(r'\bIce Cream Ice Cream\b', 'Ice Cream', name)
    
    # Remove trailing parenthesis if incomplete (like "Mini Tub" without closing)
    name = re.sub(r'\s*\([^)]*$', '', name)

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
    
    if '200+200+200' in name_lower or '200ml+200ml+200ml' in name_lower:
        name = re.sub(r'\s*\(200\+200\+200[^)]*\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(200[ml\s]*\+200[ml\s]*\+200[ml\s]*\)', '', name, flags=re.IGNORECASE)
        return name.strip(), 'FAMILY_PACK_3X200ML'
    
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
    
    # Small Scoop patterns
    if 'small scoop' in name_lower:
        variant = 'JUNIOR_SCOOP_60GMS'
        name = re.sub(r'\s*Small Scoop', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
        
    # Standalone weight patterns (often in parens or just at end)
    if '160gm' in name_lower:
        variant = 'MINI_TUB_160GMS'
        name = re.sub(r'\s*\(?160gm\)?', '', name, flags=re.IGNORECASE)
        return name.strip(), variant

    if '200ml' in name_lower:
        variant = 'MINI_TUB_200ML'
        name = re.sub(r'\s*\(?200ml\)?', '', name, flags=re.IGNORECASE)
        return name.strip(), variant

    # Default
    return name.strip(), '1_PIECE'


def determine_type(name: str) -> str:
    """Determine item type"""
    name_lower = name.lower()
    
    # Check drinks first (exact match)
    for drink in DRINKS_SET:
        if name == drink:
            return 'Drinks'
        if drink.lower() == name_lower:
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
    """Final name normalization (consolidation of rules)"""
    # Remove (eggless) from name if it appears in parentheses at end (standardizes 'Name (Eggless)')
    # But sometimes it's 'Eggless Name'. 
    # This rule was in rebuild_menu to fix 'Dates & Chocolate (Eggless)' -> 'Dates & Chocolate Eggless'
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
    
    # Remove trailing separators (hyphens, commas)
    name = re.sub(r'\s*[-,\.]+\s*$', '', name)
    
    # Clean up extra whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    
    return name


def clean_order_item_name(raw_name: str) -> Dict[str, str]:
    """
    Main entry point to clean and normalize an order item name.
    """
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
    
    # Handle special variants for specific types (Logic from rebuild_menu.py 'process_item')
    if item_type == 'Drinks':
        variant = '1_PIECE'
    
    if item_type == 'Combo' and variant == '1_PIECE':
        if 'Duo' in name:
            variant = 'DUO_200ML_200ML'
        elif 'Family Pack Of 3' in name:
            variant = 'FAMILY_PACK_3X200ML'
        elif 'Half In Half' in name:
            variant = 'HALF_IN_HALF_REGULAR_SCOOP'
    
    return {
        'name': name,
        'type': item_type,
        'variant': variant,
    }

if __name__ == "__main__":
    test_cases = [
        "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))",
        "Employee Dessert ( Any 1 )",
        "Eggless Chocolate Overload (Regular Scoop)",
        "Fig Orange Ice Cream (Regular Tub (300ml))",
        "Boston Cream Pie Dessert(2pcs)",
        "Waffle Cone",
        "Hot Chocolate",
        "Belgium 70% Dark Chocolate Ice Cream (Mini Tub)",
        "Alphonso Mango Ice Cream (Ice Cream) - Small Scoop"
    ]
    
    print("Testing clean_order_item_name function:")
    print("=" * 80)
    for test in test_cases:
        result = clean_order_item_name(test)
        print(f"\nInput:  {test}")
        print(f"Output: {result}")
