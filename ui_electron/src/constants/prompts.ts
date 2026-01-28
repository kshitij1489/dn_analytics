export const LLM_PROMPT_TEXT = `I am working on a SQLite database for a restaurant analytics system.
Below is the full schema of my database. Please use this schema context to answer all my future questions about writing SQL queries.

## Context
- The database stores restaurant orders from PetPooja (a POS system).
- It tracks Customers, Menu Items, Orders, and Order Items.
- We have a specific focus on "Menu Clustering" (normalizing raw item names to clean menu items).
- All monetary amounts are in INR.
- The restaurant operates in IST (Asia/Kolkata timezone).

## Database Schema (\`schema_sqlite.sql\`)
\`\`\`sql
-- ============================================================================
-- 1. RESTAURANTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS restaurants (
    restaurant_id INTEGER PRIMARY KEY AUTOINCREMENT,
    petpooja_restid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    address TEXT,
    contact_information TEXT,
    is_active BOOLEAN DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 2. CUSTOMERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_identity_key TEXT,
    name TEXT,
    name_normalized TEXT,
    phone TEXT,
    address TEXT,
    gstin TEXT,
    first_order_date TEXT,
    last_order_date TEXT,
    total_orders INTEGER DEFAULT 0,            
    total_spent DECIMAL(10,2) DEFAULT 0,      
    is_verified BOOLEAN DEFAULT 0,
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
    total_revenue DECIMAL(12,2) DEFAULT 0,
    total_sold INTEGER DEFAULT 0,
    sold_as_item INTEGER DEFAULT 0,
    sold_as_addon INTEGER DEFAULT 0,
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
    petpooja_order_id INTEGER NOT NULL UNIQUE,
    stream_id INTEGER NOT NULL UNIQUE,
    event_id TEXT NOT NULL UNIQUE,
    aggregate_id TEXT,
    customer_id INTEGER REFERENCES customers(customer_id),
    restaurant_id INTEGER REFERENCES restaurants(restaurant_id),
    occurred_at TEXT NOT NULL,
    created_on TEXT NOT NULL, -- "YYYY-MM-DD HH:MM:SS"
    order_type TEXT NOT NULL,
    order_from TEXT NOT NULL,
    sub_order_type TEXT,
    order_from_id TEXT,
    order_status TEXT NOT NULL,
    biller TEXT,
    assignee TEXT,
    table_no TEXT,
    token_no TEXT,
    no_of_persons INTEGER DEFAULT 0,
    customer_invoice_id TEXT,
    core_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    tax_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    discount_total DECIMAL(10,2) NOT NULL DEFAULT 0,
    delivery_charges DECIMAL(10,2) NOT NULL DEFAULT 0,
    packaging_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    service_charge DECIMAL(10,2) NOT NULL DEFAULT 0,
    round_off DECIMAL(10,2) DEFAULT 0,
    total DECIMAL(10,2) NOT NULL DEFAULT 0,
    comment TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK (core_total >= 0),
    CHECK (tax_total >= 0)
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
    menu_item_id TEXT REFERENCES menu_items(menu_item_id),
    variant_id TEXT REFERENCES variants(variant_id),
    petpooja_itemid INTEGER,
    itemcode TEXT,
    name_raw TEXT NOT NULL,
    category_name TEXT,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL CHECK (unit_price >= 0),
    total_price DECIMAL(10,2) NOT NULL CHECK (total_price >= 0),
    tax_amount DECIMAL(10,2) DEFAULT 0 CHECK (tax_amount >= 0),
    discount_amount DECIMAL(10,2) DEFAULT 0 CHECK (discount_amount >= 0),
    specialnotes TEXT,
    sap_code TEXT,
    vendoritemcode TEXT,
    match_confidence DECIMAL(5,2),
    match_method TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 10. ORDER ITEM ADDONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS order_item_addons (
    order_item_addon_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id) ON DELETE CASCADE,
    menu_item_id TEXT REFERENCES menu_items(menu_item_id),
    variant_id TEXT REFERENCES variants(variant_id),
    petpooja_addonid TEXT,
    name_raw TEXT NOT NULL,
    group_name TEXT,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),
    addon_sap_code TEXT,
    match_confidence DECIMAL(5,2),
    match_method TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- 16. VIEWS
-- ============================================================================
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
\`\`\`

## STRATEGY:
- For Menu Items Analysis (Revenue/Sales by Item), prefer using \`menu_items_summary_view\` as it has pre-calculated \`total_revenue\`, \`total_sold\` etc.
- For detailed Order Analysis, join \`orders\` and \`order_items\`.

## CRITICAL COLUMN MAPPINGS (use these EXACT names):
| Concept | Use This Column | DO NOT USE |
|---------|-----------------|------------|
| Order Revenue | \`orders.total\` | 'amount', 'revenue', 'value' |
| Order Date | \`orders.created_on\` | \`occurred_at\` (has invalid values!) |
| Order ID | \`orders.order_id\` | \`id\` (does not exist) |
| Item Revenue | \`order_items.total_price\` | |
| Item Name (raw) | \`order_items.name_raw\` | |
| Menu Item ID | \`menu_items.menu_item_id\` | \`id\` (does not exist) |
| Menu Item Name | \`menu_items.name\` | |
| Category/Type | \`menu_items.type\` | |
| Order Source | \`orders.order_from\` | Values: 'Swiggy', 'Zomato', 'POS', 'Home Website' |

## RULES:
1. Return ONLY the SQL query. No markdown, no explanation, no backticks.
2. Use standard SQLite syntax (TEXT for UUIDs/Dates).
3. Dates are stored as TEXT 'YYYY-MM-DD HH:MM:SS'. 
   - Use \`date(orders.created_on)\` to extract date.
   - Use \`strftime('%H', orders.created_on)\` for hour.
4. Relative Date Logic:
   - 'today': \`date(orders.created_on) = date('now', 'localtime')\`
   - 'yesterday': \`date(orders.created_on) = date('now', '-1 day', 'localtime')\`
   - 'last X days': \`orders.created_on >= date('now', '-X days', 'localtime')\`
5. \`order_items\` and \`order_item_addons\` link to \`menu_items\` via \`menu_item_id\`.
6. Limit results to 100 rows unless specified otherwise.
7. NEVER use \`occurred_at\` - it contains invalid data.
8. NEVER use \`created_at\` - this is the system insertion time (technical metadata). 
   - ALWAYS use \`created_on\` - this is the actual Timestamp of Order Placement (Business Date).
9. ALWAYS filter by \`orders.order_status = 'Success'\` unless specified otherwise.
10. When filtering by Item Name, ALWAYS join 'order_items' with 'menu_items' and filter on 'menu_items.name'. NEVER filter on 'order_items.name_raw'.
11. To calculate "Total Sold" or "Revenue" for an item (which can be sold as a main item OR an add-on):
    - ✅ USE \`UNION ALL\` to combine results from \`order_items\` and \`order_item_addons\`.
    - ❌ DO NOT JOIN \`order_items\` directly to \`order_item_addons\`. This causes row explosion.
    - ⚠️ EACH subquery in the UNION must JOIN to \`orders\` independently if you need to filter by order date/status.
    - Example Pattern:
      \`\`\`
      SELECT menu_item_id, SUM(qty) as total_sold, SUM(rev) as total_revenue FROM (
          SELECT oi.menu_item_id, oi.quantity as qty, oi.total_price as rev
          FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
          WHERE o.order_status = 'Success' AND o.created_on >= date('now', '-90 days', 'localtime')
          UNION ALL
          SELECT oia.menu_item_id, oia.quantity as qty, oia.price * oia.quantity as rev
          FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
          JOIN order_item_addons oia ON oi.order_item_id = oia.order_item_id
          WHERE o.order_status = 'Success' AND o.created_on >= date('now', '-90 days', 'localtime')
      ) combined GROUP BY menu_item_id
      \`\`\`



## STANDARD BUSINESS DEFINITIONS:
- **Repeat Customer**: A customer who has placed > 1 successful order in their LIFETIME.
  - When matching "Repeat Customers in last 90 days", find customers active in last 90 days, then check their LIFETIME order count (total_orders > 1). Do NOT limit the "repeat" check to just the last 90 days.
- **Item Repeat Rate**: The % of unique customers who bought a specific item more than once in their LIFETIME.
  - Formula: (Count of Customers who bought Item X > 1 time ever) / (Total Unique Customers who bought Item X).


## COMMON MISTAKES TO AVOID:
- ❌ JOINing on \`id\` (e.g. \`orders.id\`, \`menu_items.id\`). THESE COLUMNS DO NOT EXIST.
- ✅ ALWAYS use explicit IDs: \`orders.order_id\`, \`menu_items.menu_item_id\`.
- ❌ Using \`amount\` or \`revenue\` columns.
- ✅ ALWAYS use \`orders.total\` or \`order_items.total_price\`.
- ❌ Using Postgres functions like \`ILIKE\`, \`TIMESTAMPTZ\`, \`gen_random_uuid\`. Use \`LIKE\` and standard SQLite functions.
- ❌ Filtering on \`order_items.name_raw\`. ALWAYS join with \`menu_items\` and use \`menu_items.name\`.
- ❌ Using \`order_item_addons.total_price\`. THIS COLUMN DOES NOT EXIST. Use \`order_item_addons.price * order_item_addons.quantity\` for add-on revenue.
`;
