# Database Scripts

This directory contains Python scripts for initializing the database schema and loading data.

## Core Scripts

- **`load_orders.py`**: The main script for fetching and loading order data.
  - Supports `--incremental` for syncing new orders.
  - Automatically deduplicates customers.
  - Uses `ClusteringService` to auto-discover menu items.
- **`menu_manager.py`**: Utilities for menu item management and variant handling.

## 🛠 Menu Management

The system uses an **Auto-Discovery** approach:
1. **Detection**: `ClusteringService` receives an order. If the item name (after cleaning) is new, it creates a new `menu_item` with `is_verified=FALSE`.
2. **Resolution**: Use the CLI tool to review and verify these items.
   ```bash
   python3 scripts/resolve_unclustered.py
   ```
3. **Merging**: `utils/menu_utils.py` / `merge_menu_items` provides logic to safely merge duplicate menu items and update all historical records.

## Common Operations

### 1. Sync New Orders
```bash
make sync
```

### 2. Verify New Items
```bash
python3 scripts/resolve_unclustered.py
```

## Schema Reference
See [schema/00_schema_overview.md](schema/00_schema_overview.md) for a detailed description of the table structures.

---
*For full installation instructions, refer to the main [README.md](../README.md).*
