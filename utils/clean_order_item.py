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
from typing import Dict, List, Optional, Set, Tuple

from utils.id_generator import generate_deterministic_id

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

_MEASURE_RE = re.compile(r'(\d+)\s*(ml|gms?|kg)\b', re.IGNORECASE)
_MEASURE_UNIT = {'ml': 'ML', 'kg': 'KG', 'gm': 'GMS', 'gms': 'GMS'}


def _unknown_measure_variant(text: str) -> Optional[str]:
    """Return UNKNOWN_<N><UNIT> for the first size token in text, else None.

    Used so an unrecognized size inside a package branch surfaces as a visible
    UNKNOWN variant for review instead of silently inheriting that branch's
    hardcoded default weight.
    """
    m = _MEASURE_RE.search(text)
    if not m:
        return None
    return f"UNKNOWN_{m.group(1)}{_MEASURE_UNIT[m.group(2).lower()]}"


def _has_measure(text: str, amount: int, unit: str) -> bool:
    """Bounded known-size check so e.g. '1300ml' does NOT match '300ml'.

    `unit` is a regex fragment: 'ml', 'gms?' (matches gm/gms), or 'kg'.
    """
    return re.search(rf'\b{amount}\s*{unit}\b', text, flags=re.IGNORECASE) is not None


def _remove_measure(text: str, amount: int, unit: str) -> str:
    """Remove a bounded size token without clipping larger numbers."""
    return re.sub(rf'\s*\(?\b{amount}\s*{unit}\b\)?', '', text, flags=re.IGNORECASE)


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
        if _has_measure(name, 725, 'ml'):
            variant = 'FAMILY_TUB_725ML'
        elif _has_measure(name, 700, 'ml'):
            variant = 'FAMILY_TUB_700ML'
        elif _has_measure(name, 550, 'gms?'):
            variant = 'FAMILY_TUB_550GMS'
        else:
            variant = _unknown_measure_variant(name) or 'FAMILY_TUB_725ML'
        name = re.sub(r'\s*\(Family Feast\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Family Feast\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Family Tub patterns
    if 'family tub' in name_lower:
        if _has_measure(name, 725, 'ml'):
            variant = 'FAMILY_TUB_725ML'
        elif _has_measure(name, 700, 'ml'):
            variant = 'FAMILY_TUB_700ML'
        elif _has_measure(name, 500, 'gms?'):
            variant = 'FAMILY_TUB_500GMS'
        else:
            variant = _unknown_measure_variant(name) or 'FAMILY_TUB_500GMS'
        name = re.sub(r'\s*\(Family Tub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Family Tub\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Perfect Plenty patterns
    if 'perfect plenty' in name_lower:
        if _has_measure(name, 350, 'ml'):
            variant = 'PERFECT_PLENTY_350ML'
        elif _has_measure(name, 325, 'ml'):
            variant = 'PERFECT_PLENTY_325ML'
        elif _has_measure(name, 300, 'ml'):
            variant = 'PERFECT_PLENTY_300ML'
        elif _has_measure(name, 200, 'ml'):
            variant = 'PERFECT_PLENTY_200ML'
        elif _has_measure(name, 200, 'gms?'):
            variant = 'PERFECT_PLENTY_200GMS'
        else:
            variant = _unknown_measure_variant(name) or 'PERFECT_PLENTY_200GMS'
        name = re.sub(r'\s*\(Perfect Plenty\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Perfect Plenty\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Mini Indulgence patterns
    if 'mini indulgence' in name_lower:
        if _has_measure(name, 200, 'ml'):
            variant = 'MINI_TUB_200ML'
        else:
            variant = _unknown_measure_variant(name) or 'MINI_TUB_200ML'
        name = re.sub(r'\s*\(Mini Indulgence\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Mini Indulgence\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Regular Tub patterns
    if 'regular tub' in name_lower:
        if _has_measure(name, 300, 'ml'):
            variant = 'REGULAR_TUB_300ML'
        elif _has_measure(name, 220, 'gms?'):
            variant = 'REGULAR_TUB_220GMS'
        else:
            variant = _unknown_measure_variant(name) or 'REGULAR_TUB_220GMS'
        name = re.sub(r'\s*\(Regular Tub\s*\([^)]+\)\)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(Regular Tub\)', '', name, flags=re.IGNORECASE)
        return name.strip(), variant
    
    # Mini Tub patterns
    if 'mini tub' in name_lower:
        if _has_measure(name, 200, 'ml'):
            variant = 'MINI_TUB_200ML'
        elif _has_measure(name, 160, 'gms?'):
            variant = 'MINI_TUB_160GMS'
        else:
            variant = _unknown_measure_variant(name) or 'MINI_TUB_160GMS'
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
    two_piece_pattern = r'[\(\[]\s*2\s*(?:pc|pcs|piece|pieces)\s*[\)\]]'
    one_piece_pattern = r'[\(\[]\s*1\s*(?:pc|pcs|piece|pieces)\s*[\)\]]'

    if re.search(two_piece_pattern, name, flags=re.IGNORECASE):
        variant = '2_PIECES'
        name = re.sub(rf'\s*{two_piece_pattern}', '', name, flags=re.IGNORECASE)
        name = re.sub(r'Dessert$', '', name).strip()  # Remove "Dessert" suffix
        return name.strip(), variant
    
    if re.search(one_piece_pattern, name, flags=re.IGNORECASE):
        variant = '1_PIECE'
        name = re.sub(rf'\s*{one_piece_pattern}', '', name, flags=re.IGNORECASE)
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
    if _has_measure(name, 1, 'kg'):
        variant = '1KG'
        name = _remove_measure(name, 1, 'kg')
        return name.strip(), variant
    
    if _has_measure(name, 400, 'gms?'):
        variant = '400GMS'
        name = _remove_measure(name, 400, 'gms?')
        return name.strip(), variant
    
    # Factory visit patterns
    if re.search(r'\(single\)', name, flags=re.IGNORECASE):
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
    if _has_measure(name, 160, 'gms?'):
        variant = 'MINI_TUB_160GMS'
        name = _remove_measure(name, 160, 'gms?')
        return name.strip(), variant

    # Bounded 200ml (word-boundary so "1200ml" is NOT treated as 200ml — that
    # falls through to the generic UNKNOWN fallback instead of being corrupted).
    if re.search(r'\b200\s*ml\b', name, flags=re.IGNORECASE):
        # Bare 200ml with no tub context: honest volume label, not MINI_TUB.
        variant = '200ML'
        name = re.sub(r'\s*\(?\b200\s*ml\b\)?', '', name, flags=re.IGNORECASE)
        return name.strip(), variant

    # Generic size fallback: any unrecognized size token becomes a visible
    # UNKNOWN_<N><UNIT> variant instead of silently collapsing to 1_PIECE
    # (which undercounts weight/volume analytics).
    m = _MEASURE_RE.search(name)
    if m:
        num = m.group(1)
        unit = _MEASURE_UNIT[m.group(2).lower()]
        name = re.sub(r'\s*\(?\s*\d+\s*(?:ml|gms?|kg)\s*\)?', '', name, flags=re.IGNORECASE)
        return name.strip(), f'UNKNOWN_{num}{unit}'

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

    if re.fullmatch(r'(?:Butter\s+)?Waffle Cone(?:s)?', name, flags=re.IGNORECASE):
        name = 'Butter Waffle Cones'
    
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


def suggest_variant_for_resolution(item_name: str, item_type: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Infer a useful variant suggestion for the resolutions workflow."""
    if not item_name:
        return None

    clean_result = clean_order_item_name(item_name)
    candidate_variant = (clean_result.get('variant') or '').strip()
    item_name_lower = item_name.lower()
    normalized_item_type = (item_type or clean_result.get('type') or '').strip().lower()

    # The generic fallback is not useful as a resolution suggestion for menu clustering.
    if candidate_variant and candidate_variant != '1_PIECE':
        return {
            'variant_id': generate_deterministic_id(candidate_variant),
            'variant_name': candidate_variant,
        }

    if normalized_item_type == 'ice cream' or 'ice cream' in item_name_lower:
        variant_overrides = [
            (r'(60\s*gms?|60gm|junior scoop|small scoop)', 'JUNIOR_SCOOP_60GMS'),
            (r'(120\s*gms?|120gm|regular scoop|\(regular\))', 'REGULAR_SCOOP_120GMS'),
            (r'(160\s*gms?|160gm|mini tub)', 'MINI_TUB_160GMS'),
            (r'(200\s*ml|mini indulgence)', 'MINI_TUB_200ML'),
            (r'(220\s*gms?|220gm)', 'REGULAR_TUB_220GMS'),
            (r'(300\s*ml)', 'REGULAR_TUB_300ML'),
            (r'(725\s*ml)', 'FAMILY_TUB_725ML'),
            (r'(700\s*ml)', 'FAMILY_TUB_700ML'),
            (r'(500\s*gms?|500gm)', 'FAMILY_TUB_500GMS'),
        ]

        for pattern, variant_name in variant_overrides:
            if re.search(pattern, item_name_lower):
                return {
                    'variant_id': generate_deterministic_id(variant_name),
                    'variant_name': variant_name,
                }

    return None

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
