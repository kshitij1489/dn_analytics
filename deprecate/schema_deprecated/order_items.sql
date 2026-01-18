-- ============================================================================
-- Order Items and Variants Schema
-- ============================================================================
-- This schema defines tables for storing order items and their variants,
-- linking to menu items and variants for normalized analytics.
--
-- Dependencies:
--   - menu_items table (from menu_item_design.md)
--   - variants table (from menu_item_design.md)
--   - orders table (from agents.md)
--
-- ============================================================================

-- ============================================================================
-- ORDER ITEMS TABLE
-- ============================================================================
-- Stores individual items within each order, linked to menu items and variants.
-- Preserves both normalized structure and raw PetPooja data.

CREATE TABLE IF NOT EXISTS order_items (
    order_item_id SERIAL PRIMARY KEY,
    
    -- Order relationship
    order_id INTEGER NOT NULL,
    -- FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    -- Note: Add FK constraint after orders table is created
    
    -- Menu item mapping (normalized)
    menu_item_id UUID,
    variant_id UUID,
    
    -- PetPooja identifiers (for reconciliation)
    petpooja_itemid BIGINT,
    itemcode VARCHAR(100),  -- e.g., "VANILLAICE"
    
    -- Raw data preservation (for matching and debugging)
    name_raw VARCHAR(500) NOT NULL,  -- Original name from PetPooja
    category_name VARCHAR(255),      -- From OrderItem.category_name
    
    -- Order details
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL CHECK (unit_price >= 0),
    total_price DECIMAL(10,2) NOT NULL CHECK (total_price >= 0),
    tax_amount DECIMAL(10,2) DEFAULT 0 CHECK (tax_amount >= 0),
    discount_amount DECIMAL(10,2) DEFAULT 0 CHECK (discount_amount >= 0),
    
    -- Additional metadata
    specialnotes TEXT,
    sap_code VARCHAR(100),
    vendoritemcode VARCHAR(100),
    
    -- Matching confidence (for data quality)
    match_confidence DECIMAL(5,2) CHECK (
        match_confidence IS NULL OR 
        (match_confidence >= 0 AND match_confidence <= 100)
    ),
    match_method VARCHAR(50),  -- 'exact', 'fuzzy', 'manual', NULL
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- ORDER ITEM ADDONS TABLE
-- ============================================================================
-- Stores addons attached to order items (e.g., "Cup", "Waffle Cone").
-- Addons are also menu items, so they reference menu_items table.

CREATE TABLE IF NOT EXISTS order_item_addons (
    order_item_addon_id SERIAL PRIMARY KEY,
    
    -- Order item relationship
    order_item_id INTEGER NOT NULL,
    -- FOREIGN KEY (order_item_id) REFERENCES order_items(order_item_id) ON DELETE CASCADE,
    -- Note: Add FK constraint after order_items table is created
    
    -- Addon mapping (addons are also menu items)
    menu_item_id UUID,
    variant_id UUID,
    
    -- PetPooja identifiers
    petpooja_addonid VARCHAR(100),
    
    -- Raw data
    name_raw VARCHAR(255) NOT NULL,  -- Original addon name
    group_name VARCHAR(100),         -- e.g., "Cuporcone"
    
    -- Order details
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),
    
    -- Additional metadata
    addon_sap_code VARCHAR(100),
    
    -- Matching confidence
    match_confidence DECIMAL(5,2) CHECK (
        match_confidence IS NULL OR 
        (match_confidence >= 0 AND match_confidence <= 100)
    ),
    match_method VARCHAR(50),  -- 'exact', 'fuzzy', 'manual', NULL
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Order Items Indexes
CREATE INDEX IF NOT EXISTS idx_order_items_order_id 
    ON order_items(order_id);

CREATE INDEX IF NOT EXISTS idx_order_items_menu_item_id 
    ON order_items(menu_item_id);

CREATE INDEX IF NOT EXISTS idx_order_items_variant_id 
    ON order_items(variant_id);

CREATE INDEX IF NOT EXISTS idx_order_items_petpooja_itemid 
    ON order_items(petpooja_itemid);

-- Partial index for low-confidence matches (data quality queries)
CREATE INDEX IF NOT EXISTS idx_order_items_match_confidence 
    ON order_items(match_confidence) 
    WHERE match_confidence < 80;

-- Composite index for common queries (menu item + variant analytics)
CREATE INDEX IF NOT EXISTS idx_order_items_menu_variant 
    ON order_items(menu_item_id, variant_id);

-- Order Item Addons Indexes
CREATE INDEX IF NOT EXISTS idx_order_item_addons_order_item_id 
    ON order_item_addons(order_item_id);

CREATE INDEX IF NOT EXISTS idx_order_item_addons_menu_item_id 
    ON order_item_addons(menu_item_id);

CREATE INDEX IF NOT EXISTS idx_order_item_addons_group_name 
    ON order_item_addons(group_name);

-- ============================================================================
-- FOREIGN KEY CONSTRAINTS
-- ============================================================================
-- Uncomment these after creating the referenced tables

-- ALTER TABLE order_items
--     ADD CONSTRAINT fk_order_items_order_id 
--     FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE;

-- ALTER TABLE order_items
--     ADD CONSTRAINT fk_order_items_menu_item_id 
--     FOREIGN KEY (menu_item_id) REFERENCES menu_items(menu_item_id);

-- ALTER TABLE order_items
--     ADD CONSTRAINT fk_order_items_variant_id 
--     FOREIGN KEY (variant_id) REFERENCES variants(variant_id);

-- ALTER TABLE order_item_addons
--     ADD CONSTRAINT fk_order_item_addons_order_item_id 
--     FOREIGN KEY (order_item_id) REFERENCES order_items(order_item_id) ON DELETE CASCADE;

-- ALTER TABLE order_item_addons
--     ADD CONSTRAINT fk_order_item_addons_menu_item_id 
--     FOREIGN KEY (menu_item_id) REFERENCES menu_items(menu_item_id);

-- ALTER TABLE order_item_addons
--     ADD CONSTRAINT fk_order_item_addons_variant_id 
--     FOREIGN KEY (variant_id) REFERENCES variants(variant_id);

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON TABLE order_items IS 
    'Stores individual items within each order, linked to menu items and variants. '
    'Preserves both normalized structure and raw PetPooja data.';

COMMENT ON COLUMN order_items.menu_item_id IS 
    'Reference to menu_items table. NULL if item could not be matched to menu.';

COMMENT ON COLUMN order_items.variant_id IS 
    'Reference to variants table. NULL if variant could not be determined.';

COMMENT ON COLUMN order_items.match_confidence IS 
    'Confidence score (0-100) from matching algorithm. NULL if not matched. '
    'Scores < 80 should be reviewed manually.';

COMMENT ON COLUMN order_items.match_method IS 
    'How the match was made: exact, fuzzy, or manual. NULL if not matched.';

COMMENT ON TABLE order_item_addons IS 
    'Stores addons attached to order items. Addons are also menu items, '
    'so they reference menu_items table.';

COMMENT ON COLUMN order_item_addons.group_name IS 
    'Addon group category (e.g., "Cuporcone" for cup/cone options).';

-- ============================================================================
-- EXAMPLE QUERIES
-- ============================================================================

-- Find all order items for a specific order
-- SELECT * FROM order_items WHERE order_id = 110;

-- Find all order items for a specific menu item
-- SELECT * FROM order_items WHERE menu_item_id = 45;

-- Find unmatched items (need manual review)
-- SELECT * FROM order_items WHERE match_confidence IS NULL OR match_confidence < 80;

-- Find all addons for an order item
-- SELECT * FROM order_item_addons WHERE order_item_id = 1;

-- Analytics: Count orders by menu item and variant
-- SELECT 
--     mi.name,
--     v.variant_name,
--     COUNT(*) as order_count,
--     SUM(oi.quantity) as total_quantity,
--     SUM(oi.total_price) as total_revenue
-- FROM order_items oi
-- JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
-- JOIN variants v ON oi.variant_id = v.variant_id
-- GROUP BY mi.name, v.variant_name
-- ORDER BY total_revenue DESC;

