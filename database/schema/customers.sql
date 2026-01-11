-- ============================================================================
-- Customers Table Schema
-- ============================================================================
-- This schema defines the customers table for storing customer information.
-- Customers are deduplicated based on customer name (normalized).
--
-- ============================================================================

-- ============================================================================
-- CUSTOMERS TABLE
-- ============================================================================
-- Stores customer information from PetPooja orders.
-- Customers are deduplicated using normalized name as primary identifier.

CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    customer_identity_key VARCHAR(80),
    -- Customer identification
    name VARCHAR(255),                         -- Customer name (original, as provided)
    name_normalized VARCHAR(255),              -- Normalized name (lowercase, trimmed) for deduplication
    phone VARCHAR(20),                          -- Phone number (optional metadata)
    address TEXT,                              -- Delivery address (may be empty for dine-in/POS)
    gstin VARCHAR(50),                         -- GST identification number (if available)
    
    -- Metadata
    first_order_date TIMESTAMP,                -- Date of first order (for customer analytics)
    last_order_date TIMESTAMP,                 -- Date of most recent order
    total_orders INTEGER DEFAULT 0,            -- Total number of orders (denormalized for performance)
    total_spent DECIMAL(10,2) DEFAULT 0,      -- Total amount spent (denormalized for performance)
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    UNIQUE(customer_identity_key)  -- Identity key is unique identifier
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_customers_phone 
    ON customers(phone);

CREATE INDEX IF NOT EXISTS idx_customers_name 
    ON customers(name);

CREATE INDEX IF NOT EXISTS idx_customers_name_normalized 
    ON customers(name_normalized);

CREATE INDEX IF NOT EXISTS idx_customers_last_order_date 
    ON customers(last_order_date);

-- Partial index for customers with phone numbers (for phone-based queries)
CREATE INDEX IF NOT EXISTS idx_customers_phone_not_null 
    ON customers(phone) 
    WHERE phone IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_identity_key
    ON customers(customer_identity_key);

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON TABLE customers IS 
    'Stores customer information from PetPooja orders. '
    'Customers are deduplicated using normalized name as primary identifier.';

COMMENT ON COLUMN customers.name IS 
    'Customer name as provided in the order. Original casing and spacing preserved.';

COMMENT ON COLUMN customers.name_normalized IS 
    'Normalized version of customer name (lowercase, trimmed) used for deduplication. '
    'This ensures "John Doe", "john doe", and " John Doe " are treated as the same customer.';

COMMENT ON COLUMN customers.phone IS 
    'Phone number (optional). Multiple customers may share the same phone number, '
    'or have no phone number at all (NULL).';

COMMENT ON COLUMN customers.address IS 
    'Delivery address. May be empty for dine-in or POS orders.';

COMMENT ON COLUMN customers.total_orders IS 
    'Denormalized count of total orders. Updated via application logic.';

COMMENT ON COLUMN customers.total_spent IS 
    'Denormalized total amount spent. Updated via application logic.';

-- ============================================================================
-- EXAMPLE QUERIES
-- ============================================================================

-- Find customer by name (case-insensitive)
-- SELECT * FROM customers WHERE name_normalized = LOWER(TRIM('John Doe'));

-- Get top customers by total spent
-- SELECT 
--     customer_id,
--     name,
--     phone,
--     total_orders,
--     total_spent,
--     first_order_date,
--     last_order_date
-- FROM customers
-- ORDER BY total_spent DESC
-- LIMIT 10;

-- Get customers who haven't ordered in last 30 days
-- SELECT * FROM customers
-- WHERE last_order_date < NOW() - INTERVAL '30 days'
-- ORDER BY last_order_date DESC;

-- Get customer order history
-- SELECT 
--     c.name,
--     c.phone,
--     o.order_id,
--     o.created_on,
--     o.total,
--     o.order_type
-- FROM customers c
-- JOIN orders o ON c.customer_id = o.customer_id
-- WHERE c.customer_id = :customer_id
-- ORDER BY o.created_on DESC;

-- Count customers by order frequency
-- SELECT 
--     CASE 
--         WHEN total_orders = 1 THEN '1 order'
--         WHEN total_orders BETWEEN 2 AND 5 THEN '2-5 orders'
--         WHEN total_orders BETWEEN 6 AND 10 THEN '6-10 orders'
--         ELSE '10+ orders'
--     END as order_frequency,
--     COUNT(*) as customer_count
-- FROM customers
-- GROUP BY order_frequency
-- ORDER BY customer_count DESC;
