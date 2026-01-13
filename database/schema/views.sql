-- ============================================================================
-- Utility Views
-- ============================================================================

-- A summary view of menu items.
-- Useful for displaying in the UI.
CREATE OR REPLACE VIEW menu_items_summary_view AS
SELECT 
    mi.menu_item_id,
    mi.name,
    mi.type,
    mi.total_revenue,
    mi.total_sold,
    mi.is_active
FROM menu_items mi;
