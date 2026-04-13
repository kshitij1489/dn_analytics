# Dachnona / Cloud Sync: Gap Analysis & Multi-User Collaboration Plan

**Audience:** Engineers extending the Dachnona backend (`db1-prod-dachnona.store`, `desktop-analytics-sync` APIs) and the Electron analytics client.  
**Date:** April 2026  
**Scope:** What the client already pushes/pulls, what is **not** replicated (including parent-stream / merge-related state), multi-operator safety, and a phased plan to make cross-machine sync reliable.

---

## 1. Terminology

| Name | Role in this repo |
|------|-------------------|
| **Upstream / “parent” stream** | PetPooja (or JSON replay) order events consumed by `services/load_orders.py` / `sync_database` — the source of `orders`, `order_items`, `customers`, etc. |
| **Dachnona** | Hosted services under `*.dachnona.store`: e.g. PetPooja webhook proxy (`/webhooks/petpooja/...`) and the **desktop analytics sync** base URL configured as `cloud_sync_url` (documented paths under `/desktop-analytics-sync/...`). |
| **Local brain (menu)** | `data/id_maps_backup.json` + `data/cluster_state_backup.json`, produced by `export_to_backups()` and used by `perform_seeding()` and `menu_bootstrap_shipper`. |
| **Local merge journals** | SQLite tables `merge_history` (menu item merges) and `customer_merge_history` (customer merges). |

---

## 2. What the client already syncs with the cloud

### 2.1 Push (client → Dachnona)

Orchestrated by `src/core/client_learning_shipper.py` (`run_all`), invoked on a timer from `src/core/services/cloud_sync_scheduler.py` (~5 minutes) when `cloud_sync_url` / API key are set, and manually via `POST /operations/client-learning` (see `src/api/routers/operations.py`).

| Payload | Source | Notes |
|---------|--------|--------|
| Errors | Error shipper | Crash / error records. |
| Learning | `ai_logs`, `ai_feedback` (+ Tier 3 aggregates, cache stats) | Rows with `uploaded_at IS NULL` are sent then marked uploaded. |
| Menu bootstrap | **Files only**: `id_maps_backup.json`, `cluster_state_backup.json` | **Not** a live SQL dump of all menu tables. |
| Forecasts | `forecast_cache`, `item_forecast_cache`, `volume_forecast_cache`, backtest tables | Unsent rows (`uploaded_at IS NULL`), then marked. |
| Conversations | `ai_conversations` / `ai_messages` | Async cycle in scheduler. |

Attribution: `uploaded_by` is taken from **`app_users` with `is_active = 1 LIMIT 1`** — effectively a **single** “current” operator per database file.

### 2.2 Pull (cloud → client)

| Area | Mechanism | Where |
|------|------------|--------|
| Forecast cache | `GET .../forecasts/bootstrap` | `src/core/forecast_bootstrap.py`, `POST /forecast/pull-from-cloud` |
| Menu bootstrap | **No implemented client pull in this repo** | `scripts/test_server_connection.py` probes `GET .../menu-bootstrap/latest`, but nothing applies that response to SQLite or JSON backups. |

Order ingestion from the parent stream remains **local**: `sync_database` / `load_orders` write directly to SQLite; there is no “pull orders from Dachnona” path in the analytics app (the PetPooja proxy is for **triggering** upstream sync, not for merging operator edits).

---

## 3. Parent stream vs local merge / menu state

### 3.1 Customer merges and upstream orders

**Local behavior is correct *if* `customer_merge_history` exists on that machine.**

`services/load_orders.py` defines `resolve_active_customer_target()` so that when an order references a customer identity that was previously merged **as a source**, new rows resolve to the **target** customer before insert/update.

**Gap:** `customer_merge_history` (and the full suggestion/undo payload in `suggestion_context`, `moved_order_ids`, etc.) is **never** uploaded in the menu-bootstrap or learning payloads. Another desktop that only pulls forecasts (or nothing) **will not** apply the same resolution unless it receives that history (or a derived “canonical customer map”) from the cloud.

**Risk:** Two machines ingest the same upstream stream; machine A merges customers; machine B never sees `customer_merge_history` → B can attach new orders to the **old** source customer row, diverging from A’s analytics and from any reporting keyed by `customer_id`.

### 3.2 Menu merges and upstream order items

Menu resolution for new orders goes through `OrderItemCluster` (`services/clustering_service.py`), which reads **`menu_item_variants` (+ `menu_items`)** in SQLite. Those relationships are **reflected** in `cluster_state_backup.json` / `id_maps_backup.json` when `export_to_backups()` runs (after merges, `utils/menu_utils.py` calls `export_to_backups`).

**What the JSON backups do *not* include**

- `merge_history` rows (audit trail, undo payloads, variant-merge details).
- Full richness of `menu_items` (e.g. counters, `suggestion_id`, flags) beyond what export reads for id maps.
- Any ad-hoc rows that exist only in DB and were not re-exported.

**Gap:** Pushing menu bootstrap helps **cold start / clustering** on another install **only after** the cloud stores the latest JSON and the other client **implements pull + apply** (today: **no pull**). Without pull, operators still fork.

### 3.3 Similarity / merge eligibility (customer)

`fetch_active_similarity_population` and merge preview use `active_customer_filter()` (`src/core/queries/customer_query_utils.py`), which excludes customers that appear as **`source_customer_id` in `customer_merge_history` with `undone_at IS NULL`**.

That logic is **entirely local**. If merge history is missing on a clone, merged sources can reappear in duplicate detection — bad UX and risk of double-merge attempts (mitigated partly by DB unique index on active source merges **per database**).

---

## 4. Multi-user / multi-machine reality check

### 4.1 Current model

- One SQLite file per deployment is assumed for writes; cloud sync is **append / snapshot upload**, not a CRDT or transactional sync protocol.
- **`app_users`**: one active row used for `uploaded_by`. Multiple humans on different machines are not first-class — you get **last writer** on shared DB, or **forked** databases if each machine has its own file.

### 4.2 Concurrent operations (same DB file)

If several processes ever share one DB (unusual but possible):

- Menu merges and customer merges use transactions but **no distributed lock** with the cloud.
- Background shipper can upload **partial** JSON if another transaction is mid-merge (rare race).

### 4.3 Conflict handling today

**There is no explicit conflict model** for:

- Two different menu merges affecting overlapping raw keys.
- Two customer merges with incompatible graphs (e.g. chain vs star).
- Forecast / training: multiple `full_retrain` jobs — mitigated only by `forecast_training_status` skipping cloud sync while training, not by merging model artifacts on the server.

The cloud ingest tables described in `docs/FORECASTING_AND_SYNC.md` use `UNIQUE(..., employee_id)` for forecasts — that assumes **employee-scoped** rows, not a single global merged state per store.

---

## 5. Summary table: replication vs merge / functionality

| Artifact | Pushed to cloud? | Pulled by client? | If missing on machine B |
|----------|------------------|-------------------|-------------------------|
| `id_maps_backup.json` / `cluster_state_backup.json` | Yes (when shipper runs & files exist) | **No** | B’s clustering diverges from A. |
| `merge_history` | **No** | **No** | No undo / audit parity; harder to debug lineage. |
| `customer_merge_history` | **No** | **No** | **New orders may attach to pre-merge customers** on B. |
| `customer_addresses` | Only indirectly if merged into target before export N/A addresses live in DB only | **No** | Address books diverge. |
| Forecast caches | Yes (unsent rows) | Yes (bootstrap) | Mostly covered for **forecasts only**. |
| Trained model binaries / LightGBM pickles | Out of scope of current shippers | **No** in generic sync | Each machine trains locally unless you add artifact sync. |

---

## 6. Recommendations for the Dachnona backend (“store all information”)

To align the **server** with what operators need for parity:

1. **Persist full ingest payloads** (raw JSON body + `uploaded_by` + device/install id + schema version), not only normalized columns — enables replay and forensic recovery.
2. **Version menu bootstrap blobs** per `store_id` / `restaurant_id` with `updated_at`, `content_hash`, and **optional** `parent_stream_cursor` at time of export.
3. **Add authoritative merge journals** (or materialized views) on the server:
   - **Menu:** sequence of merges (source UUID → target UUID, variant mapping, actor, timestamp).
   - **Customer:** same, plus optional link to upstream identity keys used by clients.
4. **Define conflict policies** (see §7) and return **409 / structured conflicts** when ingest ordering violates invariants.

---

## 7. Conflict scenarios to design for

| Scenario | Example | Suggested policy |
|----------|---------|------------------|
| Duplicate merge | A→B merged twice | Idempotent: second request no-ops; server stores merge_idempotency_key. |
| Fork merge | A→B on device 1, A→C on device 2 | Reject or require manual resolution; surface both in admin UI. |
| Merge then upstream “revives” source | Old `customer_id` in payload after merge | Client already resolves via `resolve_active_customer_target`; server should **canonicalize** IDs on ingest if it ever replays orders. |
| Menu merge + new item with old raw name | Re-clustering | Last bootstrap wins **per key** with vector clock, or server-side three-way merge (high effort). |
| Forecast | Two employees upload same date | Already `UNIQUE(..., employee_id)` — clarify whether analytics should **aggregate** or **pick primary** per store. |

---

## 8. Phased implementation plan

### Phase A — Inventory & contracts (short)

- [ ] Document **store / restaurant** identifier on every ingest payload (today some paths only send `uploaded_by`).
- [ ] List **all** SQLite tables that influence operator-visible truth; mark push/pull/never.
- [ ] Align `FORECASTING_AND_SYNC.md` with actual Dachnona Django models (including any new tables).

### Phase B — Client pull for menu bootstrap (high value, medium effort)

- [ ] Implement `fetch_menu_bootstrap(conn, endpoint, auth)` mirroring `forecast_bootstrap`:
  - GET latest (or since `cursor`) from `/desktop-analytics-sync/menu-bootstrap/latest`.
  - Write JSON to `data/` **or** apply directly to `menu_items` / `variants` / `menu_item_variants` with clear precedence rules.
- [ ] Add API route e.g. `POST /operations/pull-menu-bootstrap` and UI control (“Pull menu state from cloud”).
- [ ] After successful pull, optionally **re-run** `export_to_backups` so local files match DB.

### Phase C — Replicate merge journals (high value for correctness)

- [ ] Extend ingest API (or add `/desktop-analytics-sync/merge-events/ingest`) for **append-only** `merge_history` + `customer_merge_history` events (JSON rows).
- [ ] On pull, apply events in `merged_at` order; detect cycles / double-source using DB constraints + server validation.
- [ ] Keep SQLite unique index semantics aligned with server rules (`idx_customer_merge_history_active_source`).

### Phase D — Multi-operator identity

- [ ] Replace `LIMIT 1` on `app_users` with explicit **session / device** selection in the UI, or store `device_id` + `employee_id` in `system_config`.
- [ ] Pass **both** to every shipper payload for auditing.

### Phase E — Training / model artifacts (optional)

- [ ] If “train models” must be shared: define blob storage (S3/GCS) + manifest in Dachnona; **do not** stuff large binaries through JSON ingest.

---

## 9. Quick answers to the original questions

1. **What must change for Dachnona to “store all the information”?**  
   Extend ingest beyond forecasts/learning/menu JSON/errors: at minimum **merge journals + versioning metadata**, ideally **raw payload archive** per upload.

2. **Are we missing parent or parent-table data that breaks merge / DB / behavior?**  
   The **parent order stream** is unchanged; the gap is **replicating local merge state** (`customer_merge_history`, and audit `merge_history`) to other clients. Without that, **customer** identity resolution on other machines is the highest-risk divergence.

3. **Safe push/pull for multiple users?**  
   Push exists for several channels; **pull is largely missing for menu**; **merge state is not synced**; **attribution assumes one active app user**. This is not yet a safe multi-master collaboration story.

4. **Do we track conflicts of multiple merges?**  
   **No** — only local constraints (e.g. unique active source in `customer_merge_history`) and application rules in preview APIs. There is **no** cross-device conflict ledger.

---

## 10. Related code references (for implementers)

- Menu export / seed: `scripts/seed_from_backups.py` (`export_to_backups`, `perform_seeding`).
- Menu bootstrap upload: `src/core/menu_bootstrap_shipper.py`.
- Shipper orchestration: `src/core/client_learning_shipper.py`.
- Scheduler: `src/core/services/cloud_sync_scheduler.py`.
- Customer merge on ingest: `services/load_orders.py` (`resolve_active_customer_target`).
- Customer merge writes: `src/core/queries/customer_merge_queries.py`.
- API sync spec (partial): `docs/FORECASTING_AND_SYNC.md` Part 2.

---

*End of document.*
