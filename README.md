# Analytics Project

This project fetches, cleans, and analyzes order data from the PetPooja webhook server.

## 📂 Project Structure

- **`app.py`**: Streamlit visualization and management UI.
- **`database/`**: SQL schemas and python scripts for data loading.
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
   streamlit run app.py
   ```

## 🛠 Project Architecture

### "Brain vs. Muscle"
- **Brain (`data/item_parsing_table.csv`)**: Single source of truth for item mappings. Preserved across rebuilds.
- **Muscle (PostgreSQL)**: Transient database. Can be wiped (`make clean`) and rebuilt (`make up`) anytime.

### Key Directories
- **`app.py`**: Main Streamlit dashboard.
- **`database/`**: SQL schemas and `load_orders.py` for data ingestion.
- **`data_cleaning/`**: Logic for normalizing menu item names.
- **`scripts/`**: Utilities for fetching and validating data.

## 📚 Documentation
- **System Context**: [SYSTEM_CONTEXT.md](SYSTEM_CONTEXT.md)
- **Database Schema**: [database/schema/00_schema_overview.md](database/schema/00_schema_overview.md)

## 🔧 Troubleshooting
- **App Not Loading**: Ensure you use `http://localhost:8501`.
- **Database Reset**: Run `make clean && make up` to wipe and re-seed the DB.
- **New Orders**: Run `make sync` to fetch incremental orders.

