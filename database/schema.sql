-- ============================================================================
-- Consolidated Database Schema
-- ============================================================================
-- This file defines the complete database schema including all tables and 
-- relationships, incorporating all previous migration changes.
--
-- Tables:
-- 1. restaurants
-- 2. customers
-- 3. menu_items
-- 4. variants
-- 5. menu_item_variants
-- 6. orders
-- 7. order_taxes
-- 8. order_discounts
-- 9. order_items
-- 10. order_item_addons
-- 11. menu_items_new
-- 12. variants_new
-- 13. item_mappings
-- 14. merge_history
-- ============================================================================

-- ============================================================================
-- 1. RESTAURANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id SERIAL PRIMARY KEY,
    
    -- Restaurant identification
    petpooja_restid VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    address TEXT,
    contact_information VARCHAR(50),
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE restaurants IS 'Stores restaurant information from PetPooja. Currently single restaurant (Dach & Nona), but designed for multi-location support.';

-- ============================================================================
-- 2. CUSTOMERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    customer_identity_key VARCHAR(80),
    
    -- Customer identification
    name VARCHAR(255),                         -- Customer name (original)
    name_normalized VARCHAR(255),              -- Normalized name (lowercase, trimmed)
    phone VARCHAR(20),
    address TEXT,
    gstin VARCHAR(50),
    
    -- Metadata
    first_order_date TIMESTAMPTZ,              -- Migrated to TIMESTAMPTZ
    last_order_date TIMESTAMPTZ,               -- Migrated to TIMESTAMPTZ
    
    -- Denormalized stats
    total_orders INTEGER DEFAULT 0,            
    total_spent DECIMAL(10,2) DEFAULT 0,      
    
    -- Verification (Added from migration 05)
    is_verified BOOLEAN DEFAULT FALSE,         -- True if Phone or Name+Address exists
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    UNIQUE(customer_identity_key)
);

COMMENT ON TABLE customers IS 'Stores customer information from PetPooja orders. Customers are deduplicated using normalized name as primary identifier.';
COMMENT ON COLUMN customers.is_verified IS 'True if the customer has a valid phone or name+address for loyalty tracking.';

-- ============================================================================
-- 3. MENU ITEMS
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
    sold_as_item INTEGER DEFAULT 0,            -- Added from migration 20260113
    sold_as_addon INTEGER DEFAULT 0,           -- Added from migration 20260113
    
    -- Verification & Suggestions
    is_verified BOOLEAN DEFAULT FALSE,         -- Added from migration 02
    suggestion_id UUID REFERENCES menu_items(menu_item_id), -- Added from migration 03

    UNIQUE(name, type)
);

COMMENT ON TABLE menu_items IS 'Stores base menu items (products).';

-- ============================================================================
-- 4. VARIANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS variants (
    variant_id UUID PRIMARY KEY,
    variant_name TEXT NOT NULL UNIQUE,
    description TEXT,
    unit TEXT,
    value DECIMAL(10,2),
    is_verified BOOLEAN DEFAULT FALSE,         -- Added from migration 02
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE variants IS 'Stores variant definitions shared across items.';

-- ============================================================================
-- 5. MENU ITEM VARIANTS (Mapping & Pricing)
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_item_variants (
    order_item_id VARCHAR(255) PRIMARY KEY,
    menu_item_id UUID NOT NULL REFERENCES menu_items(menu_item_id),
    variant_id UUID NOT NULL REFERENCES variants(variant_id),
    price DECIMAL(10,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    addon_eligible BOOLEAN DEFAULT FALSE,
    delivery_eligible BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,         -- Added from migration 02
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE menu_item_variants IS 'Junction table linking PetPooja IDs to normalized items/variants.';

-- ============================================================================
-- 6. ORDERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    
    -- PetPooja identifiers
    petpooja_order_id INTEGER NOT NULL UNIQUE,
    stream_id INTEGER NOT NULL UNIQUE,
    event_id VARCHAR(255) NOT NULL UNIQUE,
    aggregate_id VARCHAR(100),
    
    -- Relationships (Foreign Keys added inline)
    customer_id INTEGER REFERENCES customers(customer_id),
    restaurant_id INTEGER REFERENCES restaurants(restaurant_id),
    
    -- Order metadata
    occurred_at TIMESTAMPTZ NOT NULL,
    created_on TIMESTAMPTZ NOT NULL,
    
    -- Order type and source
    order_type VARCHAR(50) NOT NULL,
    order_from VARCHAR(100) NOT NULL,
    sub_order_type VARCHAR(100),
    order_from_id VARCHAR(100),
    
    -- Order status and processing
    order_status VARCHAR(50) NOT NULL,
    biller VARCHAR(100),
    assignee VARCHAR(255),
    
    -- Dine-in specific fields
    table_no VARCHAR(50),
    token_no VARCHAR(50),
    no_of_persons INTEGER DEFAULT 0,
    
    -- Customer invoice
    customer_invoice_id VARCHAR(100),
    
    -- Financial breakdown
    core_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    tax_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    discount_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0,
    packaging_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    service_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    round_off DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) NOT NULL DEFAULT 0,
    
    -- Additional information
    comment TEXT,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    CHECK (core_total >= 0),
    CHECK (tax_total >= 0),
    CHECK (discount_total >= 0),
    CHECK (delivery_charges >= 0),
    CHECK (packaging_charge >= 0),
    CHECK (service_charge >= 0),
    CHECK (total >= 0),
    CHECK (no_of_persons >= 0)
);

COMMENT ON TABLE orders IS 'Stores order-level information from PetPooja webhook payloads.';

-- ============================================================================
-- 7. ORDER TAXES
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_taxes (
    order_tax_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    tax_title VARCHAR(100) NOT NULL,
    tax_rate DECIMAL(5,2) NOT NULL,
    tax_type VARCHAR(10) NOT NULL,
    tax_amount DECIMAL(10,2) NOT NULL CHECK (tax_amount >= 0),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE order_taxes IS 'Stores individual tax components for each order.';

-- ============================================================================
-- 8. ORDER DISCOUNTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_discounts (
    order_discount_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    discount_title VARCHAR(255) NOT NULL,
    discount_type VARCHAR(10) NOT NULL,
    discount_rate DECIMAL(5,2) DEFAULT 0,
    discount_amount DECIMAL(10,2) NOT NULL CHECK (discount_amount >= 0),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE order_discounts IS 'Stores individual discount components for each order.';

-- ============================================================================
-- 9. ORDER ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    
    -- Menu item mapping (FKs added inline)
    menu_item_id UUID REFERENCES menu_items(menu_item_id),
    variant_id UUID REFERENCES variants(variant_id),
    
    -- PetPooja identifiers
    petpooja_itemid BIGINT,
    itemcode VARCHAR(100),
    
    -- Raw data preservation
    name_raw VARCHAR(500) NOT NULL,
    category_name VARCHAR(255),
    
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
    
    -- Matching confidence
    match_confidence DECIMAL(5,2) CHECK (
        match_confidence IS NULL OR 
        (match_confidence >= 0 AND match_confidence <= 100)
    ),
    match_method VARCHAR(50),
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE order_items IS 'Stores individual items within each order, linked to menu items and variants.';

-- ============================================================================
-- 10. ORDER ITEM ADDONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_item_addons (
    order_item_addon_id SERIAL PRIMARY KEY,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id) ON DELETE CASCADE,
    
    -- Addon mapping (addons are also menu items)
    menu_item_id UUID REFERENCES menu_items(menu_item_id),
    variant_id UUID REFERENCES variants(variant_id),
    
    -- PetPooja identifiers
    petpooja_addonid VARCHAR(100),
    
    -- Raw data
    name_raw VARCHAR(255) NOT NULL,
    group_name VARCHAR(100),
    
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
    match_method VARCHAR(50),
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE order_item_addons IS 'Stores addons attached to order items.';

-- ============================================================================
-- 11. MENU ITEMS NEW (Clustering)
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_items_new (
    menu_item_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 12. VARIANTS NEW (Clustering)
-- ============================================================================
CREATE TABLE IF NOT EXISTS variants_new (
    variant_id UUID PRIMARY KEY,
    variant_name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 13. ITEM MAPPINGS (Clustering)
-- ============================================================================
CREATE TABLE IF NOT EXISTS item_mappings (
    order_item_id VARCHAR(255) PRIMARY KEY,
    menu_item_id UUID NOT NULL REFERENCES menu_items_new(menu_item_id),
    variant_id UUID NOT NULL REFERENCES variants_new(variant_id),
    original_name TEXT, 
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 14. MERGE HISTORY
-- ============================================================================
CREATE TABLE IF NOT EXISTS merge_history (
    merge_id SERIAL PRIMARY KEY,
    source_id UUID NOT NULL,
    target_id UUID NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    affected_order_items JSONB NOT NULL,
    merged_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
