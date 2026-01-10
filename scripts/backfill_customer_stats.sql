
-- Backfill customer statistics (total_orders, total_spent)
-- derived from the orders table.

BEGIN;

-- Update customers table with aggregated data from orders
UPDATE customers c
SET 
    total_orders = sub.order_count,
    total_spent = sub.total_spent,
    last_order_date = sub.latest_order,
    updated_at = CURRENT_TIMESTAMP
FROM (
    SELECT 
        customer_id, 
        COUNT(*) as order_count, 
        SUM(total) as total_spent,
        MAX(created_on) as latest_order
    FROM orders
    WHERE customer_id IS NOT NULL
    GROUP BY customer_id
) sub
WHERE c.customer_id = sub.customer_id;

COMMIT;

-- Verify results
SELECT 
    COUNT(*) as customers_updated,
    SUM(total_orders) as total_orders_tracked,
    SUM(total_spent) as total_revenue_tracked
FROM customers;
