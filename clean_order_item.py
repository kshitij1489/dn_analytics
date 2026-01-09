"""
Reusable Order Item Cleaning Module

This module provides functions to clean and normalize order item names
for both initial menu creation and real-time order processing.

Usage:
    from clean_order_item import clean_order_item_name
    
    result = clean_order_item_name("Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))")
    # Returns: {'name': 'Old Fashion Vanilla Ice Cream', 'type': 'Ice Cream', 'variant': 'REGULAR_TUB_300ML'}
"""

import re
from typing import Dict, Optional, Tuple

# ============================================================
# TYPO FIXES AND NAME NORMALIZATION
# ============================================================

def fix_html_entities(text: str) -> str:
    """Fix HTML entities like &amp; -> &"""
    return text.replace('&amp;', '&')


def fix_typos(name: str) -> str:
    """Fix common typos in item names"""
    
    # Fix "Eggles" -> "Eggless"
    name = re.sub(r'\bEggles\b', 'Eggless', name)
    
    # Fix Boston Cream Piec -> Boston Cream Pie
    name = re.sub(r'\bBoston Cream Piec\b', 'Boston Cream Pie', name)
    
    # Fix Bean-to-bar capitalization variations
    name = re.sub(r'\bBean[- ]to[- ]bar\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
    name = re.sub(r'\bBean To Bar\b', 'Bean-to-Bar', name, flags=re.IGNORECASE)
    
    # Fix "Chocolate Dark" -> "Dark Chocolate" (word order)
    name = re.sub(r'\bChocolate Dark\b', 'Dark Chocolate', name)
    
    # Fix double "Ice Cream Ice Cream"
    name = re.sub(r'\bIce Cream Ice Cream\b', 'Ice Cream', name)
    
    # Fix D&n -> D&N (D&N Traditional Plum Cake)
    name = re.sub(r'\bD&n\b', 'D&N', name)
    
    # Fix "Fig Orange" -> "Fig & Orange" (missing ampersand)
    name = re.sub(r'\bFig Orange\b', 'Fig & Orange', name)
    
    # Fix "Cherry & Chocolate" without "Fudge" -> add "Fudge"
    if 'Cherry & Chocolate' in name and 'Fudge' not in name:
        name = name.replace('Cherry & Chocolate', 'Cherry & Chocolate Fudge')
    
    # Fix "Chocolate & Orange With Alcohol" -> standardize naming
    if 'Chocolate & Orange With Alcohol' in name:
        name = name.replace('Chocolate & Orange With Alcohol', 'Chocolate & Orange (Contains Alcohol) Ice Cream')
        name = name.replace('Ice Cream Ice Cream', 'Ice Cream')
    
    # Remove trailing parenthesis if incomplete (like "Mini Tub" without closing)
    name = re.sub(r'\s*\([^)]*$', '', name)
    
    return name.strip()


def extract_variant_from_name(name: str) -> Tuple[str, str]:
    """
    Extract variant from name and return (clean_name, variant)
    
    Examples:
        "Banoffee Ice Cream (160gm)" -> ("Banoffee Ice Cream", "MINI_TUB_160GMS")
        "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))" -> ("Old Fashion Vanilla Ice Cream", "REGULAR_TUB_300ML")
    """
    name_lower = name.lower()
    original_name = name
    
    # Check for explicit variant patterns
    
    # (160gm) or (160gms) -> MINI_TUB_160GMS
    if re.search(r'\(160gm[s]?\)', name_lower):
        variant = 'MINI_TUB_160GMS'
        name = re.sub(r'\s*\(160gm[s]?\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # (200ml) -> MINI_TUB_200ML
    if re.search(r'\(200ml\)', name_lower):
        variant = 'MINI_TUB_200ML'
        name = re.sub(r'\s*\(200ml\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # (300ml) -> REGULAR_TUB_300ML
    if re.search(r'\(300ml\)', name_lower):
        variant = 'REGULAR_TUB_300ML'
        name = re.sub(r'\s*\(300ml\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # (220gms) or (220gm) -> REGULAR_TUB_220GMS
    if re.search(r'\(220gm[s]?\)', name_lower):
        variant = 'REGULAR_TUB_220GMS'
        name = re.sub(r'\s*\(220gm[s]?\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # (500gms) or (500gm) -> FAMILY_TUB_500GMS
    if re.search(r'\(500gm[s]?\)', name_lower):
        variant = 'FAMILY_TUB_500GMS'
        name = re.sub(r'\s*\(500gm[s]?\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # (725ml) -> FAMILY_TUB_725ML (or could be REGULAR_TUB_725ML - need to check)
    if re.search(r'\(725ml\)', name_lower):
        variant = 'FAMILY_TUB_725ML'
        name = re.sub(r'\s*\(725ml\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # (120gm) or (120gms) or Regular Scoop -> REGULAR_SCOOP_120GMS
    if re.search(r'\(120gm[s]?\)', name_lower) or '(regular scoop' in name_lower:
        variant = 'REGULAR_SCOOP_120GMS'
        name = re.sub(r'\s*\(120gm[s]?\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Regular Scoop\)?', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # (60gm) or (60gms) or Junior Scoop or Small Scoop -> JUNIOR_SCOOP_60GMS
    if re.search(r'\(60gm[s]?\)', name_lower) or 'junior scoop' in name_lower or 'small scoop' in name_lower:
        variant = 'JUNIOR_SCOOP_60GMS'
        name = re.sub(r'\s*\(60gm[s]?\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Junior Scoop\)?', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*Small Scoop', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Perfect Plenty patterns - check nested parentheses like "(Perfect Plenty (300ml))"
    if 'perfect plenty' in name_lower:
        if '300ml' in name_lower:
            variant = 'REGULAR_TUB_300ML'
        elif '200ml' in name_lower:
            variant = 'MINI_TUB_200ML'
        elif '200gm' in name_lower or '200gms' in name_lower:
            variant = 'PERFECT_PLENTY_200GMS'
        else:
            variant = 'PERFECT_PLENTY_200GMS'  # Default
        # Remove Perfect Plenty pattern (including nested parentheses)
        # Match: (Perfect Plenty (300ml)) or (Perfect Plenty) or Perfect Plenty
        name = re.sub(r'\s*\(Perfect Plenty\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Perfect Plenty[^)]*\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*Perfect Plenty\s*', ' ', name, flags=re.IGNORECASE)
        name = name.strip()
        return name, variant
    
    # Family Tub patterns
    if 'family tub' in name_lower:
        if '725ml' in name_lower:
            variant = 'FAMILY_TUB_725ML'
        elif '500gm' in name_lower or '500gms' in name_lower:
            variant = 'FAMILY_TUB_500GMS'
        else:
            variant = 'FAMILY_TUB_500GMS'  # Default
        # Remove Family Tub pattern (including nested)
        name = re.sub(r'\s*\(Family Tub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Family Tub[^)]*\)', '', name, flags=re.IGNORECASE)
        name = name.strip()
        return name, variant
    
    # Family Feast patterns
    if 'family feast' in name_lower:
        if '725ml' in name_lower:
            variant = 'FAMILY_TUB_725ML'
        else:
            variant = 'FAMILY_TUB_725ML'  # Default
        # Remove Family Feast pattern (including nested)
        name = re.sub(r'\s*\(Family Feast\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Family Feast[^)]*\)', '', name, flags=re.IGNORECASE)
        name = name.strip()
        return name, variant
    
    # Mini Tub patterns
    if 'mini tub' in name_lower or 'mini tub' in name_lower:
        if '200ml' in name_lower:
            variant = 'MINI_TUB_200ML'
        else:
            variant = 'MINI_TUB_160GMS'  # Default
        # Remove Mini Tub pattern (including nested)
        name = re.sub(r'\s*\(Mini [Tt]ub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Mini [Tt]ub[^)]*\)', '', name, flags=re.IGNORECASE)
        name = name.strip()
        return name, variant
    
    # Regular Tub patterns
    if 'regular tub' in name_lower:
        if '300ml' in name_lower:
            variant = 'REGULAR_TUB_300ML'
        else:
            variant = 'REGULAR_TUB_220GMS'  # Default
        # Remove Regular Tub pattern (including nested like "(Regular Tub (300ml))")
        name = re.sub(r'\s*\(Regular Tub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Regular Tub[^)]*\)', '', name, flags=re.IGNORECASE)
        name = name.strip()
        return name, variant
    
    # Piece counts
    if re.search(r'\(2\s*pc[s]?\)', name_lower):
        variant = '2_PIECES'
        name = re.sub(r'\s*\(2\s*pc[s]?\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    if re.search(r'\(1\s*pc[s]?\)', name_lower) or 'any 1' in name_lower:
        variant = '1_PIECE'
        name = re.sub(r'\s*\(1\s*pc[s]?\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Any 1\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Combo patterns
    if '200ml+200ml' in name_lower or '200ml + 200ml' in name_lower:
        variant = 'DUO_200ML_200ML'
        name = re.sub(r'\s*\(200ml\s*\+\s*200ml\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    if '200+200+200' in name_lower or '200ml+200ml+200ml' in name_lower:
        variant = 'FAMILY_PACK_3X200ML'
        name = re.sub(r'\s*\(200[ml\s]*\+200[ml\s]*\+200[ml\s]*\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Default: no variant info found
    return name.strip(), '1_PIECE'


def normalize_name(name: str) -> str:
    """Add 'Ice Cream' suffix to ice cream items that are missing it"""
    
    # If already has "Ice Cream", return as is
    if 'Ice Cream' in name:
        return name
    
    # Items that need "Ice Cream" suffix added (order matters - more specific first)
    ice_cream_bases = [
        'Eggless Chocolate Overload',  # Must come before 'Eggless Chocolate'
        'Eggless Cherry & Chocolate Fudge',
        'Eggless Strawberry Cream Cheese',
        'Eggless Coconut & Pineapple',
        'Eggless Coffee Mascarpone',
        'Eggless Fig & Orange',
        'Eggless Just Chocolate',
        'Eggless Milk Chocolate',
        'Eggless Paan & Gulkand',
        'Cherry & Chocolate Fudge',
        'Chocolate Overload',
        'Dates With Fig & Orange',
        'Cakes & Cookies',
        'Coconut & Pineapple',
        'Coffee Mascarpone',
        'Dates & Chocolate',
        'Fig & Orange',
        'Just Chocolate',
        'Old Fashion Vanilla',
        'Eggless Chocolate',  # Generic - must come after specific ones
    ]
    
    for base in ice_cream_bases:
        if name == base or name.startswith(base + ' '):
            return base + ' Ice Cream'
    
    return name


def determine_type(name: str) -> str:
    """Determine the type of item: Ice Cream, Dessert, Drinks, Extra, Combo"""
    
    name_lower = name.lower()
    
    # Special cases first
    if 'employee dessert' in name_lower:
        return 'Dessert'
    
    # Extras
    if any(word in name_lower for word in ['cup', 'waffle cone', 'takeaway cup', 'butter waffle']):
        return 'Extra'
    
    # Desserts
    if any(word in name_lower for word in ['dessert', 'pie', 'brownie', 'cheesecake', 'tiramisu', 'lamington', 'plum cake']):
        return 'Dessert'
    
    # Combos
    if any(word in name_lower for word in ['combo', 'pack', 'duo', 'trio', 'family pack']):
        return 'Combo'
    
    # Drinks
    if any(word in name_lower for word in ['coffee', 'tea', 'americano', 'cappuccino', 'latte', 'affogato', 'chai']):
        return 'Drinks'
    
    # Default to Ice Cream
    return 'Ice Cream'


def clean_order_item_name(raw_name: str) -> Dict[str, str]:
    """
    Main function to clean and normalize an order item name.
    
    Args:
        raw_name: Raw item name from order (e.g., "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))")
    
    Returns:
        Dictionary with keys: 'name', 'type', 'variant'
        Example: {
            'name': 'Old Fashion Vanilla Ice Cream',
            'type': 'Ice Cream',
            'variant': 'REGULAR_TUB_300ML'
        }
    """
    # Step 1: Fix HTML entities
    name = fix_html_entities(raw_name)
    
    # Step 2: Fix typos
    name = fix_typos(name)
    
    # Step 3: Extract variant
    name, variant = extract_variant_from_name(name)
    
    # Step 4: Normalize name (add "Ice Cream" suffix if needed)
    name = normalize_name(name)
    
    # Step 5: Determine type
    item_type = determine_type(name)
    
    # Step 6: Handle special cases
    # Employee Dessert
    if 'Employee Dessert' in name:
        name = 'Employee Dessert ( Any 1 )'
        item_type = 'Dessert'
        variant = '1_PIECE'
    
    return {
        'name': name.strip(),
        'type': item_type,
        'variant': variant,
    }


if __name__ == "__main__":
    # Test cases
    test_cases = [
        "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))",
        "Employee Dessert ( Any 1 )",
        "Eggless Chocolate Overload (Regular Scoop)",
        "Fig Orange Ice Cream (Regular Tub (300ml))",
        "Boston Cream Pie Dessert(2pcs)",
        "Waffle Cone",
        "Cup",
    ]
    
    print("Testing clean_order_item_name function:")
    print("=" * 80)
    for test in test_cases:
        result = clean_order_item_name(test)
        print(f"\nInput:  {test}")
        print(f"Output: {result}")

