# Quick Start - Analytics Database Client App

Get the web application running in 3 steps!

## Step 1: Install Dependencies

```bash
pip install streamlit pandas psycopg2-binary requests
```

Or use the requirements file:
```bash
pip install -r requirements_app.txt
```

## Step 2: Configure Database (Optional)

The app will use a default connection, but you can customize it:

**Option A: Create secrets file**
```bash
mkdir -p .streamlit
echo '[database]
url = "postgresql://yourusername:yourpassword@localhost:5432/analytics"' > .streamlit/secrets.toml
```

**Option B: Use environment variable**
```bash
export DB_URL="postgresql://yourusername:yourpassword@localhost:5432/analytics"
```

**Option C: Use default**
The app will try: `postgresql://kshitijsharma@localhost:5432/analytics`

## Step 3: Run the App

```bash
streamlit run app.py
```

The app will open automatically in your browser at `http://localhost:8501`

## What You'll See

1. **Sidebar:**
   - Connect to Database button
   - Sync New Orders button
   - Database statistics

2. **Main Area Tabs:**
   - **SQL Query** - Execute custom SQL queries
   - **Orders** - Browse orders table with pagination
   - **Order Items** - Browse order items
   - **Customers** - Browse customers
   - **Restaurants** - Browse restaurants
   - **Order Taxes** - Browse tax records
   - **Order Discounts** - Browse discount records

## First Steps

1. Click "🔌 Connect to Database" in the sidebar
2. Click "Sync New Orders" to sync latest data
3. Go to "SQL Query" tab and try:
   ```sql
   SELECT * FROM orders ORDER BY created_on DESC LIMIT 10;
   ```
4. Browse tables using the tabs

## Docker Alternative

If you prefer Docker, see `DOCKER_README.md` for complete Docker setup:

```bash
# Quick start with Docker Compose
docker-compose up -d

# Or using Makefile
make up
```

## Troubleshooting

**App won't start:**
- Make sure Streamlit is installed: `pip install streamlit`
- Check Python version: `python3 --version` (needs 3.8+)

**Database connection fails:**
- Check PostgreSQL is running
- Verify database credentials
- Test connection: `python3 database/test_connection.py --db-url "your_url"`

**Sync fails:**
- Make sure menu data is loaded first
- Check internet connection (for API access)

**Docker issues:**
- See `DOCKER_README.md` for Docker-specific troubleshooting
- Check container logs: `docker-compose logs app`

For more details, see `APP_README.md` or `DOCKER_README.md`

