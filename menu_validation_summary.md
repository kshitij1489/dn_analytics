# Menu Validation Summary

## Status

✅ **Created reusable cleaning module:** `clean_order_item.py`
- Can process individual order items
- Handles typos, variants, and type detection
- Ready for integration into order processing pipeline

✅ **Created validation script:** `validate_menu_coverage.py`
- Identifies missing items from order data
- Found 17 missing order items and 6 missing addons

## Missing Items Found

### High Priority (appears multiple times):
1. **Employee Dessert ( Any 1 )** - Special employee item (confirmed exists in data)
2. **Eggless Chocolate Overload (Regular Scoop)** - 6 occurrences
3. **Eggless Cherry & Chocolate Ice Cream (Regular Scoop)** - 6 occurrences
4. **Fig Orange Ice Cream (Regular Tub (300ml))** - 3 occurrences (typo: should be "Fig & Orange")

### Medium Priority:
- Various 300ml variants
- 725ml variants (Family Tub/Feast)
- Combo items (Mocha Indulgence Duo, etc.)

## Questions for You

1. **Menu Item ID Design:**
   - Do you prefer the **three-table approach** (menu_items, variants, menu_item_variants) or **simpler composite key** approach?
   - See `menu_item_design.md` for details

2. **Missing Items:**
   - Should I add all missing items to `cleaned_menu.csv` now?
   - Or wait until we process all orders to get a complete list?

3. **Variant Handling:**
   - Some items have variants like "Perfect Plenty (300ml)" - should these be:
     - Separate menu items? OR
     - Same menu item with different variants?
   - Example: "Old Fashion Vanilla Ice Cream" with variants: MINI_TUB_160GMS, REGULAR_TUB_300ML, etc.

4. **Addons as Menu Items:**
   - You mentioned addons are also menu items (e.g., "Waffle Cone" can be standalone or addon)
   - Should addons have their own menu_item_id in the menu_items table?
   - How do we distinguish when it's used as addon vs standalone order item?

5. **Employee Dessert:**
   - "Employee Dessert ( Any 1 )" - should this be:
     - A single menu item with variant 1_PIECE? OR
     - A special category that allows any 1 dessert from the Dessert category?

## Next Steps

1. **Fix nested parentheses regex** in `clean_order_item.py` (minor issue)
2. **Add missing items** to cleaned_menu.csv (once you confirm approach)
3. **Create menu_items table** with proper IDs (once you choose design)
4. **Integrate cleaning function** into order processing pipeline (Task 3)

## Current Cleaning Function Status

The `clean_order_item_name()` function:
- ✅ Fixes typos (Eggles → Eggless, Fig Orange → Fig & Orange, etc.)
- ✅ Extracts variants (MINI_TUB_160GMS, REGULAR_SCOOP_120GMS, etc.)
- ✅ Determines type (Ice Cream, Dessert, Extra, Combo, Drinks)
- ⚠️ Minor issue: Nested parentheses like "(Perfect Plenty (300ml))" need better regex

## Recommendation

1. **Add "Employee Dessert ( Any 1 )" immediately** to cleaned_menu.csv
2. **Process all 5,000 orders** to get complete list of missing items
3. **Use three-table approach** for menu items (more flexible for analytics)
4. **Treat addons as menu items** with a flag `can_be_addon = True`

