# Analytics Project

This project fetches, cleans, and analyzes order data from the PetPooja webhook server.

## 📂 Project Structure

- **`app.py`**: Streamlit visualization and management UI.
- **`services/`**: Core business logic and data ingestion scripts (e.g., `load_orders.py`, `clustering_service.py`).
- **`database/`**: SQL schemas.
- **`data_cleaning/`**: Logic for matching raw names to menu items.
- **`data/`**: Configuration files and CSV seeds (e.g., `item_parsing_table.csv`).
- **`utils/`**: Shared utility modules (API client, database helpers).
- **`scripts/`**: One-off scripts and legacy data tools.


## 🚀 Getting Started

### Quick Start (Docker)
The recommended way to run this project is using Docker.

```bash
# 1. Start all services (Database + Web App)
make up

# 2. View application logs
make logs

# 3. Access web UI
# Open http://localhost:8501
```

### Manual Setup (Local)
1. **Install Dependencies**:
   ```bash
   pip install -r requirements_app.txt
   ```
2. **Environment**:
   Set `DB_URL` (e.g., `postgresql://user:pass@localhost:5432/analytics`).
3. **Run App**:
   ```bash
   python3 run_app.py
   ```

## 🛠 Project Architecture

### "Brain vs. Muscle"
- **Brain (`data/item_parsing_table.csv`)**: Single source of truth for item mappings. Preserved across rebuilds.
- **Muscle (PostgreSQL)**: Transient database. Can be wiped (`make clean`) and rebuilt (`make up`) anytime.

### Key Directories
- **`app.py`**: Main Streamlit dashboard.
- **`services/`**: Data ingestion (`load_orders.py`) and business logic.
- **`database/`**: SQL schemas.
- **`data_cleaning/`**: Logic for normalizing menu item names.
- **`scripts/`**: Utilities for fetching and validating data.

## 📚 Documentation
- **System Context**: [SYSTEM_CONTEXT.md](SYSTEM_CONTEXT.md)
- **Database Schema**: Full schema in `database/schema.sql`

## 🔧 Troubleshooting
- **App Not Loading**: Ensure you use `http://localhost:8501`.
- **Database Reset**: Run `make clean && make up` to wipe and re-seed the DB.
- **New Orders**: Run `make sync` to fetch incremental orders.

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
- **[Item Clustering Logic](item_clustering.md)**: Intelligent system to normalize messy order names into clean menu items.

### 📝 5. Order Management
- **Centralized Order History**: Searchable database of all past orders from all sources (Swiggy, Zomato, POS).
- **Customer Identity**: Tracks customer lifetime value and repeat purchase behavior.

### 🤖 6. AI Assistant & SQL Console
- **Natural Language Queries**: Ask questions like *"What was the best selling item last Friday?"* and get instant answers.
- **Advanced SQL Mode**: Direct SQL access for power users to run complex custom queries on the dataset.

### 📦 7. Inventory & Operations
- **COGS Analysis**: Understand Cost of Goods Sold.
- **Inventory Tracking**: (In Progress) Monitor stock levels based on sales data.
