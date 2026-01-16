-- Migration: Add suggestion_id to menu_items for fuzzy matching
ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS suggestion_id UUID REFERENCES menu_items(menu_item_id);
