-- Migration: Convert TIMESTAMP to TIMESTAMPTZ
-- This ensures that PostgreSQL stores absolute time and handles offsets correctly.

-- 1. Orders Table
ALTER TABLE orders 
    ALTER COLUMN occurred_at TYPE TIMESTAMPTZ,
    ALTER COLUMN created_on TYPE TIMESTAMPTZ,
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

-- 2. Customers Table
ALTER TABLE customers
    ALTER COLUMN first_order_date TYPE TIMESTAMPTZ,
    ALTER COLUMN last_order_date TYPE TIMESTAMPTZ,
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

-- 3. Menu Items Table
ALTER TABLE menu_items
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

-- 4. Order Taxes
ALTER TABLE order_taxes
    ALTER COLUMN created_at TYPE TIMESTAMPTZ;

-- 5. Order Discounts
ALTER TABLE order_discounts
    ALTER COLUMN created_at TYPE TIMESTAMPTZ;

-- 6. Restaurants Table
ALTER TABLE restaurants
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

-- 7. Order Items Table
ALTER TABLE order_items
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

-- 8. Order Item Addons
ALTER TABLE order_item_addons
    ALTER COLUMN created_at TYPE TIMESTAMPTZ;

-- 9. Item Merges (Migration 04)
ALTER TABLE item_merges
    ALTER COLUMN merged_at TYPE TIMESTAMPTZ;

-- 10. Item Clusters (Migration 01)
ALTER TABLE item_clusters
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

ALTER TABLE item_variants
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

ALTER TABLE cluster_mapping
    ALTER COLUMN created_at TYPE TIMESTAMPTZ,
    ALTER COLUMN updated_at TYPE TIMESTAMPTZ;

-- 11. Migration Logs
ALTER TABLE migration_logs
    ALTER COLUMN applied_at TYPE TIMESTAMPTZ;
