I am working on a PostgreSQL database for a restaurant analytics system.
Below is the full schema of my database. Please use this schema context to answer all my future questions about writing SQL queries.

## Context
- The database stores restaurant orders from PetPooja (POS system).
- It tracks Customers, Menu Items, Orders, and Order Items.
- We have a specific focus on "Menu Clustering" (normalizing raw item names to clean menu items).

## Database Schema (`schema.sql`)
```sql
-- ============================================================================
-- 1. RESTAURANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id SERIAL PRIMARY KEY,
    petpooja_restid VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    address TEXT,
    contact_information VARCHAR(50),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 2. CUSTOMERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS customers (
    customer_id SERIAL PRIMARY KEY,
    customer_identity_key VARCHAR(80) UNIQUE,
    name VARCHAR(255),
    name_normalized VARCHAR(255),
    phone VARCHAR(20),
    address TEXT,
    gstin VARCHAR(50),
    first_order_date TIMESTAMPTZ,
    last_order_date TIMESTAMPTZ,
    total_orders INTEGER DEFAULT 0,            
    total_spent DECIMAL(10,2) DEFAULT 0,      
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 3. MENU ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_items (
    menu_item_id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    total_revenue DECIMAL(12,2) DEFAULT 0,
    total_sold INTEGER DEFAULT 0,
    sold_as_item INTEGER DEFAULT 0,
    sold_as_addon INTEGER DEFAULT 0,
    is_verified BOOLEAN DEFAULT FALSE,
    suggestion_id UUID REFERENCES menu_items(menu_item_id),
    UNIQUE(name, type)
);

-- ============================================================================
-- 4. VARIANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS variants (
    variant_id UUID PRIMARY KEY,
    variant_name TEXT NOT NULL UNIQUE,
    description TEXT,
    unit TEXT,
    value DECIMAL(10,2),
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 5. MENU ITEM VARIANTS (Mapping & Pricing)
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_item_variants (
    order_item_id VARCHAR(255) PRIMARY KEY,
    menu_item_id UUID NOT NULL REFERENCES menu_items(menu_item_id),
    variant_id UUID NOT NULL REFERENCES variants(variant_id),
    price DECIMAL(10,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    addon_eligible BOOLEAN DEFAULT FALSE,
    delivery_eligible BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 6. ORDERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS orders (
    order_id SERIAL PRIMARY KEY,
    petpooja_order_id INTEGER NOT NULL UNIQUE,
    stream_id INTEGER NOT NULL UNIQUE,
    event_id VARCHAR(255) NOT NULL UNIQUE,
    aggregate_id VARCHAR(100),
    customer_id INTEGER REFERENCES customers(customer_id),
    restaurant_id INTEGER REFERENCES restaurants(restaurant_id),
    occurred_at TIMESTAMPTZ NOT NULL,
    created_on TIMESTAMPTZ NOT NULL,
    order_type VARCHAR(50) NOT NULL,
    order_from VARCHAR(100) NOT NULL,
    sub_order_type VARCHAR(100),
    order_from_id VARCHAR(100),
    order_status VARCHAR(50) NOT NULL,
    biller VARCHAR(100),
    assignee VARCHAR(255),
    table_no VARCHAR(50),
    token_no VARCHAR(50),
    no_of_persons INTEGER DEFAULT 0,
    customer_invoice_id VARCHAR(100),
    core_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    tax_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    discount_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0,
    packaging_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    service_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    round_off DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) NOT NULL DEFAULT 0,
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 7. ORDER TAXES
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_taxes (
    order_tax_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    tax_title VARCHAR(100) NOT NULL,
    tax_rate DECIMAL(5,2) NOT NULL,
    tax_type VARCHAR(10) NOT NULL,
    tax_amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 8. ORDER DISCOUNTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_discounts (
    order_discount_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    discount_title VARCHAR(255) NOT NULL,
    discount_type VARCHAR(10) NOT NULL,
    discount_rate DECIMAL(5,2) DEFAULT 0,
    discount_amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 9. ORDER ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    menu_item_id UUID REFERENCES menu_items(menu_item_id),
    variant_id UUID REFERENCES variants(variant_id),
    petpooja_itemid BIGINT,
    itemcode VARCHAR(100),
    name_raw VARCHAR(500) NOT NULL,
    category_name VARCHAR(255),
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price DECIMAL(10,2) NOT NULL,
    total_price DECIMAL(10,2) NOT NULL,
    tax_amount DECIMAL(10,2) DEFAULT 0,
    discount_amount DECIMAL(10,2) DEFAULT 0,
    specialnotes TEXT,
    sap_code VARCHAR(100),
    vendoritemcode VARCHAR(100),
    match_confidence DECIMAL(5,2),
    match_method VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 10. ORDER ITEM ADDONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_item_addons (
    order_item_addon_id SERIAL PRIMARY KEY,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id) ON DELETE CASCADE,
    menu_item_id UUID REFERENCES menu_items(menu_item_id),
    variant_id UUID REFERENCES variants(variant_id),
    petpooja_addonid VARCHAR(100),
    name_raw VARCHAR(255) NOT NULL,
    group_name VARCHAR(100),
    quantity INTEGER NOT NULL DEFAULT 1,
    price DECIMAL(10,2) NOT NULL,
    addon_sap_code VARCHAR(100),
    match_confidence DECIMAL(5,2),
    match_method VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

## Views (`views.sql`)
```sql
CREATE OR REPLACE VIEW menu_items_summary_view AS
SELECT 
    mi.menu_item_id,
    mi.name,
    mi.type,
    mi.total_revenue,
    mi.total_sold,
    mi.sold_as_item,
    mi.sold_as_addon,
    mi.is_active
FROM menu_items mi;
```

When I ask for queries, please:
1. Prefer standard PostgreSQL syntax.
2. Consider that `orders.created_on` and `occurred_at` are TIMESTAMPTZ and should be used for time-based analysis (checking specifically for IST/Asia/Kolkata timezone if needed).
3. `order_items` and `order_item_addons` link to `menu_items` via `menu_item_id`.
