-- ============================================================================
-- Menu Items and Variants Schema (Normalized)
-- ============================================================================

-- ============================================================================
-- MENU ITEMS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_items (
    menu_item_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    
    -- Analytics counters
    total_revenue DECIMAL(12,2) DEFAULT 0,
    total_sold INTEGER DEFAULT 0,
    sold_as_item INTEGER DEFAULT 0,
    sold_as_addon INTEGER DEFAULT 0,
    
    -- Verification & Suggestions
    is_verified BOOLEAN DEFAULT FALSE,
    suggestion_id UUID,

    UNIQUE(name, type)
);

-- ============================================================================
-- VARIANTS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS variants (
    variant_id UUID PRIMARY KEY,
    variant_name TEXT NOT NULL UNIQUE,
    description TEXT,
    unit TEXT,
    value DECIMAL(10,2),
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- MENU ITEM VARIANTS (Mapping & Pricing)
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_item_variants (
    order_item_id VARCHAR(255) PRIMARY KEY,
    menu_item_id UUID NOT NULL REFERENCES menu_items(menu_item_id),
    variant_id UUID NOT NULL REFERENCES variants(variant_id),
    price DECIMAL(10,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    addon_eligible BOOLEAN DEFAULT FALSE,
    delivery_eligible BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_menu_items_name ON menu_items(name);
CREATE INDEX IF NOT EXISTS idx_menu_items_type ON menu_items(type);
CREATE INDEX IF NOT EXISTS idx_menu_items_name_type ON menu_items(name, type);
CREATE INDEX IF NOT EXISTS idx_variants_variant_name ON variants(variant_name);
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_menu_item_id ON menu_item_variants(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_variant_id ON menu_item_variants(variant_id);

-- ============================================================================
-- COMMENTS
-- ============================================================================
COMMENT ON TABLE menu_items IS 'Stores base menu items (products).';
COMMENT ON TABLE variants IS 'Stores variant definitions shared across items.';
COMMENT ON TABLE menu_item_variants IS 'Junction table linking PetPooja IDs to normalized items/variants.';
