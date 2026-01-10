# Analytics Database Client App

A web-based client application for querying and managing the analytics database.

## Features

- ✅ **SQL Query Interface** - Execute custom SQL queries and view results
- ✅ **Database Sync** - Sync new orders from the cloud database with one click
- ✅ **Table Browser** - Browse all tables with pagination
- ✅ **Real-time Stats** - View database statistics in the sidebar
- ✅ **Export Data** - Download query results as CSV

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements_app.txt
```

Or install manually:
```bash
pip install streamlit pandas psycopg2-binary requests
```

### 2. Configure Database Connection

#### Option A: Using Secrets File (Recommended)

1. Create `.streamlit` directory:
```bash
mkdir -p .streamlit
```

2. Copy the example secrets file:
```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

3. Edit `.streamlit/secrets.toml` with your database credentials:
```toml
[database]
url = "postgresql://username:password@localhost:5432/analytics"
```

#### Option B: Using Environment Variable

```bash
export DB_URL="postgresql://username:password@localhost:5432/analytics"
```

#### Option C: Default Connection

If no secrets or environment variable is set, the app will use:
```
postgresql://kshitijsharma@localhost:5432/analytics
```

## Running the App

```bash
streamlit run app.py
```

The app will open in your default web browser at `http://localhost:8501`

## Usage

### SQL Query Tab

1. Enter your SQL query in the text area
2. Set a result limit (optional)
3. Click "Execute Query"
4. View results in the table
5. Download results as CSV if needed

**Example Queries:**
```sql
-- Get top 10 orders by total
SELECT * FROM orders 
ORDER BY total DESC 
LIMIT 10;

-- Get revenue by order type
SELECT order_type, COUNT(*) as count, SUM(total) as revenue
FROM orders
WHERE order_status = 'Success'
GROUP BY order_type;

-- Get top menu items
SELECT 
    mi.name,
    v.variant_name,
    COUNT(*) as order_count,
    SUM(oi.total_price) as revenue
FROM order_items oi
JOIN menu_items mi ON oi.menu_item_id = mi.menu_item_id
JOIN variants v ON oi.variant_id = v.variant_id
GROUP BY mi.name, v.variant_name
ORDER BY revenue DESC
LIMIT 10;
```

### Sync Database

1. Click "Connect to Database" in the sidebar
2. Click "Sync New Orders" button
3. The app will fetch and load only new orders (incremental update)
4. View sync statistics

### Table Browser Tabs

Each tab shows a different table:
- **Orders** - All orders with pagination
- **Order Items** - Individual items in orders
- **Customers** - Customer information
- **Restaurants** - Restaurant information
- **Order Taxes** - Tax breakdowns
- **Order Discounts** - Discount information

**Features:**
- Pagination controls (First, Previous, Next, Last)
- Configurable rows per page
- Sorted by time (newest first by default)
- Refresh button to reload data

## Troubleshooting

### Database Connection Issues

**Error:** "Database connection failed"

**Solutions:**
1. Check if PostgreSQL is running
2. Verify database credentials in `.streamlit/secrets.toml`
3. Test connection manually:
   ```bash
   python3 database/test_connection.py --db-url "your_connection_string"
   ```

### Sync Issues

**Error:** "Sync error" or "ItemMatcher initialization failed"

**Solutions:**
1. Ensure menu data is loaded first:
   ```bash
   python3 database/test_load_menu_postgresql.py --db-url "your_connection_string"
   ```
2. Check internet connection (for API access)
3. Verify API key in `fetch_orders.py`

### Performance Issues

**Slow queries:**
- Use LIMIT in your SQL queries
- Use pagination in table browser tabs
- Consider adding indexes for frequently queried columns

**Large result sets:**
- The app limits results to 1000 rows by default
- Increase limit if needed, but be aware of memory usage

## Advanced Configuration

### Custom Port

```bash
streamlit run app.py --server.port 8502
```

### Custom Theme

Create `.streamlit/config.toml`:
```toml
[theme]
primaryColor = "#FF6B6B"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F0F2F6"
textColor = "#262730"
font = "sans serif"
```

### Docker Deployment

The app can be run in Docker containers. See `DOCKER_README.md` for complete Docker setup instructions.

**Quick Start with Docker:**
```bash
# Using Docker Compose (includes PostgreSQL)
docker-compose up -d

# Or using Makefile
make up
```

The app will be available at `http://localhost:8501`

## Production Deployment

For production deployment, consider:
- Using Docker containers (see `DOCKER_README.md`)
- Using environment variables for secrets
- Setting up authentication
- Using a reverse proxy (nginx)
- Enabling HTTPS

See [Streamlit Deployment Guide](https://docs.streamlit.io/deploy) for more information.

## File Structure

```
analytics/
├── app.py                          # Main Streamlit application
├── requirements_app.txt            # Python dependencies
├── APP_README.md                  # This file
├── .streamlit/
│   ├── secrets.toml.example       # Example secrets file
│   └── secrets.toml               # Your actual secrets (not in git)
└── database/
    └── load_orders.py             # Used for sync functionality
```

## Security Notes

⚠️ **Important:**
- Never commit `.streamlit/secrets.toml` to git
- Use environment variables in production
- Restrict database access appropriately
- Consider adding authentication for production use

## Support

For issues or questions:
1. Check `SETUP_GUIDE.md` for database setup
2. Review `database/README.md` for database documentation
3. Check Streamlit documentation: https://docs.streamlit.io

---

*Last Updated: January 9, 2026*

