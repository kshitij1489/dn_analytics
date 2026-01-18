-- ============================================================================
-- Consolidated Indexes
-- ============================================================================
-- This file defines all indexes for the database schema.
-- Created AFTER tables to ensure all referenced columns exist.
-- ============================================================================

-- ============================================================================
-- 1. RESTAURANTS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_restaurants_petpooja_restid ON restaurants(petpooja_restid);
CREATE INDEX IF NOT EXISTS idx_restaurants_name ON restaurants(name);
CREATE INDEX IF NOT EXISTS idx_restaurants_is_active ON restaurants(is_active);

-- ============================================================================
-- 2. CUSTOMERS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_customers_name_normalized ON customers(name_normalized);
CREATE INDEX IF NOT EXISTS idx_customers_last_order_date ON customers(last_order_date);
CREATE INDEX IF NOT EXISTS idx_customers_phone_not_null ON customers(phone) WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_identity_key ON customers(customer_identity_key);
CREATE INDEX IF NOT EXISTS idx_customers_is_verified ON customers(is_verified);

-- ============================================================================
-- 3. MENU ITEMS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_menu_items_name ON menu_items(name);
CREATE INDEX IF NOT EXISTS idx_menu_items_type ON menu_items(type);
CREATE INDEX IF NOT EXISTS idx_menu_items_name_type ON menu_items(name, type);
CREATE INDEX IF NOT EXISTS idx_menu_items_unverified ON menu_items(is_verified) WHERE is_verified = FALSE;

-- ============================================================================
-- 4. VARIANTS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_variants_variant_name ON variants(variant_name);

-- ============================================================================
-- 5. MENU ITEM VARIANTS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_menu_item_id ON menu_item_variants(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_variant_id ON menu_item_variants(variant_id);
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_unverified ON menu_item_variants(is_verified) WHERE is_verified = FALSE;

-- ============================================================================
-- 6. ORDERS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_orders_petpooja_order_id ON orders(petpooja_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_stream_id ON orders(stream_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_restaurant_id ON orders(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_orders_created_on ON orders(created_on);
CREATE INDEX IF NOT EXISTS idx_orders_occurred_at ON orders(occurred_at);
CREATE INDEX IF NOT EXISTS idx_orders_order_type ON orders(order_type);
CREATE INDEX IF NOT EXISTS idx_orders_order_from ON orders(order_from);
CREATE INDEX IF NOT EXISTS idx_orders_order_status ON orders(order_status);
CREATE INDEX IF NOT EXISTS idx_orders_created_on_type ON orders(created_on, order_type);
CREATE INDEX IF NOT EXISTS idx_orders_customer_created ON orders(customer_id, created_on);

-- ============================================================================
-- 7. ORDER TAXES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_order_taxes_order_id ON order_taxes(order_id);

-- ============================================================================
-- 8. ORDER DISCOUNTS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_order_discounts_order_id ON order_discounts(order_id);

-- ============================================================================
-- 9. ORDER ITEMS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_menu_item_id ON order_items(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_order_items_variant_id ON order_items(variant_id);
CREATE INDEX IF NOT EXISTS idx_order_items_petpooja_itemid ON order_items(petpooja_itemid);
CREATE INDEX IF NOT EXISTS idx_order_items_match_confidence ON order_items(match_confidence) WHERE match_confidence < 80;
CREATE INDEX IF NOT EXISTS idx_order_items_menu_variant ON order_items(menu_item_id, variant_id);

-- ============================================================================
-- 10. ORDER ITEM ADDONS
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_order_item_addons_order_item_id ON order_item_addons(order_item_id);
CREATE INDEX IF NOT EXISTS idx_order_item_addons_menu_item_id ON order_item_addons(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_order_item_addons_group_name ON order_item_addons(group_name);

-- ============================================================================
-- 11. CLUSTERING TABLES
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_menu_items_new_name_type ON menu_items_new(name, type);
CREATE INDEX IF NOT EXISTS idx_variants_new_name ON variants_new(variant_name);
CREATE INDEX IF NOT EXISTS idx_item_mappings_menu_item ON item_mappings(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_item_mappings_variant ON item_mappings(variant_id);

-- ============================================================================
-- 12. MERGE HISTORY
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_merge_history_target ON merge_history(target_id);
CREATE INDEX IF NOT EXISTS idx_merge_history_merged_at ON merge_history(merged_at);
