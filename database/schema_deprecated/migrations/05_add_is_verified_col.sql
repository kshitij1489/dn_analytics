-- Migration: Add is_verified column to customers
-- This flag helps identify trackable customers (Phone or Name+Address)

-- 1. Add the column
ALTER TABLE customers ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;

-- 2. Create index for fast analytics
CREATE INDEX IF NOT EXISTS idx_customers_is_verified ON customers(is_verified);

-- 3. Update existing data
-- In our system, 'phone:' and 'addr:' keys represent verified customers.
-- 'anon:' keys represent anonymous/untrackable customers.
UPDATE customers 
SET is_verified = TRUE 
WHERE customer_identity_key LIKE 'phone:%' 
   OR customer_identity_key LIKE 'addr:%';

-- 4. Ensure new additions default to FALSE unless explicitly set (handled by app)
COMMENT ON COLUMN customers.is_verified IS 'True if the customer has a valid phone or name+address for loyalty tracking.';
