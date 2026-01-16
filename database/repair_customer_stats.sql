-- ============================================================================
-- Repair Customer Stats
-- ============================================================================
-- Recalculates total_orders and total_spent in the customers table 
-- by using the orders table as the source of truth.
-- ============================================================================

BEGIN;

-- 1. Reset all customer stats to zero
UPDATE customers 
SET total_orders = 0, 
    total_spent = 0;

-- 2. Recalculate stats from the orders table
WITH actual_stats AS (
    SELECT 
        customer_id,
        COUNT(*) as real_count,
        SUM(total) as real_spend
    FROM orders
    WHERE order_status = 'Success'
    GROUP BY customer_id
)
UPDATE customers c
SET 
    total_orders = s.real_count,
    total_spent = s.real_spend
FROM actual_stats s
WHERE c.customer_id = s.customer_id;

-- 3. Update last_order_date and first_order_date just in case
WITH order_dates AS (
    SELECT 
        customer_id,
        MIN(created_on) as first_date,
        MAX(created_on) as last_date
    FROM orders
    GROUP BY customer_id
)
UPDATE customers c
SET 
    first_order_date = d.first_date,
    last_order_date = d.last_date
FROM order_dates d
WHERE c.customer_id = d.customer_id;

COMMIT;

-- Verification query
SELECT COUNT(*) as fixed_customers FROM customers WHERE total_orders > 0;
