# Analytics Project

This project fetches, cleans, and analyzes order data from the PetPooja webhook server.

## 📂 Project Structure

- **`ui_electron/`**: Electron + React frontend application.
- **`src/api/`**: FastAPI backend serving REST endpoints.
- **`services/`**: Core business logic and data ingestion scripts (e.g., `load_orders.py`, `clustering_service.py`).
- **`database/`**: SQL schemas.
- **`data_cleaning/`**: Logic for matching raw names to menu items.
- **`data/`**: Configuration files and CSV seeds (e.g., `item_parsing_table.csv`).
- **`utils/`**: Shared utility modules (API client, database helpers).
- **`scripts/`**: One-off scripts and legacy data tools.


## 🚀 Getting Started

### Quick Start (Docker + Local)
```bash
# 1. Start PostgreSQL database
make up

# 2. Start API server (in a separate terminal)
cd src/api && uvicorn main:app --reload

# 3. Start Electron app (in a separate terminal)
cd ui_electron && npm run dev
```

### Manual Setup (Local)
1. **Install Python Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
2. **Install Node Dependencies**:
   ```bash
   cd ui_electron && npm install
   ```
3. **Environment**:
   Set `DB_URL` (e.g., `postgresql://user:pass@localhost:5432/analytics`).
4. **Run API**:
   ```bash
   cd src/api && uvicorn main:app --reload
   ```
5. **Run Electron**:
   ```bash
   cd ui_electron && npm run dev
   ```

## 🛠 Project Architecture

### "Brain vs. Muscle"
- **Brain (`data/item_parsing_table.csv`)**: Single source of truth for item mappings. Preserved across rebuilds.
- **Muscle (PostgreSQL)**: Transient database. Can be wiped (`make clean`) and rebuilt (`make up`) anytime.

### Key Components
- **`ui_electron/`**: Electron + React dashboard.
- **`src/api/`**: FastAPI backend (REST API).
- **`services/`**: Data ingestion (`load_orders.py`) and business logic.
- **`database/`**: SQL schemas.
- **`data_cleaning/`**: Logic for normalizing menu item names.
- **`scripts/`**: Utilities for fetching and validating data.

## 📚 Documentation
- **System Context**: [SYSTEM_CONTEXT.md](SYSTEM_CONTEXT.md)
- **Database Schema**: Full schema in `database/schema.sql`

## 🔧 Troubleshooting
- **App Not Loading**: Ensure API is running on port 8000 and Electron app is started.
- **Database Reset**: Run `make clean && make up` to wipe and re-seed the DB.
- **New Orders**: Run `make sync` to fetch incremental orders.

