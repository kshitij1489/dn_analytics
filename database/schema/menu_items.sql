-- ============================================================================
-- Menu Items and Variants Schema
-- ============================================================================
-- This schema defines the normalized menu structure with items, variants,
-- and their relationships. Prices are stored at the variant level.
--
-- Design:
--   - menu_items: Base products (e.g., "Banoffee Ice Cream")
--   - variants: Size/quantity options (e.g., MINI_TUB_160GMS)
--   - menu_item_variants: Junction table linking items to variants with pricing
--
-- ============================================================================

-- ============================================================================
-- MENU ITEMS TABLE
-- ============================================================================
-- Stores base menu items (products). Each item can have multiple variants.
-- Prices are stored in menu_item_variants table, not here.

CREATE TABLE IF NOT EXISTS menu_items (
    menu_item_id SERIAL PRIMARY KEY,
    
    -- Item identification
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50) NOT NULL,  -- Ice Cream, Dessert, Extra, Combo, Drinks, Service
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- PetPooja identifiers (for matching with order data)
    petpooja_itemid BIGINT,  -- From OrderItem.itemid (may be NULL if not yet seen in orders)
    itemcode VARCHAR(100),   -- From OrderItem.itemcode (e.g., "VANILLAICE")
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    UNIQUE(name, type)  -- Prevent duplicate items with same name and type
);

-- ============================================================================
-- VARIANTS TABLE
-- ============================================================================
-- Stores variant definitions shared across all menu items.
-- Examples: MINI_TUB_160GMS, REGULAR_SCOOP_120GMS, 1_PIECE, 2_PIECES

CREATE TABLE IF NOT EXISTS variants (
    variant_id SERIAL PRIMARY KEY,
    
    -- Variant identification
    variant_name VARCHAR(100) NOT NULL UNIQUE,  -- MINI_TUB_160GMS, etc.
    description TEXT,  -- Optional description of the variant
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- MENU ITEM VARIANTS TABLE (Junction)
-- ============================================================================
-- Links menu items to their available variants with pricing and eligibility flags.
-- This is where prices are stored (not in menu_items table).

CREATE TABLE IF NOT EXISTS menu_item_variants (
    menu_item_variant_id SERIAL PRIMARY KEY,
    
    -- Relationships
    menu_item_id INTEGER NOT NULL,
    -- FOREIGN KEY (menu_item_id) REFERENCES menu_items(menu_item_id) ON DELETE CASCADE,
    -- Note: Add FK constraint after menu_items table is created
    
    variant_id INTEGER NOT NULL,
    -- FOREIGN KEY (variant_id) REFERENCES variants(variant_id) ON DELETE CASCADE,
    -- Note: Add FK constraint after variants table is created
    
    -- Pricing
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),  -- Price for this variant of this item
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Eligibility flags
    addon_eligible BOOLEAN DEFAULT FALSE,      -- Can this variant be used as an addon?
    delivery_eligible BOOLEAN DEFAULT TRUE,    -- Is this variant available for delivery?
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    UNIQUE(menu_item_id, variant_id)  -- Prevent duplicate variant assignments
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Menu Items Indexes
CREATE INDEX IF NOT EXISTS idx_menu_items_name 
    ON menu_items(name);

CREATE INDEX IF NOT EXISTS idx_menu_items_type 
    ON menu_items(type);

CREATE INDEX IF NOT EXISTS idx_menu_items_is_active 
    ON menu_items(is_active);

CREATE INDEX IF NOT EXISTS idx_menu_items_petpooja_itemid 
    ON menu_items(petpooja_itemid);

CREATE INDEX IF NOT EXISTS idx_menu_items_itemcode 
    ON menu_items(itemcode);

-- Composite index for common lookups
CREATE INDEX IF NOT EXISTS idx_menu_items_name_type 
    ON menu_items(name, type);

-- Variants Indexes
CREATE INDEX IF NOT EXISTS idx_variants_variant_name 
    ON variants(variant_name);

-- Menu Item Variants Indexes
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_menu_item_id 
    ON menu_item_variants(menu_item_id);

CREATE INDEX IF NOT EXISTS idx_menu_item_variants_variant_id 
    ON menu_item_variants(variant_id);

CREATE INDEX IF NOT EXISTS idx_menu_item_variants_is_active 
    ON menu_item_variants(is_active);

CREATE INDEX IF NOT EXISTS idx_menu_item_variants_addon_eligible 
    ON menu_item_variants(addon_eligible);

CREATE INDEX IF NOT EXISTS idx_menu_item_variants_delivery_eligible 
    ON menu_item_variants(delivery_eligible);

-- Composite index for common queries
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_menu_variant 
    ON menu_item_variants(menu_item_id, variant_id);

-- ============================================================================
-- FOREIGN KEY CONSTRAINTS
-- ============================================================================
-- Uncomment these after creating the tables

-- ALTER TABLE menu_item_variants
--     ADD CONSTRAINT fk_menu_item_variants_menu_item_id 
--     FOREIGN KEY (menu_item_id) REFERENCES menu_items(menu_item_id) ON DELETE CASCADE;

-- ALTER TABLE menu_item_variants
--     ADD CONSTRAINT fk_menu_item_variants_variant_id 
--     FOREIGN KEY (variant_id) REFERENCES variants(variant_id) ON DELETE CASCADE;

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON TABLE menu_items IS 
    'Stores base menu items (products). Each item can have multiple variants. '
    'Prices are stored in menu_item_variants table, not here.';

COMMENT ON COLUMN menu_items.type IS 
    'Item category: Ice Cream, Dessert, Extra, Combo, Drinks, or Service';

COMMENT ON COLUMN menu_items.petpooja_itemid IS 
    'PetPooja item ID for matching with order data. NULL if item not yet seen in orders.';

COMMENT ON COLUMN menu_items.itemcode IS 
    'PetPooja item code (e.g., "VANILLAICE"). Used for matching order items.';

COMMENT ON TABLE variants IS 
    'Stores variant definitions shared across all menu items. '
    'Examples: MINI_TUB_160GMS, REGULAR_SCOOP_120GMS, 1_PIECE, 2_PIECES';

COMMENT ON TABLE menu_item_variants IS 
    'Junction table linking menu items to their available variants with pricing. '
    'This is where prices are stored (not in menu_items table).';

COMMENT ON COLUMN menu_item_variants.price IS 
    'Price for this specific variant of this menu item.';

COMMENT ON COLUMN menu_item_variants.addon_eligible IS 
    'Can this variant be used as an addon to other items? '
    'Default: FALSE. Set to TRUE for items like "Waffle Cone" that can be addons.';

COMMENT ON COLUMN menu_item_variants.delivery_eligible IS 
    'Is this variant available for delivery? '
    'Default: TRUE. Set to FALSE for items that are dine-in only.';

-- ============================================================================
-- EXAMPLE QUERIES
-- ============================================================================

-- Get all active menu items with their variants and prices
-- SELECT 
--     mi.menu_item_id,
--     mi.name,
--     mi.type,
--     v.variant_name,
--     miv.price,
--     miv.addon_eligible,
--     miv.delivery_eligible
-- FROM menu_items mi
-- JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
-- JOIN variants v ON miv.variant_id = v.variant_id
-- WHERE mi.is_active = TRUE AND miv.is_active = TRUE
-- ORDER BY mi.name, v.variant_name;

-- Get all variants available for a specific menu item
-- SELECT 
--     v.variant_name,
--     miv.price,
--     miv.addon_eligible,
--     miv.delivery_eligible
-- FROM menu_item_variants miv
-- JOIN variants v ON miv.variant_id = v.variant_id
-- WHERE miv.menu_item_id = 1 AND miv.is_active = TRUE;

-- Get all items that can be used as addons
-- SELECT 
--     mi.name,
--     mi.type,
--     v.variant_name,
--     miv.price
-- FROM menu_items mi
-- JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
-- JOIN variants v ON miv.variant_id = v.variant_id
-- WHERE miv.addon_eligible = TRUE AND miv.is_active = TRUE;

-- Get all items available for delivery
-- SELECT 
--     mi.name,
--     mi.type,
--     v.variant_name,
--     miv.price
-- FROM menu_items mi
-- JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
-- JOIN variants v ON miv.variant_id = v.variant_id
-- WHERE miv.delivery_eligible = TRUE AND miv.is_active = TRUE;

-- Find menu item by PetPooja itemcode
-- SELECT * FROM menu_items WHERE itemcode = 'VANILLAICE';

-- Get all variants for a specific variant name (across all items)
-- SELECT 
--     mi.name,
--     mi.type,
--     miv.price
-- FROM menu_items mi
-- JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
-- JOIN variants v ON miv.variant_id = v.variant_id
-- WHERE v.variant_name = 'MINI_TUB_160GMS' AND miv.is_active = TRUE;

