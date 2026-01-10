# Order Item Variant Table Design

## Overview

This document defines the schema for storing order items and their variants in the analytics database. Order items link to menu items and variants, preserving both the normalized structure and the actual order data.

---

## Design Philosophy

1. **Order Items** represent what was actually ordered (historical data)
2. **Menu Items** represent the catalog (current/active items)
3. **Variants** represent size/quantity options (shared across menu items)
4. **Order Items** must preserve:
   - What was ordered (raw name from PetPooja)
   - What it maps to (menu_item_id + variant_id)
   - Price at time of order (may differ from current menu price)
   - Quantity, discounts, taxes applied

---

## Core Tables

### 1. `order_items` Table

Stores individual items within each order, linked to menu items and variants.

```sql
CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    
    -- Order relationship
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    
    -- Menu item mapping (normalized)
    menu_item_id INTEGER REFERENCES menu_items(menu_item_id),
    variant_id INTEGER REFERENCES variants(variant_id),
    
    -- PetPooja identifiers (for reconciliation)
    petpooja_itemid BIGINT,  -- From OrderItem.itemid
    itemcode VARCHAR(100),   -- From OrderItem.itemcode (e.g., "VANILLAICE")
    
    -- Raw data preservation (for matching and debugging)
    name_raw VARCHAR(500) NOT NULL,  -- Original name from PetPooja
    category_name VARCHAR(255),      -- From OrderItem.category_name
    
    -- Order details
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price DECIMAL(10,2) NOT NULL,  -- Price per unit at time of order
    total_price DECIMAL(10,2) NOT NULL, -- quantity * unit_price
    tax_amount DECIMAL(10,2) DEFAULT 0,  -- Tax for this item
    discount_amount DECIMAL(10,2) DEFAULT 0,  -- Discount for this item
    
    -- Additional metadata
    specialnotes TEXT,              -- From OrderItem.specialnotes
    sap_code VARCHAR(100),          -- From OrderItem.sap_code
    vendoritemcode VARCHAR(100),    -- From OrderItem.vendoritemcode
    
    -- Matching confidence (for data quality)
    match_confidence DECIMAL(5,2),  -- 0-100, NULL if not matched
    match_method VARCHAR(50),        -- 'exact', 'fuzzy', 'manual', NULL
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Indexes
    INDEX idx_order_items_order_id (order_id),
    INDEX idx_order_items_menu_item_id (menu_item_id),
    INDEX idx_order_items_variant_id (variant_id),
    INDEX idx_order_items_petpooja_itemid (petpooja_itemid),
    INDEX idx_order_items_match_confidence (match_confidence)
);
```

### 2. `order_item_addons` Table

Stores addons attached to order items (e.g., "Cup", "Waffle Cone").

```sql
CREATE TABLE order_item_addons (
    order_item_addon_id SERIAL PRIMARY KEY,
    
    -- Order item relationship
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
    
    -- Addon mapping (addons are also menu items)
    menu_item_id INTEGER REFERENCES menu_items(menu_item_id),  -- NULL if addon not in menu
    variant_id INTEGER REFERENCES variants(variant_id),       -- Usually 1_PIECE
    
    -- PetPooja identifiers
    petpooja_addonid VARCHAR(100),  -- From addon.addonid
    
    -- Raw data
    name_raw VARCHAR(255) NOT NULL,  -- Original addon name
    group_name VARCHAR(100),         -- From addon.group_name (e.g., "Cuporcone")
    
    -- Order details
    quantity INTEGER NOT NULL DEFAULT 1,
    price DECIMAL(10,2) NOT NULL,    -- Price at time of order (often 0 for free addons)
    
    -- Additional metadata
    addon_sap_code VARCHAR(100),    -- From addon.addon_sap_code
    
    -- Matching confidence
    match_confidence DECIMAL(5,2),  -- 0-100, NULL if not matched
    match_method VARCHAR(50),        -- 'exact', 'fuzzy', 'manual', NULL
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    
    -- Indexes
    INDEX idx_order_item_addons_order_item_id (order_item_id),
    INDEX idx_order_item_addons_menu_item_id (menu_item_id)
);
```

---

## Relationship Diagram

```
┌─────────────────┐
│     orders      │
│  (order_id PK)  │
└────────┬────────┘
         │
         │ 1:N
         │
┌────────▼─────────────────┐
│      order_items          │
│  (order_item_id PK)       │
│  order_id FK              │
│  menu_item_id FK ────────┐│
│  variant_id FK ─────────┐││
│  petpooja_itemid        │││
│  name_raw               │││
│  quantity, prices       │││
└─────────────────────────┘││
                            ││
         ┌──────────────────┘│
         │                    │
         │                    │
┌────────▼────────┐  ┌────────▼────────┐
│   menu_items    │  │    variants     │
│ (menu_item_id)  │  │  (variant_id)   │
│ name, type      │  │  variant_name   │
└─────────────────┘  └─────────────────┘
         │
         │
         │ (addons are also menu items)
         │
┌────────▼─────────────────┐
│  order_item_addons       │
│  (order_item_addon_id)   │
│  order_item_id FK        │
│  menu_item_id FK         │
│  variant_id FK           │
│  name_raw                │
└──────────────────────────┘
```

---

## Key Design Decisions

### 1. **Dual Reference System**
- **`menu_item_id` + `variant_id`**: Normalized reference to catalog
- **`name_raw`**: Preserves original PetPooja name for:
  - Debugging mismatches
  - Historical accuracy
  - Re-matching if menu changes

### 2. **Price Preservation**
- Store `unit_price` and `total_price` at time of order
- Menu prices may change, but order prices should remain historical
- Enables accurate revenue analysis

### 3. **Matching Confidence**
- `match_confidence`: 0-100 score from fuzzy matching
- `match_method`: How the match was made
- Helps identify items needing manual review

### 4. **Addons as Menu Items**
- Addons reference `menu_items` table (same as order items)
- Allows unified analytics (e.g., "How many waffle cones sold?")
- `group_name` tracks addon categories (e.g., "Cuporcone")

### 5. **PetPooja ID Preservation**
- Store `petpooja_itemid` and `itemcode` for:
  - Reconciliation with PetPooja system
  - Debugging mismatches
  - Future API integrations

---

## Example Data

### Order Items
```
order_item_id | order_id | menu_item_id | variant_id | name_raw                                    | quantity | unit_price
--------------|----------|--------------|------------|---------------------------------------------|----------|------------
1             | 110      | 45           | 8          | Old Fashion Vanilla Ice Cream (Perfect... | 1        | 360.00
2             | 110      | 12           | 8          | Coffee Mascarpone Ice Cream (Perfect...  | 1        | 360.00
3             | 110      | 78           | 8          | Eggless Strawberry Cream Cheese...        | 1        | 360.00
```

### Order Item Addons
```
order_item_addon_id | order_item_id | menu_item_id | variant_id | name_raw | quantity | price
--------------------|--------------|--------------|------------|----------|----------|-------
1                   | 1            | 89           | 4          | Cup      | 1        | 0.00
```

---

## Matching Strategy

### Step 1: Extract Base Name and Variant
```python
from clean_order_item import clean_order_item_name

raw_name = "Old Fashion Vanilla Ice Cream (Perfect Plenty (300ml))"
cleaned = clean_order_item_name(raw_name)
# Returns: {
#   'name': 'Old Fashion Vanilla Ice Cream',
#   'type': 'Ice Cream',
#   'variant': 'REGULAR_TUB_300ML'
# }
```

### Step 2: Match to Menu Items
```python
# 1. Exact match on (name, type, variant)
menu_item = MenuItem.query.filter_by(
    name=cleaned['name'],
    type=cleaned['type'],
    variant=cleaned['variant']
).first()

# 2. If no exact match, fuzzy match on name + type
if not menu_item:
    menu_item = fuzzy_match_menu_item(cleaned['name'], cleaned['type'])

# 3. Get variant_id from variants table
variant = Variant.query.filter_by(variant_name=cleaned['variant']).first()
```

### Step 3: Store with Confidence Score
```python
order_item = OrderItem(
    order_id=order_id,
    menu_item_id=menu_item.menu_item_id if menu_item else None,
    variant_id=variant.variant_id if variant else None,
    name_raw=raw_name,
    match_confidence=confidence_score,  # 0-100
    match_method='exact' if exact_match else 'fuzzy'
)
```

---

## Data Quality Considerations

### 1. **Unmatched Items**
- `menu_item_id` and `variant_id` can be NULL
- `match_confidence` will be NULL or low (< 70)
- Flag for manual review

### 2. **Price Validation**
- `unit_price` should be positive
- `total_price` should equal `quantity * unit_price`
- Flag if `total_price` doesn't match order total

### 3. **Quantity Validation**
- `quantity` should be positive integer
- Flag if quantity > 10 (unusual)

### 4. **Variant Consistency**
- If `menu_item_id` exists, `variant_id` should also exist
- Flag if variant doesn't match menu item's available variants

---

## Indexes for Performance

```sql
-- Fast order lookups
CREATE INDEX idx_order_items_order_id ON order_items(order_id);

-- Fast menu item analytics
CREATE INDEX idx_order_items_menu_item_id ON order_items(menu_item_id);
CREATE INDEX idx_order_items_variant_id ON order_items(variant_id);

-- Fast PetPooja reconciliation
CREATE INDEX idx_order_items_petpooja_itemid ON order_items(petpooja_itemid);

-- Data quality queries
CREATE INDEX idx_order_items_match_confidence ON order_items(match_confidence)
WHERE match_confidence < 80;  -- Partial index for low-confidence matches

-- Addon lookups
CREATE INDEX idx_order_item_addons_order_item_id ON order_item_addons(order_item_id);
CREATE INDEX idx_order_item_addons_menu_item_id ON order_item_addons(menu_item_id);
```

---

## Migration Strategy

### Phase 1: Create Tables
1. Create `order_items` table
2. Create `order_item_addons` table
3. Create indexes

### Phase 2: Populate from Historical Data
1. Fetch all orders using `fetch_orders.py`
2. For each order:
   - Extract order items
   - Match to menu items using `clean_order_item.py`
   - Insert into `order_items`
   - Extract addons
   - Match addons to menu items
   - Insert into `order_item_addons`

### Phase 3: Incremental Updates
1. Fetch new orders (since last `stream_id`)
2. Process and insert new order items
3. Update matching confidence for previously unmatched items

---

## SQL Schema (Complete)

```sql
-- Order Items Table
CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    menu_item_id INTEGER REFERENCES menu_items(menu_item_id),
    variant_id INTEGER REFERENCES variants(variant_id),
    petpooja_itemid BIGINT,
    itemcode VARCHAR(100),
    name_raw VARCHAR(500) NOT NULL,
    category_name VARCHAR(255),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL CHECK (unit_price >= 0),
    total_price DECIMAL(10,2) NOT NULL CHECK (total_price >= 0),
    tax_amount DECIMAL(10,2) DEFAULT 0 CHECK (tax_amount >= 0),
    discount_amount DECIMAL(10,2) DEFAULT 0 CHECK (discount_amount >= 0),
    specialnotes TEXT,
    sap_code VARCHAR(100),
    vendoritemcode VARCHAR(100),
    match_confidence DECIMAL(5,2) CHECK (match_confidence >= 0 AND match_confidence <= 100),
    match_method VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Order Item Addons Table
CREATE TABLE order_item_addons (
    order_item_addon_id SERIAL PRIMARY KEY,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id) ON DELETE CASCADE,
    menu_item_id INTEGER REFERENCES menu_items(menu_item_id),
    variant_id INTEGER REFERENCES variants(variant_id),
    petpooja_addonid VARCHAR(100),
    name_raw VARCHAR(255) NOT NULL,
    group_name VARCHAR(100),
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),
    addon_sap_code VARCHAR(100),
    match_confidence DECIMAL(5,2) CHECK (match_confidence >= 0 AND match_confidence <= 100),
    match_method VARCHAR(50),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_menu_item_id ON order_items(menu_item_id);
CREATE INDEX idx_order_items_variant_id ON order_items(variant_id);
CREATE INDEX idx_order_items_petpooja_itemid ON order_items(petpooja_itemid);
CREATE INDEX idx_order_items_match_confidence ON order_items(match_confidence) WHERE match_confidence < 80;

CREATE INDEX idx_order_item_addons_order_item_id ON order_item_addons(order_item_id);
CREATE INDEX idx_order_item_addons_menu_item_id ON order_item_addons(menu_item_id);
```

---

## Next Steps

1. ✅ **Design Complete** - This document defines the schema
2. **Create SQL Migration** - Generate `database/schema/order_items.sql`
3. **Implement Matching Logic** - Create `data_cleaning/item_matcher.py`
4. **Test with Sample Data** - Load 100 orders to validate
5. **Full Data Load** - Process all 5,465 orders

---

*Last Updated: January 9, 2026*

