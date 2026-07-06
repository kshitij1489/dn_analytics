# Analytics Project

Desktop analytics app for order ingestion, menu clustering, forecasting, and cloud sync with Dachnona.

## 📂 Project Structure

- **`src/`**: FastAPI backend, API routers, SQLite connection, and cloud sync logic.
- **`ui_electron/`**: Electron + React frontend.
- **`services/`**: Data ingestion, order loading, and clustering helpers.
- **`database/`**: SQLite schema and migration assets.
- **`data/`**: Durable menu/reseed artifacts and local data files.
- **`utils/`**: Shared menu, clustering, and sync utilities.
- **`scripts/`**: Start, build, sync, verification, and release scripts.


## 🚀 Getting Started

### Quick Start (Desktop Dev)

```bash
# 1. Start backend + Electron frontend
make start

# 2. Verify SQLite connection and schema
make verify

# 3. Sync new orders incrementally
make sync
```

### Manual Setup
1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   cd ui_electron && npm install
   ```
2. **Environment**:
   Optional: set `DB_URL` to an alternate SQLite path. By default the app uses `analytics.db`.
3. **Run App**:
   ```bash
   make start
   ```

## 🛠 Project Architecture

### "Brain vs. Muscle"
- **Brain (`data/` durable menu artifacts)**: Persistent mapping/reseed state. Preserve across rebuilds.
- **Muscle (`analytics.db` SQLite)**: Transient local database. Can be wiped (`make clean`) and rebuilt (`make start` / `make verify`) from schema plus durable artifacts.

### Key Directories
- **`src/`**: Backend, API routers, database access, and sync logic.
- **`ui_electron/`**: Desktop frontend.
- **`services/`**: Data ingestion (`load_orders.py`) and business logic.
- **`database/`**: SQL schemas.
- **`scripts/`**: Utilities for fetching and validating data.

## 📚 Documentation

**Start here:** [docs/INDEX.md](docs/INDEX.md) (task routing hub) · [AGENTS.md](AGENTS.md) (AI agent instructions) · [CLAUDE.md](CLAUDE.md) (Claude pointer)

- **System Context**: [docs/SYSTEM_CONTEXT.md](docs/SYSTEM_CONTEXT.md)
- **AI session guide**: [docs/AI_SESSION_GUIDE.md](docs/AI_SESSION_GUIDE.md)
- **Database Schema**: Full schema in `database/schema_sqlite.sql`
- **Build & Share**: [docs/BUILD_INSTRUCTIONS.md](docs/BUILD_INSTRUCTIONS.md)
- **Forecasting & cloud sync**: [docs/FORECASTING_AND_SYNC.md](docs/FORECASTING_AND_SYNC.md)
- **Troubleshooting (macOS app)**: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## 🔧 Troubleshooting
- **App Not Loading**: Run `make backend` and `make frontend` separately to isolate backend vs Electron issues.
- **Database Reset**: Run `make clean && make verify` to wipe and recreate the local SQLite DB.
- **New Orders**: Run `make sync` to fetch incremental orders.
- **macOS "Damaged" or "Unidentified Developer"**: See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## ✨ Functionalities

### 📊 1. Insights & KPIs
- **Real-time Business Health**: Track Revenue, Total Orders, Average Order Value (AOV), and customer growth at a glance.
- **Trend Analysis**: Visual graphs showing sales performance over custom time ranges (daily, weekly, monthly).

### ☀️ 2. Today's Dashboard
- **Live Operation View**: See what is happening in the restaurant *right now*.
- **Hourly Breakdown**: Track sales peaks and troughs hour-by-hour.
- **Top Sellers**: Identify the best-performing items of the current day.

### 🔮 3. Smart Forecasting
- **Sales Predictions**: Uses historical data and weather patterns to forecast future revenue.
- **Algorithm Comparison**: Compares different forecasting models (Prophet vs. Holt-Winters vs. Weekday Average) to find the most accurate prediction.
- **Weather Integration**: Correlates sales with historical weather conditions (Temperature, Rain) for smarter inventory planning.

### 🍽 4. Menu Analytics
- **Pareto Analysis (80/20 Rule)**: Identifies the 20% of items contributing to 80% of revenue.
- **Item Performance**: Detailed breakdown of "Sold Count", "Revenue Share", and "Repeat Rate" for each dish.
- **Variant Tracking**: Analyzes performance of different item sizes/flavors (e.g., Small vs. Large).
- **[Item Clustering Logic](docs/item_clustering.md)**: Intelligent system to normalize messy order names into clean menu items.

### 📝 5. Order Management
- **Centralized Order History**: Searchable database of all past orders from all sources (Swiggy, Zomato, POS).
- **Customer Identity**: Tracks customer lifetime value and repeat purchase behavior.

### 🤖 6. AI Assistant & SQL Console
- **Natural Language Queries**: Ask questions like *"What was the best selling item last Friday?"* and get instant answers.
- **Advanced SQL Mode**: Direct SQL access for power users to run complex custom queries on the dataset.

### 📦 7. Inventory & Operations
- **COGS Analysis**: Understand Cost of Goods Sold.
- **Inventory Tracking**: (In Progress) Monitor stock levels based on sales data.
