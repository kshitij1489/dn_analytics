# Complete Setup Guide - Analytics Database

This guide will help you set up the analytics database on a new computer from scratch.

## Prerequisites

1. **Python 3.8+** installed
2. **PostgreSQL** installed and running
3. **Internet connection** (for fetching orders from API)

---

## Step 1: Install Dependencies

```bash
# Navigate to project directory
cd /path/to/analytics

# Install Python dependencies
pip install psycopg2-binary requests
```

---

## Step 2: Set Up PostgreSQL Database

### Option A: Using Homebrew (macOS)

```bash
# Install PostgreSQL (if not already installed)
brew install postgresql@16

# Start PostgreSQL service
brew services start postgresql@16

# Create database
createdb analytics

# Verify connection
psql -d analytics -c "SELECT version();"
```

### Option B: Using Package Manager (Linux)

```bash
# Install PostgreSQL
sudo apt-get update
sudo apt-get install postgresql postgresql-contrib

# Start PostgreSQL service
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create database (as postgres user)
sudo -u postgres createdb analytics

# Verify connection
psql -U postgres -d analytics -c "SELECT version();"
```

### Option C: Using Docker

```bash
# Run PostgreSQL in Docker
docker run --name analytics-postgres \
  -e POSTGRES_PASSWORD=yourpassword \
  -e POSTGRES_DB=analytics \
  -p 5432:5432 \
  -d postgres:16

# Verify connection
docker exec -it analytics-postgres psql -U postgres -d analytics -c "SELECT version();"
```

---

## Step 3: Test Database Connection

```bash
# Test connection (replace with your credentials)
python3 database/test_connection.py \
  --db-url "postgresql://yourusername:yourpassword@localhost:5432/analytics"

# Or with individual parameters
python3 database/test_connection.py \
  --host localhost \
  --port 5432 \
  --database analytics \
  --user yourusername \
  --password yourpassword
```

**Expected Output:**
```
================================================================================
Testing Database Connection
================================================================================
  ✓ Connection successful!
  PostgreSQL Version: PostgreSQL 16.x
```

---

## Step 4: Load Menu Data

The menu data must be loaded before loading orders (orders reference menu items).

```bash
# Load menu data from cleaned_menu.csv
python3 database/test_load_menu_postgresql.py \
  --db-url "postgresql://yourusername:yourpassword@localhost:5432/analytics"
```

**Expected Output:**
```
================================================================================
Loading Menu Data
================================================================================
1. Loading cleaned_menu.csv...
   Loaded 343 menu entries

2. Extracted unique data:
   - 103 unique menu items
   - 30 unique variants

3. Creating schema if needed...
   ✓ Schema created

4. Inserting menu items...
   ✓ Inserted 103 menu items

5. Inserting variants...
   ✓ Inserted 30 variants

6. Inserting menu item variants...
   ✓ Inserted 343 menu item variants

✅ Menu data loaded successfully!
```

**Verify Menu Data:**
```bash
# Connect to database
psql -d analytics

# Check menu items count
SELECT COUNT(*) FROM menu_items;
-- Should show: 103

# Check variants count
SELECT COUNT(*) FROM variants;
-- Should show: 30

# Check menu item variants count
SELECT COUNT(*) FROM menu_item_variants;
-- Should show: 343
```

---

## Step 5: Load Historical Order Data

### Option A: Load from API (Recommended)

```bash
# Load all historical orders from API
python3 database/load_orders.py \
  --db-url "postgresql://yourusername:yourpassword@localhost:5432/analytics"
```

**Note:** This will fetch all orders from the PetPooja API. This may take a while depending on the number of orders.

### Option B: Load from JSON File

If you have a JSON file with orders:

```bash
# Load from file
python3 database/load_orders.py \
  --db-url "postgresql://yourusername:yourpassword@localhost:5432/analytics" \
  --input-file sample_payloads/raw_orders.json
```

### Option C: Test with Limited Orders (Recommended for First Run)

```bash
# Load only 10 orders to test
python3 database/load_orders.py \
  --db-url "postgresql://yourusername:yourpassword@localhost:5432/analytics" \
  --limit 10
```

**Expected Output:**
```
================================================================================
Order Data Loading Script
================================================================================

1. Connecting to database...
  ✓ Connected successfully

2. Creating schema if needed...
  ✓ Schema ready

3. Initializing ItemMatcher...
  ✓ ItemMatcher ready

4. Loading orders...
  Fetching all orders from API...
  Total orders to process: 10
  Processing order 10/10...

================================================================================
LOADING SUMMARY
================================================================================
Orders processed: 10
Order items: 25
Order item addons: 5
Taxes: 20
Discounts: 3

✅ Loading complete!
```

---

## Step 6: Verify Data Loaded

```bash
# Connect to database
psql -d analytics

# Check orders count
SELECT COUNT(*) FROM orders;

# Check order items count
SELECT COUNT(*) FROM order_items;

# Check customers count
SELECT COUNT(*) FROM customers;

# Check restaurants count
SELECT COUNT(*) FROM restaurants;

# View sample order
SELECT 
    o.order_id,
    o.petpooja_order_id,
    o.order_type,
    o.order_from,
    o.total,
    c.name as customer_name,
    r.name as restaurant_name
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.customer_id
LEFT JOIN restaurants r ON o.restaurant_id = r.restaurant_id
LIMIT 5;
```

---

## Step 7: Set Up Incremental Updates

For ongoing updates (new orders only):

### Option A: Using Python Script

```bash
# Load only new orders (orders with stream_id > last processed)
python3 database/load_orders.py \
  --db-url "postgresql://yourusername:yourpassword@localhost:5432/analytics" \
  --incremental
```

### Option B: Using Shell Script (Recommended)

```bash
# Make script executable (first time only)
chmod +x database/update_orders_incremental.sh

# Run incremental update
./database/update_orders_incremental.sh

# Or with custom database URL
./database/update_orders_incremental.sh --db-url "postgresql://user:pass@localhost:5432/analytics"
```

**How it works:**
- The script checks the maximum `stream_id` in the database
- Fetches only orders with `stream_id` greater than that
- Processes and inserts new orders
- Reports how many new orders were loaded

**Recommended:** Set up a cron job or scheduled task to run this periodically:

```bash
# Add to crontab (runs every hour)
0 * * * * cd /path/to/analytics && ./database/update_orders_incremental.sh >> /var/log/analytics_updates.log 2>&1

# Or using Python directly
0 * * * * cd /path/to/analytics && python3 database/load_orders.py --db-url "postgresql://user:pass@localhost:5432/analytics" --incremental >> /var/log/analytics_updates.log 2>&1
```

**Check Last Update:**
```sql
-- Check last processed stream_id
SELECT MAX(stream_id) FROM orders;

-- Check last order date
SELECT MAX(created_on) FROM orders;
```

---

## Quick Start Commands (Copy-Paste)

For a new computer, run these commands in order:

```bash
# 1. Install dependencies
pip install psycopg2-binary requests

# 2. Create database (adjust for your system)
createdb analytics

# 3. Test connection (replace credentials)
python3 database/test_connection.py --db-url "postgresql://user:pass@localhost:5432/analytics"

# 4. Load menu data
python3 database/test_load_menu_postgresql.py --db-url "postgresql://user:pass@localhost:5432/analytics"

# 5. Test with 10 orders
python3 database/load_orders.py --db-url "postgresql://user:pass@localhost:5432/analytics" --limit 10

# 6. Load all historical orders (if test was successful)
python3 database/load_orders.py --db-url "postgresql://user:pass@localhost:5432/analytics"
```

---

## Troubleshooting

### Connection Issues

**Error:** `Connection refused` or `could not connect to server`

**Solutions:**
1. Check if PostgreSQL is running:
   ```bash
   # macOS
   brew services list | grep postgresql
   
   # Linux
   sudo systemctl status postgresql
   ```

2. Check PostgreSQL is listening on port 5432:
   ```bash
   # macOS/Linux
   lsof -i :5432
   ```

3. Check `pg_hba.conf` allows connections (usually in `/etc/postgresql/16/main/pg_hba.conf` or similar)

### Schema Creation Errors

**Error:** `relation "menu_items" already exists`

**Solution:** Tables already exist. This is fine - the script will use existing tables.

### Menu Data Loading Errors

**Error:** `duplicate key value violates unique constraint`

**Solution:** Menu data already loaded. Use `--clear` flag to reload:
```bash
python3 database/test_load_menu_postgresql.py \
  --db-url "postgresql://user:pass@localhost:5432/analytics" \
  --clear
```

### Order Loading Errors

**Error:** `foreign key constraint violation`

**Solution:** Make sure menu data is loaded first (Step 4).

**Error:** `ItemMatcher initialization failed`

**Solution:** Ensure menu data is loaded. ItemMatcher requires menu_items and variants tables.

### API Connection Issues

**Error:** `Connection timeout` or `API key invalid`

**Solutions:**
1. Check internet connection
2. Verify API key in `fetch_orders.py` is correct
3. Check if API endpoint is accessible:
   ```bash
   curl -H "X-API-Key: f3e1753aa4c44159fa7218a31cd8db1e" \
     "https://webhooks.db1-prod-dachnona.store/analytics/orders/?limit=1"
   ```

---

## Environment Variables (Optional)

To avoid passing credentials in commands, set environment variables:

```bash
# Add to ~/.bashrc or ~/.zshrc
export DB_URL="postgresql://yourusername:yourpassword@localhost:5432/analytics"
```

Then modify scripts to read from `os.environ.get('DB_URL')`.

---

## Next Steps

After setup is complete:

1. **Verify Data Quality:**
   ```sql
   -- Check for unmatched items
   SELECT COUNT(*) FROM order_items WHERE menu_item_id IS NULL;
   
   -- Check match confidence distribution
   SELECT match_method, COUNT(*) 
   FROM order_items 
   GROUP BY match_method;
   ```

2. **Run Analytics Queries:**
   - See `database/schema/00_schema_overview.md` for example queries
   - Create custom analytics dashboards

3. **Set Up Automated Updates:**
   - Configure cron job for incremental updates
   - Set up monitoring/alerts for errors

---

## Support

For issues or questions:
1. Check `database/TROUBLESHOOTING.md` for common issues
2. Review `STATUS.md` for project status
3. Check `database/README.md` for detailed documentation

---

*Last Updated: January 9, 2026*

