# System Context & Architecture

This document provides the canonical technical overview of the Analytics Project. It is intended for LLMs and developers to understand the system's core behaviors, especially concerning data persistence, menu logic, and the "Brain vs. Muscle" architecture.

## 1. Core Architecture: "Brain vs. Muscle"

The system is designed with a clear separation between durable configuration (the "Brain") and the transient database (the "Muscle").

- **The Brain (Persistent)**: `data/item_parsing_table.csv`
  - This file is the **single source of truth** for all item mappings, verifications, and merges.
  - **Caveat**: This file is only updated when the user explicitly clicks "Save Changes" or "Merge" in the UI. Automated background processes do *not* write to it.
  - **Portability**: This file must be preserved. If moved to a new machine, the system will fully restore its state from this file.

- **The Muscle (Transient)**: PostgreSQL Database
  - The database can be wiped (`make clean`) and rebuilt at any time.
  - **Boot Sequence**: On every `make load-menu`, the system *first* reads the CSV to seed the `item_parsing_table` in the DB, *then* processes the menu. This ensures that previous merges and aliases are respected immediately, preventing duplicate items from reappearing.

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

1.  **Never Delete the CSV**: Deleting `data/item_parsing_table.csv` causes total amnesia. The system will revert to guessing every item from scratch, losing all manual merges and fixes.
2.  **Schema Changes in Views**: PostgreSQL prevents dropping columns from views if they are used elsewhere. Always use `DROP VIEW ... CASCADE` when modifying view structure.
3.  **No "Version 2"**: Any references to "Version 1" or "Version 2" in legacy comments should be ignored. The current state described here is the baseline.
