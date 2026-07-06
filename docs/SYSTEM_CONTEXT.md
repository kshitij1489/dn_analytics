# System Context & Architecture

> **For AI agents:** Start at [INDEX.md](./INDEX.md) for task routing and token budget. Session conventions: [AI_SESSION_GUIDE.md](./AI_SESSION_GUIDE.md). Root pointers: [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md).

This document provides the canonical technical overview of the Analytics Project. It is intended for LLMs and developers to understand the system's core behaviors, especially concerning data persistence, menu logic, and the "Brain vs. Muscle" architecture.

## 1. Core Architecture: "Brain vs. Muscle"

The system is designed with a clear separation between durable configuration (the "Brain") and the transient database (the "Muscle").

- **The Brain (Persistent)**: durable menu mapping artifacts in `data/`
  - `data/item_parsing_table.csv` is the durable source for item mapping rules when present and must never be deleted or overwritten casually.
  - `data/cluster_state_backup.json` and `data/id_maps_backup.json` are the local export/reseed artifacts for the SQLite **menu catalog** (`menu_items`, `variants`). `cluster_state_backup.json` also carries per-order-item assignment shape for export and contingency restore, but routine auto-seed does not apply those rows.
  - **Caveat**: These artifacts are only updated by explicit user actions or explicit export flows. Automated background processes must not silently replace the durable truth.
  - **Portability**: Preserve these files so a new machine or local DB reset can rebuild the catalog offline. Per-order-item assignments are **not** restored from bundled JSON in normal operation — the central server is ground truth for those (see boot sequence below).

- **The Muscle (Transient)**: local SQLite database (`analytics.db`)
  - The local database can be wiped (`make clean`) and rebuilt from schema plus durable menu artifacts.
  - **Boot / reset sequence**: `make start`, `make verify`, and reset flows create the SQLite schema if needed. When `menu_items` is empty, `scripts/seed_from_backups.py` auto-seeds **catalog only** (`perform_seeding(..., seed_mappings=False)`): `menu_items` and `variants` from `data/id_maps_backup.json` and `data/cluster_state_backup.json` (types from cluster keys). Per-order-item rows in `menu_item_variants` are filled on the next Sync DB from the server's watermarked assignment snapshot (`menu_assignment_bootstrap.py`; see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §5–§6). **Contingency-only** full restore (catalog + assignments from local JSON): run `python scripts/seed_from_backups.py`, or use explicit `seed_and_relink_orders` bootstrap mode — not the default fresh-install path.
  - **Cloud note**: Dachnona cloud/server code uses PostgreSQL. Do not assume local SQLite IDs are durable across rebuilds or installs.

## 2. Menu Management Logic

### Item Merging
- **Behavior**: Merging Item A (Source) into Item B (Target) transfers all historical revenue/sales stats to B and deletes A.
- **Type Handling**: The merged item always inherits the **Target's Type**. (e.g., merging "Dessert" into "Ice Cream" results in an "Ice Cream" item).
- **Price Adoption**: Users can optionally choose to have the Target inherit the Source's prices for shared variants.
- **Persistence**: A merge action automatically creates an alias rule in the `item_parsing_table.csv`, ensuring the source item never "comes back to life" after a rebuild.

### Legacy Fallback & Suggestions
- **Primary Logic**: The system first checks the DB/CSV for an existing mapping.
- **Fallback**: If (and only if) a raw item name is completely new, the system calls the legacy regex logic (`clean_order_item.py`) to generate a "Suggestion".
- **Status**: These suggestions are saved to the DB with `is_verified=False`. They do *not* enter the permanent CSV until a user manually verifies or edits them in the UI.

## 3. Database Schema Highlights

- **`menu_items`**: The specialized catalog of distinct products.
  - **IDs**: `menu_item_id` is a serial counter. IDs are *transient* across rebuilds; names are permanent.
- **`item_parsing_table`**: The mapping engine.
  - Maps `raw_name` -> `cleaned_name`, `type`, `variant`.
- **`menu_item_variants`**: Junction table holding prices and eligibility flags.

## 4. Important Caveats for Future Development

1.  **Never delete Brain artifacts**: Do not delete or overwrite `data/item_parsing_table.csv`, `data/cluster_state_backup.json`, or `data/id_maps_backup.json` without explicit user intent (see section 1). Losing these causes total amnesia — the system reverts to guessing every item from scratch and loses manual merges and fixes.
2.  **Database Context Matters**: Local app code uses SQLite; Dachnona cloud/server code uses PostgreSQL. Check which side you are editing before applying database-specific assumptions.
3.  **No "Version 2"**: Any references to "Version 1" or "Version 2" in legacy comments should be ignored. The current state described here is the baseline.
