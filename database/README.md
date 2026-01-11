# Database Loading Scripts

This directory contains Python scripts for initializing the database schema and loading data from the PetPooja API.

## Core Scripts

- **`load_orders.py`**: The main script for fetching and loading order data. 
  - Supports `--incremental` for syncing new orders.
  - Automatically deduplicates customers by phone number.
  - Matches order items to the standardized menu.
- **`menu_manager.py`**: Handles menu synchronization and item matching logic.
- **`test_load_menu_postgresql.py`**: Utility to load the standardized menu from `cleaned_menu.csv`.

## Common Operations

### 1. Sync New Orders
```bash
make sync
# OR
python3 database/load_orders.py --incremental
```

### 2. Reload All Orders
```bash
make reload-all
# OR
python3 database/load_orders.py
```

### 3. Load Menu Data (Post-Migration)
```bash
make load-menu
# OR
python3 database/test_load_menu_postgresql.py
```

## Schema Reference
See [schema/00_schema_overview.md](schema/00_schema_overview.md) for a detailed description of the table structures and relationships.

---
*For full installation instructions, refer to [docs/setup.md](../docs/setup.md).*

