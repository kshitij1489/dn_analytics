-- Add is_verified flags to menu tables
-- Existing items are verified by default (since they came from clean sync)

-- 1. Menu Items
ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;
UPDATE menu_items SET is_verified = TRUE WHERE is_verified IS NULL;

-- 2. Variants
ALTER TABLE variants ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;
UPDATE variants SET is_verified = TRUE WHERE is_verified IS NULL;

-- 3. Menu Item Variants (Mappings)
ALTER TABLE menu_item_variants ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;
UPDATE menu_item_variants SET is_verified = TRUE WHERE is_verified IS NULL;

-- Add indexes for performance on unverified checks
CREATE INDEX IF NOT EXISTS idx_menu_items_unverified ON menu_items(is_verified) WHERE is_verified = FALSE;
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_unverified ON menu_item_variants(is_verified) WHERE is_verified = FALSE;
