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

## 🚀 Getting Started

### Quick Start
This project uses **SQLite**. No Docker or external database is required.

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the Backend Server
make start
# (Runs on http://localhost:8000)

# 3. Start the Frontend (Electron/React)
cd ui_electron && npm install && npm run dev
```

## ⚙️ System Configuration

The application features a centralized **Configuration** page within the UI where all system-wide settings are managed. These settings are persisted in the database and take effect immediately.

### 🧠 AI Models
- **OpenAI API Key**: Required for AI features (Intent Classification, SQL Generation).
- **Model Selection**: Choose your preferred model (e.g., `gpt-4o`, `gpt-4-turbo`).
- **Test Connection**: Use the inline test button to verify your API Key and Model compatibility before saving.

### 🔌 Integrations
- **Orders Service**: Configure the Webhook URL and API Key for syncing order data.
- **Fail-safe Sync**: The system implements an automatic retry mechanism (max 3 attempts) for data syncing. If a connection fails persistently, an error popup will notify you.
- **Verification**: Use "Test Connection" to validate integration URLs and authentication.

### 🚀 Implementation Details
- **Dynamic Loading**: API Keys and URLs are fetched per-request from the `system_config` table. No backend restart is needed after changing settings.

### Data Management
Use the provided `Makefile` for common tasks:

- **`make sync`**: Fetch new orders from the API (incremental update).
- **`make reload-all`**: Fetch ALL orders (full refresh).
- **`make clean`**: Delete and reset the local database (`analytics.db`).
- **`make verify`**: Check database connection and schema status.


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
- **"App Custom Be Opened" (macOS)**: See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for Gatekeeper workaround.
- **Database Reset**: Run `make clean` to wipe the DB, then restart the server.
- **New Orders**: Run `make sync` to fetch incremental orders.

