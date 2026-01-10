# Database Setup and Testing

This directory contains scripts for loading data into the analytics database.

## Prerequisites

### For PostgreSQL
```bash
pip install psycopg2-binary
```

### For SQLite
No additional packages needed (built into Python)

---

## Menu Data Loading

### Step 1: Test with SQLite (No Setup Required)

```bash
# Test with SQLite (creates test_analytics.db)
python3 database/test_load_menu.py
```

This creates a local SQLite database for testing.

### Step 2: Load into PostgreSQL

#### Option A: Using Connection URL
```bash
python3 database/test_load_menu_postgresql.py \
  --db-url "postgresql://username:password@localhost:5432/analytics"
```

#### Option B: Using Individual Parameters
```bash
python3 database/test_load_menu_postgresql.py \
  --host localhost \
  --port 5432 \
  --database analytics \
  --user postgres \
  --password yourpassword
```

#### Option C: Clear Existing Data First
```bash
# Add --clear flag to remove existing menu data before loading
python3 database/test_load_menu_postgresql.py \
  --db-url "postgresql://user:pass@localhost:5432/analytics" \
  --clear
```

---

## Order Data Loading

### Step 1: Load Orders from API

```bash
# Load all orders from API
python3 database/load_orders.py \
  --db-url "postgresql://username:password@localhost:5432/analytics"
```

### Step 2: Load Orders from JSON File

```bash
# Load from a JSON file
python3 database/load_orders.py \
  --db-url "postgresql://username:password@localhost:5432/analytics" \
  --input-file sample_payloads/raw_orders.json
```

### Step 3: Incremental Updates

```bash
# Only load new orders (orders with stream_id > last processed)
python3 database/load_orders.py \
  --db-url "postgresql://username:password@localhost:5432/analytics" \
  --incremental
```

### Step 4: Test with Limited Orders

```bash
# Load only 10 orders for testing
python3 database/load_orders.py \
  --db-url "postgresql://username:password@localhost:5432/analytics" \
  --limit 10
```

### Using Individual Parameters

```bash
python3 database/load_orders.py \
  --host localhost \
  --port 5432 \
  --database analytics \
  --user postgres \
  --password yourpassword
```

### What the Script Does

1. **Connects to PostgreSQL database**
2. **Creates schema if needed** (tables: restaurants, customers, orders, order_items, order_item_addons, order_taxes, order_discounts)
3. **Initializes ItemMatcher** (for matching order items to menu items)
4. **Loads orders** (from API or file)
5. **Processes each order:**
   - Creates/updates restaurant
   - Creates/updates customer (deduplicates by phone)
   - Inserts order
   - Matches and inserts order items
   - Matches and inserts addons
   - Inserts taxes and discounts
6. **Reports statistics** (orders processed, items, addons, errors)

### Features

- ✅ **Automatic schema creation** - Creates all tables if they don't exist
- ✅ **Customer deduplication** - Uses phone number as unique identifier
- ✅ **Item matching** - Uses ItemMatcher to match order items to menu items
- ✅ **Incremental updates** - Only processes new orders
- ✅ **Error handling** - Captures and reports errors per order
- ✅ **Progress reporting** - Shows progress and final statistics

### Output Example

```
================================================================================
Order Data Loading Script
================================================================================

1. Connecting to database...
  ✓ Connected successfully

2. Creating schema if needed...
  ✓ Created schema from restaurants.sql
  ✓ Created schema from customers.sql
  ✓ Created schema from orders.sql
  ✓ Created schema from order_items.sql
  ✓ Schema ready

3. Initializing ItemMatcher...
  ✓ ItemMatcher ready

4. Loading orders...
  Fetching all orders from API...
  Total orders to process: 100
  Processing order 10/100...
  Processing order 20/100...
  ...

================================================================================
LOADING SUMMARY
================================================================================
Orders processed: 100
Order items: 250
Order item addons: 45
Taxes: 200
Discounts: 30

✅ Loading complete!
```

---

## Database Schema

All schema files are in `database/schema/`:

- `menu_items.sql` - Menu items, variants, menu_item_variants tables
- `orders.sql` - Orders, order_taxes, order_discounts tables
- `customers.sql` - Customers table
- `restaurants.sql` - Restaurants table
- `order_items.sql` - Order items and addons tables
- `00_schema_overview.md` - Complete schema documentation

### Creating Schema in PostgreSQL

```bash
# Connect to your database
psql -h localhost -U postgres -d analytics

# Run schema files in order:
\i database/schema/menu_items.sql
\i database/schema/customers.sql
\i database/schema/restaurants.sql
\i database/schema/orders.sql
\i database/schema/order_items.sql
```

Or use the Python script which creates schema automatically.

---

## Environment Variables (Optional)

You can set these environment variables to avoid passing credentials:

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=analytics
export DB_USER=postgres
export DB_PASSWORD=yourpassword
```

Then modify the script to read from environment variables.

---

## Verification Queries

After loading data, verify with these queries:

```sql
-- Count menu items
SELECT COUNT(*) FROM menu_items;

-- Count variants
SELECT COUNT(*) FROM variants;

-- Count menu item variants
SELECT COUNT(*) FROM menu_item_variants;

-- Check addon-eligible items
SELECT mi.name, v.variant_name
FROM menu_items mi
JOIN menu_item_variants miv ON mi.menu_item_id = miv.menu_item_id
JOIN variants v ON miv.variant_id = v.variant_id
WHERE miv.addon_eligible = TRUE
LIMIT 10;

-- Check items by type
SELECT type, COUNT(*) as count
FROM menu_items
GROUP BY type
ORDER BY count DESC;
```

---

## Troubleshooting

### Connection Errors

**Error: "psycopg2 not installed"**
```bash
pip install psycopg2-binary
```

**Error: "Connection refused"**
- Check PostgreSQL is running: `pg_isready`
- Verify host, port, and database name
- Check firewall settings

**Error: "Authentication failed"**
- Verify username and password
- Check PostgreSQL pg_hba.conf settings

### Data Loading Errors

**Error: "Duplicate key violation"**
- Use `--clear` flag to remove existing data first
- Or manually delete existing menu data

**Error: "Foreign key constraint violation"**
- Ensure schema is created in correct order
- Check that referenced tables exist

---

## Next Steps

1. ✅ **Menu Data Loaded** - Menu items, variants, and relationships
2. **Test Item Matching** - Verify matching logic works with loaded data
3. **Load Order Data** - Create script to load orders using matching logic

---

*Last Updated: January 9, 2026*

