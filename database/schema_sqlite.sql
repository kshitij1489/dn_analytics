-- ============================================================================
-- Consolidated Database Schema (SQLite Version)
-- ============================================================================

-- Enable Foreign Keys and WAL Mode
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================================
-- 1. RESTAURANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Restaurant identification
    petpooja_restid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    address TEXT,
    contact_information TEXT,
    
    -- Status
    is_active BOOLEAN DEFAULT 1,
    
    -- Timestamps
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 2. CUSTOMERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_identity_key TEXT,
    
    -- Customer identification
    name TEXT,
    name_normalized TEXT,
    phone TEXT,
    address TEXT,
    gstin TEXT,
    
    -- Metadata
    first_order_date TEXT,
    last_order_date TEXT,
    
    -- Denormalized stats
    total_orders INTEGER DEFAULT 0,            
    total_spent DECIMAL(10,2) DEFAULT 0,      
    
    -- Verification
    is_verified BOOLEAN DEFAULT 0,
    
    -- Timestamps
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(customer_identity_key)
);

-- ============================================================================
-- 3. MENU ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_items (
    menu_item_id TEXT PRIMARY KEY, -- UUID as TEXT
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    
    -- Analytics counters
    total_revenue DECIMAL(12,2) DEFAULT 0,
    total_sold INTEGER DEFAULT 0,
    sold_as_item INTEGER DEFAULT 0,
    sold_as_addon INTEGER DEFAULT 0,
    
    -- Verification & Suggestions
    is_verified BOOLEAN DEFAULT 0,
    suggestion_id TEXT REFERENCES menu_items(menu_item_id),
    
    UNIQUE(name, type)
);

-- ============================================================================
-- 4. VARIANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS variants (
    variant_id TEXT PRIMARY KEY, -- UUID as TEXT
    variant_name TEXT NOT NULL UNIQUE,
    description TEXT,
    unit TEXT,
    value DECIMAL(10,2),
    is_verified BOOLEAN DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 5. MENU ITEM VARIANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS menu_item_variants (
    order_item_id TEXT PRIMARY KEY,
    menu_item_id TEXT NOT NULL REFERENCES menu_items(menu_item_id),
    variant_id TEXT NOT NULL REFERENCES variants(variant_id),
    price DECIMAL(10,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT 1,
    addon_eligible BOOLEAN DEFAULT 0,
    delivery_eligible BOOLEAN DEFAULT 1,
    is_verified BOOLEAN DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 6. ORDERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- PetPooja identifiers
    petpooja_order_id INTEGER NOT NULL UNIQUE,
    stream_id INTEGER NOT NULL UNIQUE,
    event_id TEXT NOT NULL UNIQUE,
    aggregate_id TEXT,
    
    -- Relationships
    customer_id INTEGER REFERENCES customers(customer_id),
    restaurant_id INTEGER REFERENCES restaurants(restaurant_id),
    
    -- Order metadata
    occurred_at TEXT NOT NULL,
    created_on TEXT NOT NULL, -- "YYYY-MM-DD HH:MM:SS"
    
    -- Order type and source
    order_type TEXT NOT NULL,
    order_from TEXT NOT NULL,
    sub_order_type TEXT,
    order_from_id TEXT,
    
    -- Order status and processing
    order_status TEXT NOT NULL,
    biller TEXT,
    assignee TEXT,
    
    -- Dine-in specific fields
    table_no TEXT,
    token_no TEXT,
    no_of_persons INTEGER DEFAULT 0,
    
    -- Customer invoice
    customer_invoice_id TEXT,
    
    -- Financial breakdown
    core_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    tax_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    discount_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0,
    packaging_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    service_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    round_off DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) NOT NULL DEFAULT 0,
    
    -- Additional information
    comment TEXT,
    
    -- Timestamps
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    
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
-- 7. ORDER TAXES
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_taxes (
    order_tax_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    tax_title TEXT NOT NULL,
    tax_rate DECIMAL(5,2) NOT NULL,
    tax_type TEXT NOT NULL,
    tax_amount DECIMAL(10,2) NOT NULL CHECK (tax_amount >= 0),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 8. ORDER DISCOUNTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_discounts (
    order_discount_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    discount_title TEXT NOT NULL,
    discount_type TEXT NOT NULL,
    discount_rate DECIMAL(5,2) DEFAULT 0,
    discount_amount DECIMAL(10,2) NOT NULL CHECK (discount_amount >= 0),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 9. ORDER ITEMS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    
    -- Menu item mapping
    menu_item_id TEXT REFERENCES menu_items(menu_item_id),
    variant_id TEXT REFERENCES variants(variant_id),
    
    -- PetPooja identifiers
    petpooja_itemid INTEGER,
    itemcode TEXT,
    
    -- Raw data preservation
    name_raw TEXT NOT NULL,
    category_name TEXT,
    
    -- Order details
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL CHECK (unit_price >= 0),
    total_price DECIMAL(10,2) NOT NULL CHECK (total_price >= 0),
    tax_amount DECIMAL(10,2) DEFAULT 0 CHECK (tax_amount >= 0),
    discount_amount DECIMAL(10,2) DEFAULT 0 CHECK (discount_amount >= 0),
    
    -- Additional metadata
    specialnotes TEXT,
    sap_code TEXT,
    vendoritemcode TEXT,
    
    -- Matching confidence
    match_confidence DECIMAL(5,2) CHECK (
        match_confidence IS NULL OR 
        (match_confidence >= 0 AND match_confidence <= 100)
    ),
    match_method TEXT,
    
    -- Timestamps
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 10. ORDER ITEM ADDONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_item_addons (
    order_item_addon_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id) ON DELETE CASCADE,
    
    -- Addon mapping
    menu_item_id TEXT REFERENCES menu_items(menu_item_id),
    variant_id TEXT REFERENCES variants(variant_id),
    
    -- PetPooja identifiers
    petpooja_addonid TEXT,
    
    -- Raw data
    name_raw TEXT NOT NULL,
    group_name TEXT,
    
    -- Order details
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),
    
    -- Additional metadata
    addon_sap_code TEXT,
    
    -- Matching confidence
    match_confidence DECIMAL(5,2) CHECK (
        match_confidence IS NULL OR 
        (match_confidence >= 0 AND match_confidence <= 100)
    ),
    match_method TEXT,
    
    -- Timestamps
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 14. MERGE HISTORY
-- ============================================================================
CREATE TABLE IF NOT EXISTS merge_history (
    merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    affected_order_items TEXT NOT NULL, -- JSONB as TEXT
    merged_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 15. AI LOGS & FEEDBACK
-- ============================================================================
CREATE TABLE IF NOT EXISTS ai_logs (
    query_id TEXT PRIMARY KEY, -- UUID as TEXT (one per user query + AI response)
    user_query TEXT NOT NULL, -- effective query used (corrected/rewritten)
    intent TEXT,
    sql_generated TEXT,
    response_type TEXT, -- 'text', 'table', 'chart', 'multi'
    response_payload TEXT, -- JSON summary or small payload (Phase 6: avoid large result data)
    error_message TEXT,
    execution_time_ms INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    -- Phase 6: pipeline metadata for debug/eval/cache (no large result data)
    raw_user_query TEXT, -- original prompt from user (before spelling/follow-up)
    corrected_query TEXT, -- after spelling + optional follow-up rewrite (= user_query)
    action_sequence TEXT, -- JSON array e.g. ["RUN_SQL"]
    explanation TEXT, -- natural-language explanation(s) from pipeline
    uploaded_at TEXT -- when sent to client-learning cloud (NULL = not yet)
);

CREATE TABLE IF NOT EXISTS ai_feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id TEXT REFERENCES ai_logs(query_id) ON DELETE CASCADE,
    is_positive BOOLEAN NOT NULL,
    comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    uploaded_at TEXT -- when sent to client-learning cloud (NULL = not yet)
);

-- ============================================================================
-- 15b. AI CONVERSATIONS (Persistence)
-- ============================================================================
CREATE TABLE IF NOT EXISTS ai_conversations (
    conversation_id TEXT PRIMARY KEY, -- UUID as TEXT
    title TEXT, -- Auto-generated or user-defined title
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    synced_at TEXT -- NULL = never synced to master server
);

CREATE TABLE IF NOT EXISTS ai_messages (
    message_id TEXT PRIMARY KEY, -- UUID as TEXT
    conversation_id TEXT NOT NULL REFERENCES ai_conversations(conversation_id) ON DELETE CASCADE,
    role TEXT NOT NULL, -- 'user' or 'ai'
    content TEXT NOT NULL, -- JSON blob (text, table data, chart config, etc.)
    type TEXT, -- 'text', 'table', 'chart', 'multi'
    sql_query TEXT,
    explanation TEXT,
    query_id TEXT REFERENCES ai_logs(query_id) ON DELETE SET NULL,
    query_status TEXT, -- 'complete', 'incomplete', 'ignored'
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_updated ON ai_conversations(updated_at);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_synced ON ai_conversations(synced_at);

-- ============================================================================
-- 16. VIEWS
-- ============================================================================

-- Menu Items Summary View (used by Menu Items tab)
CREATE VIEW IF NOT EXISTS menu_items_summary_view AS
SELECT 
    m.menu_item_id,
    m.name,
    m.type,
    m.total_revenue,
    m.total_sold,
    m.sold_as_item,
    m.sold_as_addon,
    m.is_active
FROM menu_items m;

-- ============================================================================
-- 17. INDEXES
-- ============================================================================

-- Restaurants
CREATE INDEX IF NOT EXISTS idx_restaurants_petpooja_restid ON restaurants(petpooja_restid);
CREATE INDEX IF NOT EXISTS idx_restaurants_name ON restaurants(name);
CREATE INDEX IF NOT EXISTS idx_restaurants_is_active ON restaurants(is_active);

-- Customers
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
CREATE INDEX IF NOT EXISTS idx_customers_name_normalized ON customers(name_normalized);
CREATE INDEX IF NOT EXISTS idx_customers_last_order_date ON customers(last_order_date);
CREATE INDEX IF NOT EXISTS idx_customers_phone_not_null ON customers(phone) WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_identity_key ON customers(customer_identity_key);
CREATE INDEX IF NOT EXISTS idx_customers_is_verified ON customers(is_verified);

-- Menu Items
CREATE INDEX IF NOT EXISTS idx_menu_items_name ON menu_items(name);
CREATE INDEX IF NOT EXISTS idx_menu_items_type ON menu_items(type);
CREATE INDEX IF NOT EXISTS idx_menu_items_name_type ON menu_items(name, type);
CREATE INDEX IF NOT EXISTS idx_menu_items_unverified ON menu_items(is_verified) WHERE is_verified = 0;

-- Variants
CREATE INDEX IF NOT EXISTS idx_variants_variant_name ON variants(variant_name);

-- Menu Item Variants
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_menu_item_id ON menu_item_variants(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_variant_id ON menu_item_variants(variant_id);
CREATE INDEX IF NOT EXISTS idx_menu_item_variants_unverified ON menu_item_variants(is_verified) WHERE is_verified = 0;

-- Orders
CREATE INDEX IF NOT EXISTS idx_orders_petpooja_order_id ON orders(petpooja_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_stream_id ON orders(stream_id);
CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_restaurant_id ON orders(restaurant_id);
CREATE INDEX IF NOT EXISTS idx_orders_created_on ON orders(created_on);
CREATE INDEX IF NOT EXISTS idx_orders_occurred_at ON orders(occurred_at);
CREATE INDEX IF NOT EXISTS idx_orders_order_type ON orders(order_type);
CREATE INDEX IF NOT EXISTS idx_orders_order_from ON orders(order_from);
CREATE INDEX IF NOT EXISTS idx_orders_order_status ON orders(order_status);
CREATE INDEX IF NOT EXISTS idx_orders_created_on_type ON orders(created_on, order_type);
CREATE INDEX IF NOT EXISTS idx_orders_customer_created ON orders(customer_id, created_on);

-- Order Taxes
CREATE INDEX IF NOT EXISTS idx_order_taxes_order_id ON order_taxes(order_id);

-- Order Discounts
CREATE INDEX IF NOT EXISTS idx_order_discounts_order_id ON order_discounts(order_id);

-- Order Items
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_menu_item_id ON order_items(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_order_items_variant_id ON order_items(variant_id);
CREATE INDEX IF NOT EXISTS idx_order_items_petpooja_itemid ON order_items(petpooja_itemid);
CREATE INDEX IF NOT EXISTS idx_order_items_match_confidence ON order_items(match_confidence) WHERE match_confidence < 80;
CREATE INDEX IF NOT EXISTS idx_order_items_menu_variant ON order_items(menu_item_id, variant_id);

-- Order Item Addons
CREATE INDEX IF NOT EXISTS idx_order_item_addons_order_item_id ON order_item_addons(order_item_id);
CREATE INDEX IF NOT EXISTS idx_order_item_addons_menu_item_id ON order_item_addons(menu_item_id);
CREATE INDEX IF NOT EXISTS idx_order_item_addons_group_name ON order_item_addons(group_name);


-- Merge History
CREATE INDEX IF NOT EXISTS idx_merge_history_target ON merge_history(target_id);
-- ============================================================================
-- 18. SYSTEM CONFIGURATION
-- ============================================================================
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);


-- ============================================================================
-- 18. APP USERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS app_users (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 19. SYSTEM CONFIGURATION
-- ============================================================================
CREATE TABLE IF NOT EXISTS weather_daily (
    date TEXT,        -- YYYY-MM-DD
    city TEXT,
    
    -- Observed Metrics (Actuals)
    temp_max DECIMAL(4,1),
    temp_min DECIMAL(4,1),
    temp_mean DECIMAL(4,1),
    precipitation_sum DECIMAL(6,1),
    rain_sum DECIMAL(6,1),
    wind_speed_max DECIMAL(4,1),
    
    -- Weather Codes (WMO)
    weather_code INTEGER,
    
    -- Forecast Snapshot (JSON) - Predictions for next 7 days made on this date
    forecast_snapshot TEXT,
    
    -- Metadata
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    
    PRIMARY KEY (date, city)
);

-- ============================================================================
-- 20. FORECAST SNAPSHOTS (Audit/Replay)
-- ============================================================================
CREATE TABLE IF NOT EXISTS forecast_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_run_date DATE NOT NULL,      -- When the forecast was generated (e.g., Today)
    target_date DATE NOT NULL,            -- The future date being predicted
    
    -- Predictions
    pred_mean FLOAT,
    pred_std FLOAT,
    lower_95 FLOAT,
    upper_95 FLOAT,
    
    -- Metadata for Audit
    model_window_start DATE,
    model_window_end DATE,
    
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(forecast_run_date, target_date)
);

CREATE INDEX IF NOT EXISTS idx_forecast_snapshots_run_date ON forecast_snapshots(forecast_run_date);

