# Dachnona / Cloud Sync: Reviewed Gap Analysis & Collaboration Plan

**Audience:** Engineers working on the Dachnona cloud APIs and this analytics client.  
**Date:** April 13, 2026  
**Status:** Reviewed against the current repo implementation, not just the earlier Cursor analysis. Updated to track shipped client-side progress.  
**Primary goal:** Make customer merges portable across machines without inventing a one-off sync path that diverges from the existing push/pull patterns.

---

## 0. Implementation Progress

| Workstream | Status in this repo | Notes |
|-----------|----------------------|-------|
| Phase 1 - Customer merge push | Complete | Local merge/undo actions write append-only sync events and upload through the existing cloud push bundle. |
| Phase 2 - Customer merge pull/apply | Complete on the client | Manual pull route, cursor storage, remote-event idempotency, and deterministic local replay are implemented. |
| Phase 3 - Multi-device attribution | Complete on the client | Persistent `device_id` / `install_id` attribution is generated locally and included in customer/menu merge sync payloads; the active sync identity is exposed in Configuration. |
| Phase 4 - Menu bootstrap pull/apply | Complete on the client | Manual `menu-bootstrap/latest` pull writes the standard backup files, reuses `perform_seeding()`, and can relink local `order_items` from the snapshot. |
| Phase 5 - Menu merge push/pull | Complete on the client | Local menu merge/undo actions emit append-only sync events; scheduler/manual push and manual pull/apply are implemented. |
| Dachnona backend ingest endpoint | Pending externally | Cloud still needs `POST /desktop-analytics-sync/customer-merges/ingest` to receive the Phase 1 payloads. |
| Dachnona backend delta endpoint | Pending externally | Cloud still needs `GET /desktop-analytics-sync/customer-merges` for the Phase 2 pull path to talk to. |
| Dachnona backend attribution persistence | Pending externally | Cloud still needs to persist and surface `device_id` / `install_id` attribution alongside merge events for multi-device auditability. |
| Dachnona backend menu bootstrap latest endpoint | Pending externally | Cloud still needs `GET /desktop-analytics-sync/menu-bootstrap/latest` available for the Phase 4 pull path if it is not already live. |
| Dachnona backend menu merge ingest endpoint | Pending externally | Cloud still needs `POST /desktop-analytics-sync/menu-merges/ingest` to receive Phase 5 payloads. |
| Dachnona backend menu merge delta endpoint | Pending externally | Cloud still needs `GET /desktop-analytics-sync/menu-merges` for the Phase 5 pull path to talk to. |

---

## 1. Executive Summary

The repo currently has **three different sync flows**, and they should not be conflated:

| Flow | Direction | Current entry point | Purpose |
|------|-----------|---------------------|---------|
| **Upstream order sync** | Remote -> local | `POST /api/sync/run` -> `src/core/services/sync_service.py` | Pull PetPooja/order stream data into local SQLite. |
| **Cloud push** | Local -> Dachnona | `src/core/services/cloud_sync_scheduler.py` and `POST /api/sync/client-learning` | Push telemetry, menu bootstrap, customer merges, menu merges, forecasts, and conversations to the cloud. |
| **Cloud pull** | Dachnona -> local | `POST /api/forecast/pull-from-cloud`, `POST /api/orders/customers/merge/pull-from-cloud`, `POST /api/menu/bootstrap/pull-from-cloud`, and `POST /api/menu/merge/pull-from-cloud` | Pull forecast bootstrap, customer merge events, menu bootstrap snapshots, and menu merge events into local SQLite. |

The customer merge problem is **not** an upstream PetPooja sync issue. It is a **cloud replication concern**:

- `customer_merge_history` is stored locally in SQLite.
- New incoming orders correctly honor that local merge history.
- The analytics client now has a push path and a manual pull/apply path for those merges.
- The remaining external dependency is Dachnona backend support for the corresponding ingest and delta endpoints.

The earlier analysis was directionally right on that point, but it missed two important implementation realities:

1. **Pulling raw `customer_merge_history` rows is not enough.** Another machine must apply the same data mutations as the local merge flow, not just insert history rows.
2. **Local numeric IDs are not a safe cloud contract.** `customer_id`, `order_id`, and `address_id` are local SQLite values and can differ across machines.

Because your main pain is customer merge divergence, the high-value work items are:

1. Keep the new **customer merge event push** path stable and validated against the real Dachnona ingest API.
2. Keep the new **customer merge pull/apply** path stable and validated against the real Dachnona delta API.
3. Treat menu bootstrap pull as a secondary track, not the first fix for this problem.

---

## 2. What Exists Today in Code

### 2.1 Upstream Order Sync

| Item | Implementation |
|------|----------------|
| Manual API | `POST /api/sync/run` in `src/api/routers/operations.py` |
| Worker | `sync_database()` in `src/core/services/sync_service.py` |
| Remote source | `fetch_stream_raw(..., endpoint="orders")` |
| Local effects | Writes `orders`, `order_items`, `customers`, related tables; exports menu backups after new orders |

This path is for **PetPooja/order ingestion**, not for Dachnona collaboration.

### 2.2 Cloud Push

| Item | Implementation |
|------|----------------|
| Background loop | `src/core/services/cloud_sync_scheduler.py` |
| Scheduler cadence | Every 300 seconds |
| Base config | `system_config.cloud_sync_url` and `system_config.cloud_sync_api_key` via `src/core/config/cloud_sync_config.py` |
| Main orchestrator | `src/core/client_learning_shipper.py::run_all()` |
| Manual API | `POST /api/sync/client-learning` |

The scheduler currently pushes:

- Errors
- Learning payloads
- Menu bootstrap JSON
- Customer merge events
- Forecast caches/backtests
- Conversations

Important detail:

- `POST /api/sync/client-learning` only runs the `run_all()` bundle.
- **Conversations are pushed separately** by `services/sync_conversations.py`.
- So there is no single manual "push everything to cloud" route today.

### 2.3 Cloud Pull

There are now four real cloud pull paths in the repo:

| Pull path | Manual API | Logic | Data pulled |
|-----------|------------|-------|-------------|
| Forecast bootstrap | `POST /api/forecast/pull-from-cloud` | `src/core/forecast_bootstrap.py` | Revenue, item, and volume forecasts + backtests |
| Customer merge replay | `POST /api/orders/customers/merge/pull-from-cloud` | `src/core/customer_merge_sync.py` | Customer merge apply/undo events replayed into local SQLite |
| Menu bootstrap snapshot | `POST /api/menu/bootstrap/pull-from-cloud` | `src/core/menu_bootstrap_sync.py` | Latest menu bootstrap snapshot, written to local backup JSON and applied via seeding + optional `order_items` relink |
| Menu merge replay | `POST /api/menu/merge/pull-from-cloud` | `src/core/menu_merge_sync.py` | Menu merge / undo events replayed via the existing local menu merge logic |

There is **no implemented client pull** for:

- conversations
- learning/error payloads

`scripts/test_server_connection.py` still probes `GET /desktop-analytics-sync/menu-bootstrap/latest`, but production code now also consumes that response through `src/core/menu_bootstrap_sync.py`.

---

## 3. What Actually Replicates Today

| Artifact | Push to cloud? | Pull from cloud? | Notes |
|----------|----------------|------------------|-------|
| Error logs | Yes | No | Uploaded from JSONL files; files are truncated/deleted on success. |
| `ai_logs`, `ai_feedback` | Yes | No | Rows with `uploaded_at IS NULL` are pushed and then marked. |
| Tier 3 learning payloads | Yes | No | Cache stats, aggregates, schema hash, and incorrect cache entries are posted every run when URL is configured. |
| Menu bootstrap JSON | Yes | Yes | Pull writes the snapshot into the same local backup files and can optionally relink `order_items`; snapshot parity is still limited by what the backup format contains. |
| Forecast caches/backtests | Yes | Yes | Push uses `uploaded_at`; pull uses forecast bootstrap endpoint. |
| Conversations | Yes | No | `synced_at` based push-only flow. |
| `customer_merge_history` | Yes | Yes | Replicated as append-only merge events, not raw history rows. Pull is manual and idempotent. |
| `merge_history` | Yes | Yes | Replicated as append-only menu merge events, not as raw `merge_history` rows. Undo replay stays explicit and idempotent. |
| `customer_addresses` | No direct sync | No | Only local DB state today. |

`uploaded_by` attribution comes from `app_users WHERE is_active = 1 LIMIT 1`, but the current user model is effectively a singleton:

- `GET /api/config/users` returns `LIMIT 1`
- `POST /api/config/users` deletes all rows and re-inserts one row

That is workable for single-device attribution, not for strong multi-operator provenance.

---

## 4. Customer Merge Behavior Today

### 4.1 What Works Locally

The local customer merge flow is coherent.

`src/core/queries/customer_merge_queries.py::merge_customers()` does all of this:

- validates source/target via preview logic
- copies structured addresses to the target customer
- merges target customer fields
- moves existing `orders.customer_id` from source -> target
- records audit/history in `customer_merge_history`
- recomputes aggregates for both customers

Future order ingest also respects local merges:

- `services/load_orders.py::resolve_active_customer_target()` follows active merge chains
- `get_or_create_customer()` calls it before updating or returning a customer

Duplicate detection also respects local merge state:

- `active_customer_filter()` excludes customers that are active merge sources

So the local machine behaves correctly **if it has the merge history**.

### 4.2 Why Another Machine Diverges

`customer_merge_history` is not part of any existing push/pull channel.

That means machine B can have:

- the same upstream orders
- the same customers table shape
- the same customer UI

but still lack the merge decisions made on machine A.

Result:

- merged source customers can reappear in similarity suggestions on B
- new orders on B can keep attaching to the pre-merge customer
- customer KPIs, profiles, and merge history diverge across operators

### 4.3 Important Gap Missing from the Earlier Analysis

Replicating the raw history row is **not enough**.

If machine B already ingested orders before it learns about a merge, then a proper pull/apply flow must do more than insert into `customer_merge_history`. It must also perform the equivalent of the local merge side effects:

- move existing `orders.customer_id`
- merge customer fields onto the target
- copy structured addresses
- recompute customer aggregates

Otherwise machine B will have the history row but still keep the wrong analytics state.

### 4.4 Local IDs Are Not a Safe Cloud Contract

This is the biggest design risk for customer merge sync.

The current table stores local SQLite identifiers:

- `source_customer_id`
- `target_customer_id`
- `moved_order_ids`
- address IDs inside undo context

Those values are not guaranteed to be portable across machines.

Reasons:

- `customer_id` is an autoincrement SQLite key
- `order_id` is an autoincrement SQLite key
- `address_id` is an autoincrement SQLite key
- anonymous customer identity keys are generated with random UUIDs

So a cloud API that blindly ships `customer_merge_history` rows between machines is brittle. The cloud contract needs **portable locators**, not only local row IDs.

Recommended portable fields for customer merge events:

- `remote_event_id` or idempotency key
- store/tenant identifier
- source and target customer snapshots
- source and target stable identity locators where available
  - phone hash
  - name + address hash
  - existing `customer_identity_key` when it is deterministic
- merge metadata
  - `merged_at`
  - `undone_at`
  - `similarity_score`
  - `model_name`
  - `suggestion_context`
- optional portable order refs if needed for undo
  - `petpooja_order_id`
  - `event_id`
  - `stream_id`

The receiving client should resolve those portable locators to its local `customer_id` values before applying the merge.

---

## 5. Menu Bootstrap and Menu Merge Reality

### 5.1 What the Snapshot Contains

`scripts/seed_from_backups.py::export_to_backups()` exports:

- `id_maps_backup.json`
- `cluster_state_backup.json`

Those files primarily represent:

- menu item IDs and names
- variant IDs and names
- item type IDs
- mapping state keyed by `order_item_id`

This is useful, but it is **not** a full menu-state database export.

### 5.2 What It Does Not Contain

The current menu bootstrap snapshot does **not** fully preserve:

- `merge_history`
- `order_item_addons` remaps
- menu counters and all operational flags
- `suggestion_id` and other richer menu metadata
- an explicit authoritative audit trail of menu merges

### 5.3 What `perform_seeding()` Actually Restores

This matters a lot for any future menu pull feature.

`perform_seeding()` restores only:

- `menu_items`
- `variants`
- `menu_item_variants`

It does **not** rewrite:

- `order_items`
- `order_item_addons`
- `merge_history`

That means menu bootstrap pull is good for:

- a fresh install
- an empty DB before order replay
- helping future clustering start closer to the desired state

But it is **not** a complete reconciliation strategy for an already-populated database.

So if you later add menu pull, do not assume:

`GET latest snapshot` -> `perform_seeding()` -> "all menu analytics now match"

That is not true with the current code.

### 5.4 What This Means for Priority

Because your immediate pain is customer merge divergence, **customer merge sync should be built first**.

Menu bootstrap pull is still useful, but it does not solve the customer merge problem and it is only a partial menu-state reconciliation path anyway.

---

## 6. Confirmed Bugs and Gaps in the Existing Sync APIs

### 6.1 Confirmed and Fixed in This Review

During this review I found a real upgrade-path sync bug in startup migrations:

- older startup code only backfilled forecast `uploaded_at` on some backtest tables
- volume forecast/backtest sync on upgraded databases could silently skip rows if the column was missing

This is now fixed in:

- `src/api/main.py`

Startup now attempts to add `uploaded_at` to all forecast cache/backtest tables.

### 6.2 Confirmed Existing Gaps Still Present

1. **Customer merge sync now exists client-side, but still depends on Dachnona backend support**
   - The analytics client can now push merge events and manually pull/apply them.
   - The remaining gap is server-side ingest + delta APIs.

2. **Menu bootstrap pull now exists client-side, but its parity is bounded by snapshot shape**
   - The client can now consume `menu-bootstrap/latest`, write the local backup files, seed menu tables, and relink `order_items`.
   - Snapshot pull still cannot restore addon remaps or raw menu merge audit rows because that data is not in the snapshot format.

3. **No manual conversations push endpoint**
   - Conversations are scheduled, but not included in `POST /api/sync/client-learning`.

4. **Menu bootstrap is fire-and-forget**
   - No local `uploaded_at`
   - No content hash
   - No dedupe
   - No change detection

5. **Employee identity is still singleton-style, but device/install attribution is now added**
   - `app_users` still behaves like a singleton.
   - The client now also emits persistent `device_id` / `install_id` metadata so cloud-side auditability is no longer limited to the singleton employee row.

6. **Customer merge payload fields are local-machine-oriented**
   - Good for local undo
   - Unsafe as a direct cloud replication contract

---

## 7. Recommended Customer Merge Sync Design

This section is the practical answer to "how do we make this new API consistent with the existing app?"

### 7.1 Stay Consistent with Existing Patterns

Use the same split the app already uses:

- **Push** goes into the existing scheduler/orchestrator path.
- **Pull** is a feature-specific manual API, similar to forecast bootstrap.

Recommended client structure:

| Concern | Recommended place |
|---------|-------------------|
| Customer merge push shipper | New module, e.g. `src/core/customer_merge_shipper.py` |
| Push orchestration | Add to `src/core/client_learning_shipper.py::run_all()` |
| Background scheduling | Reuse `src/core/services/cloud_sync_scheduler.py` |
| Manual pull API | Add customer-merge-specific route, e.g. in `src/api/routers/orders.py` |
| Pull/apply logic | New module, e.g. `src/core/customer_merge_sync.py` |

### 7.2 Recommended Push Behavior

Add a customer merge shipper that selects merge events not yet uploaded.

Two implementation options:

| Option | Notes |
|--------|-------|
| Add `uploaded_at` to `customer_merge_history` | Simple, but undo events need special handling because the row changes after initial upload. |
| Add a dedicated outbound event log/state table | Cleaner for append-only sync and future conflict handling. |

For consistency with the rest of the app, I would prefer:

- append-only merge events
- explicit event IDs
- idempotent ingest on the server

At minimum, each pushed event should include:

- `remote_event_id`
- `schema_version`
- `uploaded_by`
- optional `device_id` / `install_id`
- source/target portable locators
- snapshots
- merge metadata
- undo metadata if applicable

### 7.3 Recommended Pull Behavior

Mirror the forecast pull pattern, but do not make it generic too early.

Recommended manual API shape:

- `POST /api/orders/customers/merge/pull-from-cloud`

Why this shape:

- Forecast pull is feature-specific, not under `/api/sync`
- Customer merges are owned by the customer feature
- This keeps feature code near the customer router and customer queries

The pull path should:

1. request merge events from the server using a cursor
2. resolve cloud locators to local customer rows
3. apply the merge or undo with deterministic local mutations
4. store the remote cursor locally, likely in `system_config`
5. mark applied remote events idempotently

### 7.4 Do Not Reuse `merge_customers()` Blindly for Remote Events

Local interactive merge and remote event replay are related, but not identical.

For remote replay, you likely want a dedicated function such as:

- `apply_customer_merge_event(...)`
- `apply_customer_merge_undo_event(...)`

Reasons:

- remote apply must be idempotent
- remote apply must tolerate already-moved local data
- remote apply may need to resolve customers by portable keys, not by local IDs
- remote apply should preserve the original event metadata

### 7.5 Server Contract Needed from Dachnona

Minimum server-side features:

- ingest endpoint for customer merge events
- delta pull endpoint for customer merge events
- idempotent event handling
- stable cursor or `updated_after`
- store/tenant scoping

Suggested endpoints:

- `POST /desktop-analytics-sync/customer-merges/ingest`
- `GET /desktop-analytics-sync/customer-merges`

or one generic merge-events family if the server prefers that.

---

## 8. What the Dachnona Backend Must Add

If the server only keeps the current baseline endpoints, it can continue storing:

- errors
- learning payloads
- menu bootstrap snapshots
- forecasts
- conversations

But to solve the customer merge collaboration problem, Dachnona needs new persistence and APIs for merge events.

| Need | Why it is required |
|------|--------------------|
| Customer merge event table(s) | Current client never pushes this state today. |
| Idempotency key / `remote_event_id` | Required so repeated uploads do not duplicate merge actions. |
| Delta pull API | Required so another machine can learn about merges after they happen. |
| Tenant/store scoping | Required so events do not mix across restaurants/stores. |
| Optional raw payload archive | Very useful for debugging and replay. |

For customer merges specifically, the server should not normalize away too much too early. Keeping the raw payload is valuable because the local client logic is richer than just `source_id -> target_id`.

---

## 9. Priority Plan

The previous version prioritized menu bootstrap pull first. For your stated goal, that is the wrong order.

### Phase 1 - Customer Merge Push

Status: Complete in the analytics client.

- add client-side customer merge shipper
- add server ingest endpoint/table
- integrate push into `client_learning_shipper.run_all()`
- include result in `POST /api/sync/client-learning`

### Phase 2 - Customer Merge Pull and Apply

Status: Complete in the analytics client.

- add server delta endpoint
- add client manual pull route
- add local cursor storage
- implement deterministic apply/undo logic for remote events

### Phase 3 - Multi-Device Attribution

Status: Complete in the analytics client. Dachnona backend persistence/reporting is still pending externally.

- add `device_id` / `install_id`
- stop treating `app_users` as sufficient multi-device identity
- send both employee identity and device identity with merge events

### Phase 4 - Menu Pull, If Still Needed

Status: Complete in the analytics client. Depends on Dachnona serving `menu-bootstrap/latest`.

- implement `menu-bootstrap/latest` consumption
- decide whether snapshot apply is only for fresh installs or also for populated DBs
- if populated DB parity matters, extend apply logic beyond `perform_seeding()`

### Phase 5 - Optional Menu Merge Event Sync

Status: Complete in the analytics client. Dachnona ingest/delta endpoints are still pending externally.

- only after deciding whether menu bootstrap snapshots are enough
- if not enough, add explicit menu merge event ingest/pull

---

## 10. Quick Answers to the Original Questions

1. **Why does customer merge stay local today?**  
   Because `customer_merge_history` is only stored in local SQLite and is not part of any current cloud push/pull flow.

2. **Is this missing from the routine sync/push/pull path?**  
   It was missing when this plan was first written. In the current repo, cloud push now covers customer merges and menu merges, and cloud pull covers forecasts, customer merges, menu bootstrap snapshots, and menu merges. The remaining gap is the corresponding Dachnona backend support.

3. **Will menu bootstrap pull solve the customer merge problem?**  
   No. Customer merges need their own replicated event/apply path.

4. **What should be built first?**  
   Customer merge event push + pull/apply, integrated into the existing scheduler and shipper architecture.

5. **What is the biggest technical risk if we do this naively?**  
   Using local SQLite IDs as if they were portable cloud identifiers.

---

## 11. Code References

- Upstream sync: `src/core/services/sync_service.py`
- Manual upstream sync API: `src/api/routers/operations.py`
- Customer merge push shipper: `src/core/customer_merge_shipper.py`
- Customer merge pull/apply: `src/core/customer_merge_sync.py`
- Customer merge manual pull API: `src/api/routers/orders.py`
- Scheduler: `src/core/services/cloud_sync_scheduler.py`
- Cloud push orchestrator: `src/core/client_learning_shipper.py`
- Learning shipper: `src/core/learning_shipper.py`
- Menu bootstrap shipper: `src/core/menu_bootstrap_shipper.py`
- Forecast shipper: `src/core/forecast_shipper.py`
- Forecast pull: `src/core/forecast_bootstrap.py`
- Conversations sync: `services/sync_conversations.py`
- Customer merge writes: `src/core/queries/customer_merge_queries.py`
- Customer merge history schema: `database/schema_sqlite.sql`
- Customer merge behavior on ingest: `services/load_orders.py`
- Menu backup export/seed: `scripts/seed_from_backups.py`

---

*End of reviewed plan.*
