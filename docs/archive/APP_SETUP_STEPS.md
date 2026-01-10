# App Setup Steps - First Time Setup

If you're seeing "relation does not exist" errors, follow these steps:

## Step 1: Connect to Database

1. Open the app: `http://localhost:8501`
2. In the sidebar, click **"🔌 Connect to Database"**
3. This will automatically create the database schema (tables)

## Step 2: Load Menu Data

The app needs menu data before it can sync orders. Run this command:

```bash
# Using Docker
docker-compose exec app python3 database/test_load_menu_postgresql.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics"

# Or using Makefile
make load-menu
```

## Step 3: Sync Orders

Now you can click **"Sync New Orders"** in the app sidebar.

## Quick Setup (All at Once)

```bash
# 1. Start containers
docker-compose up -d

# 2. Load menu data
docker-compose exec app python3 database/test_load_menu_postgresql.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics"

# 3. Load some test orders (optional)
docker-compose exec app python3 database/load_orders.py \
  --db-url "postgresql://postgres:postgres@postgres:5432/analytics" \
  --limit 10

# 4. Access app
# Open http://localhost:8501
```

## Troubleshooting

### "relation does not exist" Error

**Solution:** The schema hasn't been created yet.

1. Click **"🔌 Connect to Database"** in the sidebar
2. Or click **"📋 Create Schema"** button if it appears
3. Then load menu data

### "Menu data not loaded" Warning

**Solution:** Load menu data first:

```bash
make load-menu
```

### Schema Creation Fails

**Solution:** Create schema manually:

```bash
docker-compose exec postgres psql -U postgres -d analytics -f /path/to/schema/orders.sql
```

Or use the Python script:
```bash
docker-compose exec app python3 database/test_load_menu_postgresql.py --db-url "postgresql://postgres:postgres@postgres:5432/analytics"
```

---

**Remember:** Always load menu data before syncing orders! 🚀

