"""
Script to clean and normalize cafe menu data.
Creates a CSV with columns: name, type, variant
"""

import csv
import re

# Raw input data - Main menu items
raw_data = [
    ('Banoffee Ice Cream', '160gm', 160, True),
    ('Banoffee Ice Cream (Family Tub', '500gms', 500, False),
    ('Banoffee Ice Cream (Junior Scoop', '60gm', 60, False),
    ('Banoffee Ice Cream (Mini Tub', '160gms', 160, False),
    ('Banoffee Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Bean To Bar Dark Chocolate Ice Cream Small Scoop', None, None, True),
    ('Bean-to-bar 70% Dark Chocolate Ice Cream', '160gm', 160, True),
    ('Bean-to-bar 70% Dark Chocolate Ice Cream 200ml', None, None, True),
    ('Bean-to-bar Chocolate Dark Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Bean-to-bar Dark Chocolate Ice Cream (Mini Tub', '160gms', 160, False),
    ('Bean-to-bar Dark Chocolate Ice Cream (Regular Tub', '220gms', 220, False),
    ('Boston Cream Pie', '1pcs', None, False),
    ('Boston Cream Pie Dessert', '2pcs', None, False),
    ('Boston Cream Piec', '2pcs', None, False),
    ('Brownie Cheesecake', None, None, False),
    ('Butter Waffle Cone', '2pcs', None, True),
    ('Butter Waffle Cones', '1pcs', None, False),
    ('Cakes & Cookies', '160gm', 160, True),
    ('Cakes & Cookies 200ml', None, None, True),
    ('Cakes & Cookies Ice Cream', '160gm', 160, True),
    ('Cakes & Cookies Ice Cream (Mini tub', '160gms', 160, False),
    ('Cakes & Cookies Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Cakes & Cookies Ice Cream (Regular Tub', '220gms', 220, False),
    ('Cakes & Cookies Ice Cream 200ml', None, None, True),
    ('Cakes & Cookies Ice Cream Small Scoop', None, None, True),
    ('Cakes &amp; Cookies Ice Cream Small Scoop', None, None, True),
    ('Cherry & Chocolate', '160gm', 160, True),
    ('Cherry & Chocolate Fudge Ice Cream (Junior Scoop', '60gm', 60, False),
    ('Cherry & Chocolate Fudge Ice Cream (Mini Tub', '160gms', 160, False),
    ('Cherry & Chocolate Fudge Ice Cream (Mini tub', '160gms', 160, False),
    ('Cherry & Chocolate Fudge Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Cherry & Chocolate Fudge Ice Cream (Regular Tub', '220gms', 220, False),
    ('Chocolate & Orange', 'contains Alcohol', 120, False),
    ('Chocolate & Orange', 'contains Alcohol', 160, False),
    ('Chocolate & Orange', 'contains Alcohol', 220, False),
    ('Chocolate & Orange With Alcohol', '160gm', 160, True),
    ('Chocolate Overload Ice Cream (Regular Tub', '220gms', 220, False),
    ('Classic Brownie & Ice Cream With Fudge Sauce', None, None, False),
    ('Classic Chocolate Lamington', '1pcs', None, False),
    ('Classic Chocolate Lamington', '2pcs', None, False),
    ('Classic Tiramisu', None, None, False),
    ('Coconut & Pineapple', '160gm', 160, True),
    ('Coconut & Pineapple Ice Cream (Mini Tub', '160gms', 160, False),
    ('Coconut & Pineapple Ice Cream (Regular Tub', '220gms', 220, False),
    ('Coffee Mascarpone Ice Cream', '160gm', 160, True),
    ('Coffee Mascarpone Ice Cream (Mini Tub', '160gms', 160, False),
    ('Coffee Mascarpone Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Coffee Mascarpone Ice Cream (Regular Tub', '220gms', 220, False),
    ('Coffee Mascarpone Ice Cream 200ml', None, None, True),
    ('Coffee Mascarpone Small Scoop', None, None, True),
    ('Cup', None, None, True),
    ('D&n Traditional Plum Cake', 'eggless', None, False),
    ('Dates & Chocolate', '160gm', 160, True),
    ('Dates & Chocolate (Mini Tub', '160gms', 160, False),
    ('Dates & Chocolate (Regular Scoop', '120gm', 120, False),
    ('Dates & Chocolate (Regular Tub', '220gms', 220, False),
    ('Dates & Chocolate 200ml', None, None, True),
    ('Dates & Chocolate 200ml', 'eggless', None, True),
    ('Dates &amp; Chocolate Small Scoop', None, None, True),
    ('Dates Rose & Nuts Ice Cream (Mini Tub', '160gms', 160, False),
    ('Dates Rose & Nuts Ice Cream (Mini tub', '160gms', 160, False),
    ('Dates Rose & Nuts Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Dates With Fig & Orange', '160gm', 160, True),
    ('Dates With Fig & Orange (Regular Tub', '220gms', 220, False),
    ('Dates With Fig & Orange 200ml', None, None, True),
    ('Dates With Fig & Orange 200ml', 'eggless', None, True),
    ('Dates With Fig &amp; Orange Small Scoop', None, None, True),
    ('Design Family Pack Of 3 Ice Creams', '200+200+200 Ml', None, False),
    ('Design Your Indulgence Duo Ice Creams', '200ml+200ml', None, False),
    ('Eggless Banoffee Ice Cream', 'Regular Scoop', None, False),
    ('Eggless Banoffee Ice Cream 200ml', None, None, True),
    ('Eggless Banoffee Ice Cream Small Scoop', None, None, True),
    ('Eggless Cherry & Chocolate 200ml', None, None, True),
    ('Eggless Cherry & Chocolate Ice Cream Small Scoop', None, None, True),
    ('Eggless Cherry &amp; Chocolate Ice Cream Small Scoop', None, None, True),
    ('Eggless Chocolate Ice Cream', 'Junior Scoop', None, False),
    ('Eggless Chocolate Ice Cream (Regular Tub', '300ml', None, False),
    ('Eggless Chocolate Overload', 'Regular Scoop', None, False),
    ('Eggless Chocolate Overload (Junior Scoop', '60gm', 60, False),
    ('Eggless Chocolate Overload (Mini tub', '160gms', 160, False),
    ('Eggless Chocolate Overload (Mini tub', '200ml', None, False),
    ('Eggless Chocolate Overload (Regular Scoop', '120gm', 120, False),
    ('Eggless Chocolate Overload 200ml', None, None, True),
    ('Eggless Chocolate Overload Ice Cream Small Scoop', None, None, True),
    ('Eggless Coconut & Pineapple 200ml', None, None, True),
    ('Eggless Coconut & Pineapple Ice Cream (Family Tub', '500gms', 500, False),
    ('Eggless Coconut & Pineapple Ice Cream (Mini Tub', '160gms', 160, False),
    ('Eggless Coconut & Pineapple Ice Cream (Mini tub', '160gms', 160, False),
    ('Eggless Coconut & Pineapple Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Eggless Coconut & Pineapple Small Scoop', None, None, True),
    ('Eggless Coconut &amp; Pineapple Small Scoop', None, None, True),
    ('Eggless Coffee Mascarpone Ice Cream', '160gm', 160, True),
    ('Eggless Coffee Mascarpone Ice Cream (Mini Tub', '160gms', 160, False),
    ('Eggless Coffee Mascarpone Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Eggless Coffee Mascarpone Ice Cream (Regular Tub', '300ml', None, False),
    ('Eggless Coffee Mascarpone Ice Cream 200ml', None, None, True),
    ('Eggless Coffee Mascarpone Ice Cream Small Scoop', None, None, True),
    ('Eggless Design Your Indulgence Duo Ice Creams', '200ml+200ml', None, False),
    ('Eggless Fig & Orange Ice Cream 200ml', None, None, True),
    ('Eggless Fig Orange Ice Cream', 'Junior Scoop', None, False),
    ('Eggless Just Chocolate Small Scoop', None, None, True),
    ('Eggless Paan & Gulkand Ice Cream 200ml', None, None, True),
    ('Eggless Paan & Gulkand Ice Cream Small Scoop', None, None, True),
    ('Eggless Paan &amp; Gulkand Ice Cream Small Scoop', None, None, True),
    ('Eggless Strawberry Cream Cheese Ice Cream', 'Regular Scoop', None, False),
    ('Eggless Strawberry Cream Cheese Ice Cream 200ml', None, None, True),
    ('Eggless Strawberry Cream Cheese Small Scoop', None, None, True),
    ('Fig & Orange', '160gm', 160, True),
    ('Fig & Orange 200ml', None, None, True),
    ('Fig & Orange Ice Cream', '160gm', 160, True),
    ('Fig & Orange Ice Cream (Family Tub', '500gms', 500, False),
    ('Fig & Orange Ice Cream (Mini Tub', '160gms', 160, False),
    ('Fig & Orange Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Fig & Orange Ice Cream (Regular Tub', '220gms', 220, False),
    ('Fudgy Chocolate Brownie', '1pc', None, False),
    ('Fudgy Chocolate Brownie', '2pcs', None, False),
    ('Half In Half Regular Scoop Combo', None, None, False),
    ('Just Chocolate Ice Cream (Junior Scoop', '60gm', 60, False),
    ('Just Chocolate Ice Cream (Mini Tub', '160gms', 160, False),
    ('Just Chocolate Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Masala Chai Ice Cream (Mini Tub', '160gms', 160, False),
    ('Monkey Business Ice Cream', '160gm', 160, True),
    ('Monkey Business Ice Cream (Mini Tub', '160gms', 160, False),
    ('Monkey Business Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Monkey Business Ice Cream (Regular Tub', '220gms', 220, False),
    ('Monkey Business Ice Cream 200ml', None, None, True),
    ('Monkey Business Ice Cream Small Scoop', None, None, True),
    ('New York Baked Cheesecake Eggless', None, None, False),
    ('Old Fashion Vanilla Ice Cream', '160gm', 160, True),
    ('Old Fashion Vanilla Ice Cream', 'Junior Scoop', None, False),
    ('Old Fashion Vanilla Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Old Fashion Vanilla Ice Cream 200ml', None, None, True),
    ('Old Fashion Vanilla Small Scoop', None, None, True),
    ('Orange & Chocolate Cheesecake', None, None, False),
    ('Paan & Gulkand Ice Cream (Junior Scoop', '60gm', 60, False),
    ('Paan & Gulkand Ice Cream (Mini Tub', '160gms', 160, False),
    ('Paan & Gulkand Ice Cream (Mini tub', '160gms', 160, False),
    ('Paan & Gulkand Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Paan & Gulkand Ice Cream (Regular Tub', '220gms', 220, False),
    ('Pistachio Ice Cream', '160gm', 160, True),
    ('Pistachio Ice Cream (Mini Tub', '160gms', 160, False),
    ('Pistachio Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Pistachio Ice Cream (Regular Tub', '220gms', 220, False),
    ('Pistachio Ice Cream 200ml', None, None, True),
    ('Pistachio Ice Cream Ice Cream Small Scoop', None, None, True),
    ('Rose Cardamom Ice Cream', '160gm', 160, True),
    ('Rose Cardamom Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Rose Cardamom Ice Cream 200ml', None, None, True),
    ('Strawberry Cream Cheese Ice Cream', '160gm', 160, True),
    ('Strawberry Cream Cheese Ice Cream (Junior Scoop', '60gm', 60, False),
    ('Strawberry Cream Cheese Ice Cream (Mini Tub', '160gms', 160, False),
    ('Strawberry Cream Cheese Ice Cream (Mini tub', '160gms', 160, False),
    ('Strawberry Cream Cheese Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Sunshine Limone Ice Cream', '160gm', 160, True),
    ('Sunshine Limone Ice Cream (Mini Tub', '160gms', 160, False),
    ('Sunshine Limone Ice Cream (Regular Scoop', '120gm', 120, False),
    ('Sunshine Limone Ice Cream (Regular Tub', '220gms', 220, False),
    ('Sunshine Limone Ice Cream 200ml', None, None, True),
    ('Sunshine Limone Ice Cream Small Scoop', None, None, True),
    ('Takeaway Cup', None, None, True),
    ('Waffle Cone', None, None, True),
    # ============================================================
    # ADDON MENU ITEMS (appended from addon data)
    # ============================================================
    ('Banoffee Ice Cream (160gm)', None, 160, True),
    ('Bean To Bar Dark Chocolate Ice Cream Small Scoop', None, None, True),
    ('Bean-to-bar 70% Dark Chocolate Ice Cream (160gm)', None, 160, True),
    ('Bean-to-bar 70% Dark Chocolate Ice Cream 200ml', None, None, True),
    ('Butter Waffle Cone (2pcs)', None, None, True),
    ('Cakes & Cookies (160gm)', None, 160, True),
    ('Cakes & Cookies 200ml', None, None, True),
    ('Cakes & Cookies Ice Cream (160gm)', None, 160, True),
    ('Cakes & Cookies Ice Cream 200ml', None, None, True),
    ('Cakes & Cookies Ice Cream Small Scoop', None, None, True),
    ('Cakes &amp; Cookies 200ml', None, None, True),
    ('Cakes &amp; Cookies Ice Cream 200ml', None, None, True),
    ('Cakes &amp; Cookies Ice Cream Small Scoop', None, None, True),
    ('Cherry & Chocolate (160gm)', None, 160, True),
    ('Chocolate & Orange With Alcohol (160gm)', None, 160, True),
    ('Chocolate Overload (160gm)', None, 160, True),
    ('Coconut & Pineapple (160gm)', None, 160, True),
    ('Coconut &amp; Pineapple (160gm)', None, 160, True),
    ('Coffee Mascarpone Ice Cream (160gm)', None, 160, True),
    ('Coffee Mascarpone Ice Cream 200ml', None, None, True),
    ('Coffee Mascarpone Small Scoop', None, None, True),
    ('Cup', None, None, True),
    ('Dates & Chocolate (160gm)', None, 160, True),
    ('Dates & Chocolate 200ml', None, None, True),
    ('Dates & Chocolate 200ml (eggless)', 'eggless', None, True),
    ('Dates &amp; Chocolate Small Scoop', None, None, True),
    ('Dates With Fig & Orange (160gm)', None, 160, True),
    ('Dates With Fig & Orange 200ml', None, None, True),
    ('Dates With Fig & Orange 200ml (eggless)', 'eggless', None, True),
    ('Dates With Fig &amp; Orange Small Scoop', None, None, True),
    ('Eggles Cherry & Chocolate 200ml', None, None, True),
    ('Eggles Cherry &amp; Chocolate 200ml', None, None, True),
    ('Eggless Banoffee Ice Cream 200ml', None, None, True),
    ('Eggless Banoffee Ice Cream Small Scoop', None, None, True),
    ('Eggless Cherry & Chocolate 200ml', None, None, True),
    ('Eggless Cherry & Chocolate Ice Cream Small Scoop', None, None, True),
    ('Eggless Cherry &amp; Chocolate 200ml', None, None, True),
    ('Eggless Cherry &amp; Chocolate Ice Cream Small Scoop', None, None, True),
    ('Eggless Chocolate Ice Cream 200ml', None, None, True),
    ('Eggless Chocolate Overload 200ml', None, None, True),
    ('Eggless Chocolate Overload Ice Cream Small Scoop', None, None, True),
    ('Eggless Coconut & Pineapple 200ml', None, None, True),
    ('Eggless Coconut & Pineapple Small Scoop', None, None, True),
    ('Eggless Coconut &amp; Pineapple 200ml', None, None, True),
    ('Eggless Coconut &amp; Pineapple Small Scoop', None, None, True),
    ('Eggless Coffee Mascarpone Ice Cream (160gm)', None, 160, True),
    ('Eggless Coffee Mascarpone Ice Cream 200ml', None, None, True),
    ('Eggless Coffee Mascarpone Ice Cream Small Scoop', None, None, True),
    ('Eggless Fig & Orange 200ml', None, None, True),
    ('Eggless Fig & Orange Ice Cream 200ml', None, None, True),
    ('Eggless Fig & Orange Ice Cream Small Scoop', None, None, True),
    ('Eggless Fig &amp; Orange 200ml', None, None, True),
    ('Eggless Fig &amp; Orange Ice Cream Small Scoop', None, None, True),
    ('Eggless Just Chocolate 200ml', None, None, True),
    ('Eggless Just Chocolate Small Scoop', None, None, True),
    ('Eggless Milk Chocolate Ice Cream 200ml', None, None, True),
    ('Eggless Paan & Gulkand Ice Cream 200ml', None, None, True),
    ('Eggless Paan & Gulkand Ice Cream Small Scoop', None, None, True),
    ('Eggless Paan &amp; Gulkand Ice Cream 200ml', None, None, True),
    ('Eggless Paan &amp; Gulkand Ice Cream Small Scoop', None, None, True),
    ('Eggless Strawberry Cream Cheese Ice Cream 200ml', None, None, True),
    ('Eggless Strawberry Cream Cheese Small Scoop', None, None, True),
    ('Fig & Orange (160gm)', None, 160, True),
    ('Fig & Orange 200ml', None, None, True),
    ('Fig & Orange Ice Cream (160gm)', None, 160, True),
    ('Fig & Orange Ice Cream 200ml', None, None, True),
    ('Fig &amp; Orange 200ml', None, None, True),
    ('Fig &amp; Orange Ice Cream (160gm)', None, 160, True),
    ('Fig &amp; Orange Ice Cream 200ml', None, None, True),
    ('Fig &amp; Orange Ice Cream Small Scoop', None, None, True),
    ('Go Bananas Ice Cream 200ml', None, None, True),
    ('Just Chocolate (160gm)', None, 160, True),
    ('Masala Chai Ice Cream (160gm)', None, 160, True),
    ('Masala Chai Ice Cream 200ml', None, None, True),
    ('Masala Chai Ice Cream Small Scoop', None, None, True),
    ('Monkey Business Ice Cream (160gm)', None, 160, True),
    ('Monkey Business Ice Cream 200ml', None, None, True),
    ('Monkey Business Ice Cream Small Scoop', None, None, True),
    ('Old Fashion Vanilla Ice Cream (160gm)', None, 160, True),
    ('Old Fashion Vanilla Ice Cream 200ml', None, None, True),
    ('Old Fashion Vanilla Small Scoop', None, None, True),
    ('Paan & Gulkand Ice Cream (160gm)', None, 160, True),
    ('Pistachio Ice Cream (160gm)', None, 160, True),
    ('Pistachio Ice Cream 200ml', None, None, True),
    ('Pistachio Ice Cream Ice Cream Small Scoop', None, None, True),
    ('Rose Cardamom Ice Cream (160gm)', None, 160, True),
    ('Rose Cardamom Ice Cream 200ml', None, None, True),
    ('Rose Cardamom Ice Cream Small Scoop', None, None, True),
    ('Strawberry Cream Cheese Ice Cream (160gm)', None, 160, True),
    ('Sunshine Limone Ice Cream (160gm)', None, 160, True),
    ('Sunshine Limone Ice Cream 200ml', None, None, True),
    ('Sunshine Limone Ice Cream Small Scoop', None, None, True),
    ('Takeaway Cup', None, None, True),
    ('Triple Chocolate Ice Cream 200ml', None, None, True),
    ('Waffle Cone', None, None, True),
]

# ============================================================
# TYPO FIXES AND NAME NORMALIZATION
# ============================================================

def fix_html_entities(text):
    """Fix HTML entities like &amp; -> &"""
    return text.replace('&amp;', '&')

def fix_typos(name):
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
    
    # Fix "Cherry & Chocolate" without "Fudge" -> add "Fudge" to match full name
    # Based on data, the full ice cream name is "Cherry & Chocolate Fudge Ice Cream"
    # This applies to both regular and Eggless versions
    if 'Cherry & Chocolate' in name and 'Fudge' not in name:
        name = name.replace('Cherry & Chocolate', 'Cherry & Chocolate Fudge')
    
    # Fix "Chocolate & Orange With Alcohol" -> standardize naming
    if 'Chocolate & Orange With Alcohol' in name:
        name = name.replace('Chocolate & Orange With Alcohol', 'Chocolate & Orange (Contains Alcohol) Ice Cream')
        # Remove duplicate "Ice Cream" if present
        name = name.replace('Ice Cream Ice Cream', 'Ice Cream')
    
    # Remove trailing parenthesis if incomplete (like "Mini Tub" without closing)
    name = re.sub(r'\s*\([^)]*$', '', name)
    
    return name.strip()

def normalize_name(raw_name, description, weight):
    """Extract and normalize the clean item name"""
    name = fix_html_entities(raw_name)
    name = fix_typos(name)
    
    # Remove variant info from name (things in parentheses at end)
    variant_patterns = [
        r'\s*\(Family Tub\)?',
        r'\s*\(Junior Scoop\)?',
        r'\s*\(Mini [Tt]ub\)?',
        r'\s*\(Regular Scoop\)?',
        r'\s*\(Regular Tub\)?',
        r'\s*\(Perfect Plenty[^)]*\)?',
        r'\s*\(160gm[s]?\)',  # Handle (160gm) or (160gms)
        r'\s*\(2\s*pc[s]?\)',  # Handle (2pcs)
        r'\s*\(1\s*pc[s]?\)',  # Handle (1pcs)
        r'\s*\(eggless\)',  # Handle (eggless) suffix
    ]
    for pattern in variant_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)
    
    # Remove size suffixes from name
    name = re.sub(r'\s+200ml$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+300ml$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+160gm$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+Small Scoop$', '', name, flags=re.IGNORECASE)
    
    # Handle "Chocolate & Orange" with alcohol
    if 'Chocolate & Orange' in name and description and 'alcohol' in description.lower():
        name = 'Chocolate & Orange (Contains Alcohol) Ice Cream'
    elif 'Chocolate & Orange With Alcohol' in name:
        name = 'Chocolate & Orange (Contains Alcohol) Ice Cream'
    
    # Add "Ice Cream" suffix for ice cream items that are missing it
    ice_cream_items_without_suffix = [
        'Cakes & Cookies',
        'Cherry & Chocolate Fudge',  # Note: "Cherry & Chocolate" gets converted to this in fix_typos
        'Chocolate Overload',
        'Coconut & Pineapple',
        'Coffee Mascarpone',
        'Dates & Chocolate',
        'Dates With Fig & Orange',
        'Fig & Orange',
        'Just Chocolate',
        'Old Fashion Vanilla',
        'Eggless Cherry & Chocolate Fudge',  # Note: converted from "Eggless Cherry & Chocolate"
        'Eggless Chocolate',
        'Eggless Chocolate Overload',
        'Eggless Coconut & Pineapple',
        'Eggless Coffee Mascarpone',
        'Eggless Fig & Orange',
        'Eggless Just Chocolate',
        'Eggless Milk Chocolate',
        'Eggless Paan & Gulkand',
        'Eggless Strawberry Cream Cheese',
    ]
    
    for item in ice_cream_items_without_suffix:
        if name == item or name.startswith(item + ' '):
            if 'Ice Cream' not in name:
                name = item + ' Ice Cream'
            break
    
    # Ensure "Cherry & Chocolate Fudge" stays as is (it's not "Cherry & Chocolate")
    # Already handled by exact match above
    
    return name.strip()

# ============================================================
# TYPE DETERMINATION
# ============================================================

# Extras
EXTRAS = [
    'Cup',
    'Takeaway Cup',
    'Waffle Cone',
    'Butter Waffle Cone',
    'Butter Waffle Cones',
]

# Desserts
DESSERTS = [
    'Boston Cream Pie',
    'Boston Cream Pie Dessert',
    'Brownie Cheesecake',
    'Classic Brownie & Ice Cream With Fudge Sauce',
    'Classic Chocolate Lamington',
    'Classic Tiramisu',
    'D&N Traditional Plum Cake',
    'Fudgy Chocolate Brownie',
    'New York Baked Cheesecake Eggless',
    'Orange & Chocolate Cheesecake',
]

# Combos
COMBOS = [
    'Design Family Pack Of 3 Ice Creams',
    'Design Your Indulgence Duo Ice Creams',
    'Eggless Design Your Indulgence Duo Ice Creams',
    'Half In Half Regular Scoop Combo',
]

def determine_type(name, raw_name):
    """Determine the type of item: Ice Cream, Dessert, Drinks, Extra, Combo"""
    
    # Check extras
    for extra in EXTRAS:
        if extra.lower() in name.lower() or extra.lower() in raw_name.lower():
            return 'Extra'
    
    # Check desserts
    for dessert in DESSERTS:
        if dessert.lower() in name.lower() or dessert.lower() in raw_name.lower():
            return 'Dessert'
    
    # Check combos
    for combo in COMBOS:
        if combo.lower() in name.lower() or combo.lower() in raw_name.lower():
            return 'Combo'
    
    # Default to Ice Cream for most items
    return 'Ice Cream'

# ============================================================
# VARIANT DETERMINATION
# ============================================================

def determine_variant(raw_name, description, weight):
    """Determine the variant based on name, description, and weight"""
    
    raw_lower = raw_name.lower()
    desc_lower = (description or '').lower()
    
    # Check for explicit variant in name
    if '(family tub' in raw_lower or 'family tub' in desc_lower:
        return 'FAMILY_TUB_500GMS'
    
    if '(junior scoop' in raw_lower or desc_lower == 'junior scoop':
        return 'JUNIOR_SCOOP_60GMS'
    
    if '(mini tub' in raw_lower or 'mini tub' in desc_lower:
        return 'MINI_TUB_160GMS'
    
    if '(regular scoop' in raw_lower or desc_lower == 'regular scoop':
        return 'REGULAR_SCOOP_120GMS'
    
    if '(regular tub' in raw_lower or 'regular tub' in desc_lower:
        if '300ml' in desc_lower or '300ml' in raw_lower:
            return 'REGULAR_TUB_300ML'
        return 'REGULAR_TUB_220GMS'
    
    if 'small scoop' in raw_lower:
        return 'JUNIOR_SCOOP_60GMS'
    
    # Check for (160gm) pattern in name (common in addon data)
    if '(160gm' in raw_lower:
        return 'MINI_TUB_160GMS'
    
    # Check for piece counts in name (for extras like Butter Waffle Cone)
    if '(2pc' in raw_lower or '(2 pc' in raw_lower:
        return '2_PIECES'
    if '(1pc' in raw_lower or '(1 pc' in raw_lower:
        return '1_PIECE'
    
    # Check for size in description
    if description:
        if '200ml+200ml' in description.lower() or '200+200+200' in description.lower():
            # Combo variant
            if '200+200+200' in description:
                return 'FAMILY_PACK_3X200ML'
            return 'DUO_200ML_200ML'
        
        if '300ml' in description.lower():
            return 'REGULAR_TUB_300ML'
        
        if '200ml' in description.lower():
            return 'MINI_TUB_200ML'
    
    # Check for 200ml in name
    if '200ml' in raw_lower:
        return 'MINI_TUB_200ML'
    
    if '300ml' in raw_lower:
        return 'REGULAR_TUB_300ML'
    
    # Check weight-based inference for standalone entries
    if weight == 500:
        return 'FAMILY_TUB_500GMS'
    elif weight == 220:
        return 'REGULAR_TUB_220GMS'
    elif weight == 160:
        return 'MINI_TUB_160GMS'
    elif weight == 120:
        return 'REGULAR_SCOOP_120GMS'
    elif weight == 60:
        return 'JUNIOR_SCOOP_60GMS'
    
    # Check description for piece counts (desserts/extras)
    if description:
        if '2pc' in desc_lower or '2 pc' in desc_lower:
            return '2_PIECES'
        if '1pc' in desc_lower or '1 pc' in desc_lower:
            return '1_PIECE'
    
    # Default to 1_PIECE for desserts/extras with no other info
    return '1_PIECE'

# ============================================================
# SPECIAL CASE HANDLING
# ============================================================

def handle_special_cases(name, item_type, variant, raw_name, description, weight):
    """Handle special edge cases"""
    
    # Boston Cream Pie - all should be "Boston Cream Pie"
    if 'boston cream pie' in name.lower():
        name = 'Boston Cream Pie'
        item_type = 'Dessert'
    
    # Half In Half Combo
    if 'half in half' in name.lower():
        variant = 'HALF_IN_HALF_REGULAR_SCOOP'
    
    # Butter Waffle Cone/Cones
    if 'butter waffle' in name.lower():
        name = 'Butter Waffle Cone'
        item_type = 'Extra'
    
    # Waffle Cone standalone
    if name.lower() == 'waffle cone':
        item_type = 'Extra'
        variant = '1_PIECE'
    
    # Cup / Takeaway Cup
    if name.lower() in ['cup', 'takeaway cup']:
        item_type = 'Extra'
        variant = '1_PIECE'
    
    return name, item_type, variant

# ============================================================
# MAIN PROCESSING
# ============================================================

def process_data():
    """Process all raw data and return cleaned entries"""
    results = []
    
    for entry in raw_data:
        raw_name, description, weight, _ = entry
        
        # Normalize the name
        name = normalize_name(raw_name, description, weight)
        
        # Determine type
        item_type = determine_type(name, raw_name)
        
        # Determine variant
        variant = determine_variant(raw_name, description, weight)
        
        # Handle special cases
        name, item_type, variant = handle_special_cases(name, item_type, variant, raw_name, description, weight)
        
        results.append({
            'name': name,
            'type': item_type,
            'variant': variant,
            'original': raw_name  # Keep for debugging
        })
    
    return results

def deduplicate_and_sort(results):
    """Remove duplicates and sort results"""
    seen = set()
    unique_results = []
    
    for r in results:
        key = (r['name'], r['type'], r['variant'])
        if key not in seen:
            seen.add(key)
            unique_results.append(r)
    
    # Sort by name, then type, then variant
    unique_results.sort(key=lambda x: (x['name'], x['type'], x['variant']))
    
    return unique_results

def save_to_csv(results, filename='cleaned_menu.csv'):
    """Save results to CSV file"""
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['name', 'type', 'variant'])
        for r in results:
            writer.writerow([r['name'], r['type'], r['variant']])
    
    print(f"Saved {len(results)} entries to {filename}")

def main():
    print("Processing menu data...")
    results = process_data()
    
    print(f"\nProcessed {len(results)} raw entries")
    
    # Deduplicate
    unique_results = deduplicate_and_sort(results)
    print(f"After deduplication: {len(unique_results)} unique entries")
    
    # Save to CSV
    save_to_csv(unique_results)
    
    # Print preview
    print("\n" + "="*80)
    print("PREVIEW (first 30 entries):")
    print("="*80)
    for r in unique_results[:30]:
        print(f"{r['name']},{r['type']},{r['variant']}")
    
    print("\n" + "="*80)
    print("ENTRIES THAT MAY NEED REVIEW:")
    print("="*80)
    
    # Flag potential issues
    for r in unique_results:
        # Items without "Ice Cream" in name that are typed as Ice Cream
        if r['type'] == 'Ice Cream' and 'Ice Cream' not in r['name']:
            print(f"[CHECK] Ice Cream without suffix: {r['name']}, {r['variant']}")

if __name__ == '__main__':
    main()

