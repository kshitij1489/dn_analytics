-- ============================================================================
-- Migration: Switch from Phone-Based to Name-Based Customer Tracking
-- ============================================================================
-- Simplified approach: Drop customers table and recreate with new schema.
-- Then reload all orders from source to repopulate customers.
-- ============================================================================

BEGIN;

-- Step 1: Drop existing customers table
DROP TABLE IF EXISTS customers CASCADE;

-- Step 2: Recreate customers table with new schema
CREATE TABLE customers (
    customer_id SERIAL PRIMARY KEY,
    
    -- Customer identification
    name VARCHAR(255),
    name_normalized VARCHAR(255),
    phone VARCHAR(20),
    address TEXT,
    gstin VARCHAR(50),
    
    -- Metadata
    first_order_date TIMESTAMP,
    last_order_date TIMESTAMP,
    total_orders INTEGER DEFAULT 0,
    total_spent DECIMAL(10,2) DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    UNIQUE(name_normalized)
);

-- Step 3: Create indexes
CREATE INDEX idx_customers_phone ON customers(phone);
CREATE INDEX idx_customers_name ON customers(name);
CREATE INDEX idx_customers_name_normalized ON customers(name_normalized);
CREATE INDEX idx_customers_last_order_date ON customers(last_order_date);
CREATE INDEX idx_customers_phone_not_null ON customers(phone) WHERE phone IS NOT NULL;

-- Step 4: Clear customer_id from orders (will be repopulated on reload)
UPDATE orders SET customer_id = NULL;

COMMIT;

-- Verification
SELECT 'Migration complete. Customers table recreated. Run order reload to repopulate.' as status;
