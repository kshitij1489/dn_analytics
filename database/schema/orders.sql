-- ============================================================================
-- Orders Table Schema
-- ============================================================================
-- This schema defines the orders table for storing order data from PetPooja.
-- Orders link to customers, restaurants, and contain order items.
--
-- Dependencies:
--   - customers table (for customer_id FK)
--   - restaurants table (for restaurant_id FK)
--
-- ============================================================================

-- ============================================================================
-- ORDERS TABLE
-- ============================================================================
-- Stores order-level information from PetPooja webhook payloads.
-- Preserves both normalized structure and raw PetPooja identifiers.

CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    
    -- PetPooja identifiers (for reconciliation and incremental updates)
    petpooja_order_id INTEGER NOT NULL UNIQUE,  -- From Order.orderID
    stream_id INTEGER NOT NULL UNIQUE,         -- From top-level stream_id (for incremental updates)
    event_id VARCHAR(255) NOT NULL UNIQUE,      -- From top-level event_id (UUID)
    aggregate_id VARCHAR(100),                  -- From top-level aggregate_id (usually same as orderID)
    
    -- Customer relationship
    customer_id INTEGER,
    -- FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    -- Note: Add FK constraint after customers table is created
    
    -- Restaurant relationship
    restaurant_id INTEGER,
    -- FOREIGN KEY (restaurant_id) REFERENCES restaurants(restaurant_id),
    -- Note: Add FK constraint after restaurants table is created
    
    -- Order metadata
    occurred_at TIMESTAMP NOT NULL,             -- From top-level occurred_at
    created_on TIMESTAMP NOT NULL,             -- From Order.created_on (order creation time)
    
    -- Order type and source
    order_type VARCHAR(50) NOT NULL,            -- Delivery, Dine In, Takeaway
    order_from VARCHAR(100) NOT NULL,          -- Zomato, Swiggy, POS, etc.
    sub_order_type VARCHAR(100),               -- Zomato, Swiggy, AC, etc.
    order_from_id VARCHAR(100),                -- Platform-specific order ID
    
    -- Order status and processing
    order_status VARCHAR(50) NOT NULL,         -- Success, Cancelled, Refunded, etc.
    biller VARCHAR(100),                       -- Zomato, POS, Swiggy, etc.
    assignee VARCHAR(255),                     -- Staff member assigned (if any)
    
    -- Dine-in specific fields
    table_no VARCHAR(50),                      -- Table number for dine-in orders
    token_no VARCHAR(50),                       -- Token number for dine-in orders
    no_of_persons INTEGER DEFAULT 0,           -- Number of persons (dine-in)
    
    -- Customer invoice
    customer_invoice_id VARCHAR(100),          -- From Order.customer_invoice_id
    
    -- Financial breakdown
    core_total DECIMAL(10,2) NOT NULL DEFAULT 0,      -- Subtotal before taxes/discounts
    tax_total DECIMAL(10,2) NOT NULL DEFAULT 0,       -- Total tax amount
    discount_total DECIMAL(10,2) NOT NULL DEFAULT 0,  -- Total discount amount
    delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0,-- Delivery charges
    packaging_charge DECIMAL(10,2) NOT NULL DEFAULT 0,-- Packaging charges
    service_charge DECIMAL(10,2) NOT NULL DEFAULT 0,   -- Service charges
    round_off DECIMAL(10,2) DEFAULT 0,                 -- Rounding adjustment
    total DECIMAL(10,2) NOT NULL DEFAULT 0,            -- Final total amount
    
    -- Additional information
    comment TEXT,                               -- Order comments/notes
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    CHECK (core_total >= 0),
    CHECK (tax_total >= 0),
    CHECK (discount_total >= 0),
    CHECK (delivery_charges >= 0),
    CHECK (packaging_charge >= 0),
    CHECK (service_charge >= 0),
    CHECK (total >= 0),
    CHECK (no_of_persons >= 0)
);

-- ============================================================================
-- ORDER TAXES TABLE
-- ============================================================================
-- Stores individual tax components for each order (e.g., CGST, SGST separately).
-- Allows detailed tax analysis and reporting.

CREATE TABLE IF NOT EXISTS order_taxes (
    order_tax_id SERIAL PRIMARY KEY,
    
    -- Order relationship
    order_id INTEGER NOT NULL,
    -- FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    -- Note: Add FK constraint after orders table is created
    
    -- Tax details
    tax_title VARCHAR(100) NOT NULL,           -- e.g., "CGST@9", "SGST@9"
    tax_rate DECIMAL(5,2) NOT NULL,            -- Tax rate (percentage)
    tax_type VARCHAR(10) NOT NULL,             -- "P" for Percentage, "F" for Fixed
    tax_amount DECIMAL(10,2) NOT NULL CHECK (tax_amount >= 0),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- ORDER DISCOUNTS TABLE
-- ============================================================================
-- Stores individual discount components for each order.
-- Allows detailed discount analysis and reporting.

CREATE TABLE IF NOT EXISTS order_discounts (
    order_discount_id SERIAL PRIMARY KEY,
    
    -- Order relationship
    order_id INTEGER NOT NULL,
    -- FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
    -- Note: Add FK constraint after orders table is created
    
    -- Discount details
    discount_title VARCHAR(255) NOT NULL,      -- e.g., "Special Discount", "Promo Code"
    discount_type VARCHAR(10) NOT NULL,        -- "P" for Percentage, "F" for Fixed
    discount_rate DECIMAL(5,2) DEFAULT 0,      -- Discount rate (if percentage)
    discount_amount DECIMAL(10,2) NOT NULL CHECK (discount_amount >= 0),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Orders Indexes
CREATE INDEX IF NOT EXISTS idx_orders_petpooja_order_id 
    ON orders(petpooja_order_id);

CREATE INDEX IF NOT EXISTS idx_orders_stream_id 
    ON orders(stream_id);

CREATE INDEX IF NOT EXISTS idx_orders_customer_id 
    ON orders(customer_id);

CREATE INDEX IF NOT EXISTS idx_orders_restaurant_id 
    ON orders(restaurant_id);

CREATE INDEX IF NOT EXISTS idx_orders_created_on 
    ON orders(created_on);

CREATE INDEX IF NOT EXISTS idx_orders_occurred_at 
    ON orders(occurred_at);

CREATE INDEX IF NOT EXISTS idx_orders_order_type 
    ON orders(order_type);

CREATE INDEX IF NOT EXISTS idx_orders_order_from 
    ON orders(order_from);

CREATE INDEX IF NOT EXISTS idx_orders_order_status 
    ON orders(order_status);

-- Composite indexes for common queries
CREATE INDEX IF NOT EXISTS idx_orders_created_on_type 
    ON orders(created_on, order_type);

CREATE INDEX IF NOT EXISTS idx_orders_customer_created 
    ON orders(customer_id, created_on);

-- Order Taxes Indexes
CREATE INDEX IF NOT EXISTS idx_order_taxes_order_id 
    ON order_taxes(order_id);

-- Order Discounts Indexes
CREATE INDEX IF NOT EXISTS idx_order_discounts_order_id 
    ON order_discounts(order_id);

-- ============================================================================
-- FOREIGN KEY CONSTRAINTS
-- ============================================================================
-- Uncomment these after creating the referenced tables

-- ALTER TABLE orders
--     ADD CONSTRAINT fk_orders_customer_id 
--     FOREIGN KEY (customer_id) REFERENCES customers(customer_id);

-- ALTER TABLE orders
--     ADD CONSTRAINT fk_orders_restaurant_id 
--     FOREIGN KEY (restaurant_id) REFERENCES restaurants(restaurant_id);

-- ALTER TABLE order_taxes
--     ADD CONSTRAINT fk_order_taxes_order_id 
--     FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE;

-- ALTER TABLE order_discounts
--     ADD CONSTRAINT fk_order_discounts_order_id 
--     FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE;

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON TABLE orders IS 
    'Stores order-level information from PetPooja webhook payloads. '
    'Preserves both normalized structure and raw PetPooja identifiers.';

COMMENT ON COLUMN orders.stream_id IS 
    'Stream ID from PetPooja webhook. Used for incremental updates. '
    'Always fetch orders with stream_id > last_processed_stream_id.';

COMMENT ON COLUMN orders.petpooja_order_id IS 
    'PetPooja order ID (from Order.orderID). Unique identifier from PetPooja system.';

COMMENT ON COLUMN orders.order_type IS 
    'Type of order: Delivery, Dine In, or Takeaway.';

COMMENT ON COLUMN orders.order_from IS 
    'Source platform: Zomato, Swiggy, POS, etc.';

COMMENT ON COLUMN orders.sub_order_type IS 
    'Sub-type from platform (e.g., "Zomato", "Swiggy", "AC" for air-conditioned section).';

COMMENT ON COLUMN orders.order_status IS 
    'Order status: Success, Cancelled, Refunded, etc.';

COMMENT ON COLUMN orders.core_total IS 
    'Subtotal before taxes, discounts, and charges.';

COMMENT ON COLUMN orders.total IS 
    'Final total amount: core_total + tax_total - discount_total + delivery_charges + packaging_charge + service_charge + round_off';

COMMENT ON TABLE order_taxes IS 
    'Stores individual tax components for each order (e.g., CGST, SGST separately). '
    'Allows detailed tax analysis and reporting.';

COMMENT ON TABLE order_discounts IS 
    'Stores individual discount components for each order. '
    'Allows detailed discount analysis and reporting.';

-- ============================================================================
-- EXAMPLE QUERIES
-- ============================================================================

-- Get all orders for a specific date range
-- SELECT * FROM orders 
-- WHERE created_on >= '2025-06-01' AND created_on < '2025-07-01'
-- ORDER BY created_on DESC;

-- Get orders by type
-- SELECT order_type, COUNT(*) as count, SUM(total) as revenue
-- FROM orders
-- WHERE order_status = 'Success'
-- GROUP BY order_type;

-- Get orders by platform
-- SELECT order_from, COUNT(*) as count, SUM(total) as revenue
-- FROM orders
-- WHERE order_status = 'Success'
-- GROUP BY order_from
-- ORDER BY revenue DESC;

-- Get order with taxes and discounts
-- SELECT 
--     o.order_id,
--     o.total,
--     o.tax_total,
--     o.discount_total,
--     (SELECT SUM(tax_amount) FROM order_taxes WHERE order_id = o.order_id) as calculated_tax,
--     (SELECT SUM(discount_amount) FROM order_discounts WHERE order_id = o.order_id) as calculated_discount
-- FROM orders o
-- WHERE o.order_id = 110;

-- Get daily revenue
-- SELECT 
--     DATE(created_on) as order_date,
--     COUNT(*) as order_count,
--     SUM(total) as revenue,
--     AVG(total) as avg_order_value
-- FROM orders
-- WHERE order_status = 'Success'
-- GROUP BY DATE(created_on)
-- ORDER BY order_date DESC;

-- Get orders needing incremental update (since last stream_id)
-- SELECT * FROM orders 
-- WHERE stream_id > :last_processed_stream_id
-- ORDER BY stream_id ASC;

-- Get customer order history
-- SELECT * FROM orders 
-- WHERE customer_id = :customer_id
-- ORDER BY created_on DESC;

-- Validate order totals
-- SELECT 
--     order_id,
--     total,
--     (core_total + tax_total - discount_total + delivery_charges + packaging_charge + service_charge + round_off) as calculated_total,
--     ABS(total - (core_total + tax_total - discount_total + delivery_charges + packaging_charge + service_charge + round_off)) as difference
-- FROM orders
-- WHERE ABS(total - (core_total + tax_total - discount_total + delivery_charges + packaging_charge + service_charge + round_off)) > 0.01;

