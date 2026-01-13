# Database Loading Scripts

This directory contains Python scripts for initializing the database schema and loading data from the PetPooja API.

## Core Scripts

- **`load_orders.py`**: The main script for fetching and loading order data.
  - Supports `--incremental` for syncing new orders.
  - Automatically deduplicates customers.
  - Uses `ItemMatcher` which consults the `item_parsing_table`.
- **`load_parsing_table.py`**: Utility to load/sync the `item_parsing_table.csv` into the SQL database.
- **`menu_manager.py`**: Handles menu synchronization and variants.
- **`test_load_menu_postgresql.py`**: Utility to load the standardized menu from `cleaned_menu.csv`.

## 🛠 Menu Management

We use a data-driven parsing system:
1. **`item_parsing_table`**: Stores explicit mappings between Raw Names and Cleaned Attributes.
2. **Conflict Resolution**: New raw items are added as `is_verified=False` and can be managed via the Web UI.
3. **Merging**: `utils/menu_utils.py` provides logic to safely merge duplicate menu items and update all historical records.

## Common Operations

### 1. Sync New Orders
```bash
make sync
```

### 2. Load/Refresh Parsing Rules
```bash
# Load seed data from data/item_parsing_table.csv
python3 database/load_parsing_table.py --user your_user
```

## Schema Reference
See [schema/00_schema_overview.md](schema/00_schema_overview.md) for a detailed description of the table structures.

---
*For full installation instructions, refer to the main [README.md](../README.md).*
