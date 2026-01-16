-- Migration: 01_clustering_tables.sql
-- Description: Create normalized tables for item clustering service.

-- 1. New Menu Items Table (Normalized)
CREATE TABLE IF NOT EXISTS menu_items_new (
    menu_item_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast lookups by name/type
CREATE INDEX IF NOT EXISTS idx_menu_items_new_name_type ON menu_items_new(name, type);


-- 2. New Variants Table (Normalized)
CREATE TABLE IF NOT EXISTS variants_new (
    variant_id UUID PRIMARY KEY,
    variant_name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for lookups
CREATE INDEX IF NOT EXISTS idx_variants_new_name ON variants_new(variant_name);


-- 3. Item Mappings (The Cluster Table)
-- Maps specific Order Item IDs (PetPooja/Generated) to the Normalized Menu Item + Variant
CREATE TABLE IF NOT EXISTS item_mappings (
    order_item_id VARCHAR(255) PRIMARY KEY,
    menu_item_id UUID NOT NULL REFERENCES menu_items_new(menu_item_id),
    variant_id UUID NOT NULL REFERENCES variants_new(variant_id),
    original_name TEXT, 
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for joins
CREATE INDEX IF NOT EXISTS idx_item_mappings_menu_item ON item_mappings(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_item_mappings_variant ON item_mappings(variant_id);
