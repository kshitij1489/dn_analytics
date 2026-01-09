# Menu Item & Variant ID Design

## Design Philosophy

1. **Menu Items** are the base products (e.g., "Banoffee Ice Cream", "Boston Cream Pie")
2. **Variants** are size/quantity options for each menu item (e.g., MINI_TUB_160GMS, REGULAR_SCOOP_120GMS)
3. **Addons** are also menu items that can be attached to other items (e.g., "Waffle Cone" can be standalone or an addon)

## ID Structure

### Menu Item ID
- **Format:** Auto-incrementing integer or UUID
- **Example:** `menu_item_id = 1` for "Banoffee Ice Cream"
- **Uniqueness:** One menu item per unique (name, type) combination

### Variant ID  
- **Format:** Auto-incrementing integer or UUID
- **Example:** `variant_id = 1` for "MINI_TUB_160GMS"
- **Uniqueness:** One variant per unique variant name (shared across all menu items)

## Proposed Schema

```sql
-- Menu Items Table
CREATE TABLE menu_items (
    menu_item_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,  -- Ice Cream, Dessert, Extra, Combo, Drinks
    base_price DECIMAL(10,2),
    is_active BOOLEAN DEFAULT TRUE,
    petpooja_itemid BIGINT,  -- For matching with PetPooja data
    itemcode VARCHAR(100),   -- VANILLAICE, etc.
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(name, type)  -- Prevent duplicates
);

-- Variants Table (shared across all menu items)
CREATE TABLE variants (
    variant_id SERIAL PRIMARY KEY,
    variant_name VARCHAR(100) NOT NULL UNIQUE,  -- MINI_TUB_160GMS, etc.
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Menu Item Variants (junction table)
-- Links menu items to their available variants with pricing
CREATE TABLE menu_item_variants (
    menu_item_variant_id SERIAL PRIMARY KEY,
    menu_item_id INTEGER REFERENCES menu_items(menu_item_id),
    variant_id INTEGER REFERENCES variants(variant_id),
    price DECIMAL(10,2),  -- Price for this specific variant of this item
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(menu_item_id, variant_id)
);
```

## Example Data

### Menu Items
```
menu_item_id | name                          | type       | base_price
-------------|------------------------------|------------|------------
1            | Banoffee Ice Cream           | Ice Cream  | 160.00
2            | Boston Cream Pie              | Dessert    | 200.00
3            | Waffle Cone                  | Extra      | 0.00
4            | Employee Dessert ( Any 1 )    | Dessert    | 0.00
```

### Variants
```
variant_id | variant_name
-----------|------------------
1          | MINI_TUB_160GMS
2          | REGULAR_SCOOP_120GMS
3          | REGULAR_TUB_220GMS
4          | 1_PIECE
5          | 2_PIECES
```

### Menu Item Variants (Junction)
```
menu_item_variant_id | menu_item_id | variant_id | price
---------------------|--------------|------------|-------
1                    | 1            | 1          | 160.00
2                    | 1            | 2          | 120.00
3                    | 2            | 4          | 200.00
4                    | 4            | 4          | 0.00
```

## Benefits

1. **Normalized:** No duplicate variant names
2. **Flexible:** Easy to add new variants or menu items
3. **Pricing:** Can have different prices for same variant across items
4. **Query-friendly:** Easy to find all items with a specific variant
5. **Scalable:** Handles 300K+ orders efficiently

## Alternative: Composite Key Approach

If you prefer simpler structure:

```sql
CREATE TABLE menu_items (
    menu_item_id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,
    variant VARCHAR(100) NOT NULL,  -- MINI_TUB_160GMS, etc.
    price DECIMAL(10,2),
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(name, variant)
);
```

**Pros:** Simpler, fewer joins
**Cons:** Duplicate variant strings, less normalized

## Recommendation

Use the **three-table approach** (menu_items, variants, menu_item_variants) for:
- Better normalization
- Easier variant management
- More flexible pricing
- Better analytics (e.g., "all items with MINI_TUB variant")

