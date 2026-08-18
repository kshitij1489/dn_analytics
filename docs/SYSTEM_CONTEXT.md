# System Context & Architecture

> **For AI agents:** Start at [INDEX.md](./INDEX.md) for task routing and token budget. Session conventions: [SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) Part A. Root pointers: [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md).

This document provides the canonical technical overview of the Analytics Project. It is intended for LLMs and developers to understand the system's core behaviors, especially concerning data persistence, menu logic, and the "Brain vs. Muscle" architecture.

## 1. Core Architecture: "Brain vs. Muscle"

The system is designed with a clear separation between durable configuration (the "Brain") and the transient database (the "Muscle").

- **The Brain (Persistent)**: central server state, with local SQLite as the current install's cache/read model
  - The central server is the durable source for cross-install menu assignments, verification flags, merge/undo history, customer merge history, and server-authored forecasts.
  - `data/cluster_state_backup.json` and `data/id_maps_backup.json`, when present, are dev/disaster-recovery export artifacts only. Runtime app paths do not read them, write them, or package them as first-run seeds.
  - **Caveat**: Existing JSON exports can still contain hard-won manual catalog work. Do not delete or overwrite them casually; refresh them only through an explicit support export.

- **The Muscle (Transient)**: one local SQLite analytics database per restaurant profile
  - The original `analytics.db` remains the first restaurant's file. Additional profiles use hashed filenames under `profiles/`; `analytics-control.db` stores the server-managed profile registry, global configuration, and the persisted physical-restaurant selection. A singleton `restaurant_profile_identity` row binds every analytics file and prevents path/restaurant mismatches.
  - A profile database can be wiped and rebuilt from schema plus server pulls. Reset and every state-changing action require one explicitly selected, authorized physical restaurant. **All Stores** (`__all__`) is a local read-only scope over the bound profiles — a query-time federation, never a database (see [SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) Part B).
  - **Boot / reset sequence**: `make start`, `make verify`, and reset flows create the SQLite schema if needed. **Catalog** (`menu_items`, `variants`) is **not** auto-seeded from bundled JSON on routine paths. On first Sync DB with cloud configured, `menu_bootstrap_sync` pulls the catalog snapshot from the server **before** POS order import; assignment snapshot + event tails follow after orders (see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §4–§6). Without cloud, orders create an organic catalog via clustering (degraded mode). Per-order-item rows in `menu_item_variants` are filled from the server's watermarked assignment snapshot (`menu_assignment_bootstrap.py`). **Contingency-only** JSON restore is explicit: `python scripts/seed_from_backups.py --restore --from <dir> --yes` for catalog + assignments, or add `--catalog-only`; `seed_and_relink_orders` bootstrap mode remains an explicit support mode — not the default fresh-install path.
  - **Cloud note**: Dachnona cloud/server code uses PostgreSQL. Do not assume local SQLite IDs are durable across rebuilds or installs. After the §25 catalog cutover, the desktop `global_menu_*` tables are a wire cache of the singleton server catalog; first sync on this client wipes a pre-cutover projection via `cache_epoch` and re-bootstraps from snapshot plus event tail.

### Restaurant-scoped desktop routing (Phase 1, 2026-08-08)

The allowed restaurant list comes only from `GET /analytics/restaurants/`. Local code sends the exact selected ID as `X-Analytics-Scope`; the backend resolves it through the control registry, opens that profile's canonical path, verifies the identity row, and then adds `X-Restaurant-ID` to every scoped central request. Missing identity, `__all__`, unauthorized sync/mutation attempts, mixed existing databases, and profile/path mismatches fail closed before ingest. Cached unauthorized profiles remain selectable for offline read-only analytics and are never deleted automatically.

App-global API/connection/UI settings live in `analytics-control.db` and resolve through one reader (`resolve_config_values`), which falls back to a profile's legacy `system_config` rows so a pre-Phase-1 install keeps working until the one-time copy runs. Order cursors, collaboration cursors, revisions, assignment/bootstrap flags, forecast cache/cursors, sync identity, the menu-bootstrap dedup hash, and the derived weather CSV export are profile-local. Background sync captures an immutable selected profile before opening its connection, so a later selector change cannot redirect the job; the selector itself is disabled while a mutation is awaiting its central commit, and any response that arrives after a switch is discarded rather than rendered against the new store.

Uploads are split by scope: the menu-bootstrap seed carries the profile's `X-Restaurant-ID`, per-profile learning/conversation telemetry stays unscoped on the wire, and app-global error-log files upload once per cycle (`client_learning_shipper.run_all(..., include_global_uploads=...)`).

### All Stores federation (Phase 2, 2026-08-09)

`X-Analytics-Scope: __all__` resolves to an immutable snapshot of the authorized, bound profiles for that one request. Each profile is opened **read-only**, the ordinary single-store query runs against it, and an explicit per-endpoint reducer combines the results: counts and money sum, every average/share/rate is recomputed from combined atoms, and row tables become an attributed sorted union with `<restaurant_id>:<local key>` row keys. Menu surfaces are global-only: Group Catalog de-duplicates canonical group tables, menu analytics group by global IDs, and unlinked local rows are omitted with explicit identity-coverage metadata rather than name-merged. Forecast rows still group by their durable dimensions. Customers stay profile-qualified — nothing is merged across stores. A profile that cannot be read is returned in `incomplete_profiles` and is never counted as zero. Every business date is computed in that profile's own timezone from one captured instant.

State-changing routes, one-store drilldowns, SQL Console, and AI Mode answer `409 single_restaurant_required` before opening a database. Sync DB in All Stores mode freezes the store list, then runs the full single-store sync once per restaurant, sequentially, reporting `completed` / `partial` / `failed` with per-store detail. Full reference: [SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) Part B.

### Cloud commit authority (menu & customer)

When cloud sync is configured and strict mode is active on a scope, the central server (`db.dachnona`, PostgreSQL) is the **commit authority** for:

- Menu **assignments**, **verification flags**, and **merge/undo history** (human merge, undo, remap, resolve, verify).
- Customer **merge/undo history** and merged customer identity.

Local SQLite holds a cached copy; human edits in strict mode commit locally **only after** the server accepts the mutation. JSON exports are explicit dev/disaster-recovery artifacts — not routine runtime output or ground truth for per-install assignment or customer-merge state. **Strict mode is live in prod for both scopes since 2026-07-07**, and sync is **strict-only**: the commit endpoints are the only writers. See [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §12 (menu), §14 (customer), and §9 (customer replay quarantine).

### Forecasting (pull-only desktop)

**Complete (2026-07-09).** Forecast model training runs on the central server only. The desktop app pulls server-authored forecast deltas during Sync DB (`forecast_sync.py`), caches them in `central_forecast_*` SQLite tables, and renders charts from that cache via `central_forecast_projection.py`. The desktop must not train, backtest, upload, or repair forecasts locally. Revenue forecasts are live from central; item/volume families populate when central publishes them (assignment locator coverage prerequisite). See [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md).

## 2. Menu Management Logic

### Item Merging
- **Behavior**: Merging Item A (Source) into Item B (Target) transfers all historical revenue/sales stats to B and deletes A.
- **Type Handling**: The merged item always inherits the **Target's Type**. (e.g., merging "Dessert" into "Ice Cream" results in an "Ice Cream" item).
- **Price Adoption**: Users can optionally choose to have the Target inherit the Source's prices for shared variants.
- **Persistence**: A merge action updates the DB mapping and, via the assignment sync stream, converges across installs — see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md). Routine runtime no longer writes JSON backup artifacts.

### Legacy Fallback & Suggestions
- **Primary Logic**: The system first checks the DB for an existing mapping.
- **Fallback**: If (and only if) a raw item name is completely new, the system calls the legacy regex logic (`clean_order_item.py`) to generate a "Suggestion".
- **Status**: These suggestions are saved to the DB with `is_verified=False`. They do *not* become authoritative until a user manually verifies or edits them in the UI and that decision is committed/synced.

## 2b. Order Ingest & Replay (why the pipeline is the way it is)

Raw POS orders become local rows through `services/load_orders.process_order`. Two properties are load-bearing and easy to break — see [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md) for detail:

- **Atomic ingest.** An order's header + items + addons + taxes + discounts commit as **one transaction**. The header is *not* committed early, because the incremental sync watermark is `MAX(stream_id)`; a premature header commit would advance it past a partially-loaded order. Do not add mid-order commits.
- **Idempotent replay.** PetPooja re-sends the same `orderID` (new `stream_id`) on every edit/cancel. Re-ingest **wipes and rebuilds** the order's child rows and **recomputes** affected `menu_items` and `customers` aggregates from source rows, rather than incrementing. Denormalized counters therefore have **two code paths** (inline increment on first ingest; recompute on replay) that must stay consistent.

## 3. Database Schema Highlights

- **`menu_items`**: The specialized catalog of distinct products.
  - **IDs**: `menu_item_id` is a UUID (`uuid5`, see `utils/id_generator.py`), deterministically derived from the item name. IDs are *transient* across rebuilds in the sense that a rebuild can regenerate them from scratch; names are the permanent reference.
  - **Denormalized counters**: `total_sold`, `sold_as_item`, `sold_as_addon`, `total_revenue`, … accumulate on ingest and are recomputed on replay (see §2b). `sold_as_addon` is accumulate-only.
- **`menu_item_variants`**: Junction table holding prices, eligibility flags, and (for order items) the per-order-item assignment (`order_item_id` PK — see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §3.1). `addon_eligible` is **derived from observed usage** each sync (sticky 0→1), not configured.
- **`order_items` / `order_item_addons`**: Per-order line items and their addons. Both carry `menu_item_id` / `variant_id` mappings plus `match_method` / `match_confidence` provenance. Wiped-and-rebuilt on order replay.
- **`mapping_id_cores` / `mapping_anomalies`**: Local-only diagnostics for the silent-reuse guard (see [item_clustering.md](./item_clustering.md)). Not exported to backups, not shipped to cloud.

## 4. Important Caveats for Future Development

1.  **Never delete recovery exports casually**: Do not delete or overwrite existing `cluster_state_backup.json` / `id_maps_backup.json` exports without explicit user intent (see section 1). They are no longer runtime truth, but may be the only offline copy of historical manual catalog work.
2.  **Database Context Matters**: Local app code uses SQLite; Dachnona cloud/server code uses PostgreSQL. Check which side you are editing before applying database-specific assumptions.
3.  **Don't break ingest atomicity or the two stat paths**: When editing `load_orders.py`, keep the single end-of-order commit and update *both* the inline-increment and recompute paths for any denormalized counter. See [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md) §8 for the full checklist.
