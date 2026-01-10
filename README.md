# Analytics Project

This project fetches, cleans, and analyzes order data from the PetPooja webhook server.

## 📂 Project Structure

- **`data/`**: cleaned data files (`cleaned_menu.csv`, `orders.json`) and database (`test_analytics.db`).
- **`docs/`**: Documentation.
    - [Setup Guide](docs/setup.md)
    - [Docker Setup](docs/docker.md)
    - [Troubleshooting](docs/troubleshooting.md)
    - [Design Docs](docs/design/)
- **`utils/`**: Shared utility modules (API client).
- **`database/`**: Database loading and schema scripts.
- **`rebuild_menu.py`**: Fetches raw data and regenerates `cleaned_menu.csv`.
- **`fetch_orders.py`**: Fetches raw order payloads.

## 🚀 Quick Start

### Local Setup
Refer to [docs/setup.md](docs/setup.md) for full instructions.

### Docker Setup
Refer to [docs/docker.md](docs/docker.md).
```bash
make up       # Start services
make logs     # View logs
make down     # Stop services
```

## 🛠 Common Tasks

### 1. Rebuild Menu Data
Fetch the latest orders and update the standardized menu:
```bash
python3 rebuild_menu.py
```
Output: `data/cleaned_menu.csv`

### 2. Fetch Raw Orders
```bash
python3 fetch_orders.py
```

### 3. Run Analytics App
```bash
docker-compose up -d
# Open http://localhost:8501
```

## 📚 Documentation
- **Design:** See `docs/design/` for schema discussions.
- **Archive:** Older reports and docs are in `docs/archive/`.
