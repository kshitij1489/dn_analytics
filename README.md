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

The recommended way to run this project is using **Docker**.

### Quick Start (Docker)
Ensure you have Docker and Docker Compose installed.

```bash
# 1. Start all services (Database + Web App)
make up

# 2. View application logs
make logs

# 3. Access web UI
# Open http://localhost:8501
```

### Common Docker Commands
- `make build`: Rebuild Docker images.
- `make clean`: Stop and remove all containers and volumes (⚠️ deletes DB data).
- `make psql`: Access the PostgreSQL shell inside the container.
- `make shell`: Access the application container shell.

---

### Manual Setup (Without Docker)
If you prefer to run locally:

1. **Install Dependencies**:
   ```bash
   pip install -r requirements_app.txt
   ```
2. **Environment**:
   Set `DB_URL` environment variable (e.g., `postgresql://user:pass@localhost:5432/analytics`).
3. **Run App**:
   ```bash
   streamlit run app.py
   ```

## 🛠 Features

### 1. Unified Parsing Table
The system uses an `item_parsing_table` to map raw order names to cleaned attributes.
- **Verification UI**: Manage these mappings in the "⚡ Parsing & Conflicts" tab.
- **Auto-Suggester**: Uses fuzzy matching and regex to suggest initial parsings.

### 2. Item Merging
Safe item deduplication logic is available in the "📋 Menu Items" tab.
- Transfers revenue and order history.
- Updates parsing rules automatically.

## 📚 Documentation
- **System Context**: [SYSTEM_CONTEXT.md](SYSTEM_CONTEXT.md)
- **Database Schema**: [database/schema/00_schema_overview.md](database/schema/00_schema_overview.md)

## 🔧 Troubleshooting

### 1. App Not Loading
- **URL**: Use `http://localhost:8501`. (Logs might show `0.0.0.0`, but this is internal).
- **Check Status**: `make ps` or `docker-compose ps`.
- **Logs**: `make logs`.

### 2. Database Issues
- **Connection**: Ensure Postgres is healthy: `docker-compose ps postgres`.
- **Reset**: To start fresh (⚠️ deletes data): `make clean && make up`.

### 3. Changes Not Reflecting
- **Rebuild**: `make build && make up`.
- **Quick Restart**: `make restart`.

