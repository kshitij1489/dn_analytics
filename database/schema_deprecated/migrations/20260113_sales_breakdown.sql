-- Migration: Add Sales Breakdown Columns
-- Goal: Split total_sold into sold_as_item and sold_as_addon

-- 1. Add columns to menu_items
ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS sold_as_item INTEGER DEFAULT 0;
ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS sold_as_addon INTEGER DEFAULT 0;

-- 2. Create temporary aggregation tables for backfill
-- We filter for 'Success' orders only to match current total_sold logic

-- Aggregate from order_items
WITH item_counts AS (
    SELECT 
        oi.menu_item_id, 
        SUM(oi.quantity) as count
    FROM order_items oi
    JOIN orders o ON oi.order_id = o.order_id
    WHERE o.order_status = 'Success'
    GROUP BY oi.menu_item_id
),
-- Aggregate from order_item_addons
addon_counts AS (
    SELECT 
        oia.menu_item_id, 
        SUM(oia.quantity) as count
    FROM order_item_addons oia
    JOIN order_items oi ON oia.order_item_id = oi.order_item_id
    JOIN orders o ON oi.order_id = o.order_id
    WHERE o.order_status = 'Success'
    GROUP BY oia.menu_item_id
)
-- 3. Update menu_items with the counts
UPDATE menu_items mi
SET 
    sold_as_item = COALESCE(ic.count, 0),
    sold_as_addon = COALESCE(ac.count, 0)
FROM (SELECT menu_item_id FROM menu_items) mi_list
LEFT JOIN item_counts ic ON mi_list.menu_item_id = ic.menu_item_id
LEFT JOIN addon_counts ac ON mi_list.menu_item_id = ac.menu_item_id
WHERE mi.menu_item_id = mi_list.menu_item_id;

-- 4. Verification Check: Do these sum to total_sold?
-- We can check for any discrepancies
-- SELECT menu_item_id, name, total_sold, (sold_as_item + sold_as_addon) as calculated_total
-- FROM menu_items
-- WHERE total_sold != (sold_as_item + sold_as_addon);
