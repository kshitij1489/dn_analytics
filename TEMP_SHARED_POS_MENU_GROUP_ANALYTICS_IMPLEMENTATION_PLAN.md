# Temporary Analytics Implementation Plan: Shared Petpooja Menu Group

**Status:** implementation in progress; Phases A–D complete on 2026-08-11 for a controlled clean rebuild  
**Frozen wire contract:** `contracts/central_server_analytics_app_api_contract.md`, revision 1.7, especially §9 and §25  
**Golden fixtures:** `contracts/fixtures/1/`  
**Deployment model:** one controlled desktop installation containing the Dach & Nona and Super Mart restaurant profiles  
**Created:** 2026-08-11  
**Simplified:** 2026-08-11  
**Remove after:** both rebuilt profiles pass the rollout checks, the evidence is recorded in permanent docs, and the temporary central plan is retired

## 1. Outcome

Dach & Nona and Super Mart must behave as two restaurant profiles backed by one shared menu-group catalog:

- one canonical item list, variant list, redirect set, POS item/variant mapping and current price per Petpooja locator;
- identical Group Catalog, Menu Matrix and Group History in either profile after sync;
- restaurant-local orders, quantities, revenue, availability and eligibility;
- one group-wide history containing eligible legacy events plus future global mutations;
- no separate `GlobalMenuItemVariant` table;
- no restaurant-only canonical menu edits after the group reaches `shadow`.

The central server is the durable authority. Each local SQLite profile is a rebuildable cache and analytics projection.

| Data | Analytics storage | Authority |
|---|---|---|
| Canonical items | `global_menu_items` | central menu group |
| Canonical variants | `global_variants` | central menu group |
| Allowed item/variant pair, Petpooja key and current price | `global_menu_mapping_rules` | central menu group |
| Local item/variant projection | `menu_items`, `variants` and global link tables | rebuildable cache |
| POS assignment/cache row | existing `menu_item_variants` | rebuildable per-profile projection |
| Redirects | `global_menu_redirects` | central menu group |
| Convergence events | `global_menu_events` | central menu group |
| Human audit history | `global_menu_history` | central unified history endpoint |
| Orders and sales | existing order tables | selected restaurant / POS replay |

## 2. Clean-rebuild assumptions and stop gate

This plan deliberately does **not** implement an in-place revision-1.6 data migration. It is valid only while all of the following remain true:

1. There is one controlled desktop installation in use.
2. Its Dach & Nona and Super Mart profile databases may be reset and rebuilt during a coordinated maintenance window.
3. Central already holds, or will hold before reset, the complete canonical catalog, variants, redirects, assignments, prices and unified history required by revision 1.7.
4. The required restaurant order history can be replayed through the supported POS/order-ingest path.
5. `analytics-control.db`, app-global configuration and the restaurant registry are preserved.
6. `data/cluster_state_backup.json`, `data/id_maps_backup.json` and every other recovery/export artifact are preserved.

Before resetting either profile, record and verify:

- central snapshot counts and digest for items, variants, redirects and mapping rules;
- price-bearing rule count and absence of malformed/duplicate POS locators;
- unified history counts for Dach & Nona and Super Mart legacy rows;
- assignment coverage for both restaurants;
- POS replay availability for the required order period;
- current per-profile order, revenue, menu, assignment and eligibility baselines;
- recoverable copies of both profile SQLite files.

If any check fails, stop. Do not reset the local databases; restore the in-place migration phase instead.

## 3. Decisions that must not drift

1. The clean rebuild removes compatibility migration work; it does not remove the new schema required by a fresh database.
2. `global_menu_shared_pos_catalog_v1` is the only switch that makes `pos_item` and `pos_addon` group-scoped and price-bearing.
3. `GlobalMenuMappingRule` is the shared catalog entry. Do not add a pair model, pair id or fifth snapshot section.
4. `price` is a two-decimal decimal string on the wire and a decimal/numeric value in storage. Never pass it through binary floating point.
5. A rule with no global variant projects to the deterministic no-variant sentinel because `menu_item_variants.variant_id` is non-null.
6. Shared projection may replace current item id, variant id and catalog price in `menu_item_variants`; later refreshes must preserve restaurant-local availability and eligibility fields.
7. Historical order-line prices, totals and revenue are facts and must never be rewritten from catalog price.
8. Legacy `merge_history` is not copied into the new cache. Central supplies eligible legacy and global rows through the unified history endpoint; legacy rows are never presented as undoable.
9. Snapshot, event, assignment and history application is idempotent and cursor-safe. A failed page never advances its cursor.
10. Capability absence preserves legacy behavior. Cached price-bearing data never enables shared behavior by itself.
11. The frozen revision-1.7 observation/reconciliation protocol remains in scope. `shared_pos_catalog` is a parity and activation input, not a local-to-central ground-truth migration.

## 4. Rebuild data flow

```text
central registry + revision-1.7 menu-group authority
                         │
                         ├─ status / catalog snapshot / event tail
                         ├─ assignment snapshot
                         └─ unified history
                         ▼
fresh profile schema → global caches → deterministic local projection
                         │
POS order replay ────────┤
                         ▼
restaurant-local analytics, availability and eligibility
```

There is no single whole-database download. The rebuild composes central domain pulls with POS order ingestion.

## 5. Boundary with the central repository

Analytics development can begin from frozen fixtures. The destructive cutover cannot begin until the central implementation and data checks pass.

| Central dependency | Analytics behavior before it exists | Required before reset/activation |
|---|---|---|
| §9 accepts `shared_pos_catalog` and returns `shared_pos_catalog_updated` | mocked fixture tests | live contract test passes |
| snapshot/event mapping rules include `price` | parser/projector fixture tests | exact serializers deployed |
| `GET global-menu/history` | history pull remains gated | endpoint and data-count checks pass |
| price mutation preview/commit | UI remains gated | central mutation tests pass |
| `global_menu_shadow_write_blocked` | desktop blocks early | central remains final guard |
| `global_menu_shared_pos_catalog_v1` | no shared-POS behavior | advertised only after reviewed reconciliation |

Central deploys additive schema and dormant endpoints first. The shared-POS capability is the final activation switch.

## 6. Analytics implementation phases

### Phase A — Own revision 1.7 and the fresh-database schema

Files:

- `contracts/central_server_analytics_app_api_contract.md`
- `contracts/fixtures/1/*.json`
- `database/schema_sqlite.sql`
- `src/core/global_menu_schema.py`
- `tests/test_global_menu_phase1.py`
- `tests/test_unscoped_routes_and_schema_ownership.py`
- `ui_electron/src/globalMenuCapabilities.test.ts`

Tasks:

1. Give every new revision-1.7 fixture an analytics test owner: shared observation, price-bearing rule, status fields, history page, price preview, shadow refusal and allowed-restaurants capability.
2. Add `price DECIMAL(10,2) CHECK (price IS NULL OR price >= 0)` to the canonical `global_menu_mapping_rules` definition.
3. Add `history_cursor TEXT` to the canonical `global_menu_state` definition.
4. Add the canonical `global_menu_history` table and ordering index required by §25.10.
5. Add the shared-POS capability constant and advertised/ready properties to `GlobalMenuCapabilityStatus`.
6. Add the new table/columns to `GLOBAL_MENU_TABLES` validation.
7. Test a fresh database and repeated schema application.
8. Do **not** add revision-1.6 `ALTER TABLE` upgrades, cached-row preservation migrations or old-profile migration tests.

Exit criterion: a newly created profile has the complete revision-1.7 schema, fixture ownership is explicit, and shared behavior remains off without the capability.

### Phase B — Export the contract-required observation

Files:

- `src/core/menu_catalog_seed.py`
- `src/core/menu_bootstrap_shipper.py`
- `tests/test_menu_assignment_bootstrap.py`

Tasks:

1. Add a pure `build_shared_pos_catalog(conn)` helper over active `menu_item_variants`, `menu_items`, `variants` and raw POS evidence.
2. Emit exact §9 fields in deterministic `(locator_type, locator_value)` order.
3. Reject locator-kind collisions, negative/non-finite decimals and malformed values.
4. Serialize variant value and price as exact two-decimal strings.
5. Hash `id_maps + shared_pos_catalog`, so a price-only change uploads.
6. Persist the hash only after success and `shared_pos_catalog_updated: true`.
7. Keep `cluster_state` as `seed_only`; the observation is not canonical authority.

Tests cover item/addon/no-variant rows, deterministic hash, price-only change, unchanged skip, refusal retry, invalid decimals and inactive mappings.

### Phase C — Pull and materialize the shared catalog

Files:

- `src/core/global_menu_sync.py`
- `src/core/global_menu_identity.py`
- `src/core/global_menu_schema.py`
- `src/core/menu_assignment_apply.py` only if a reusable guarded upsert is needed
- `tests/test_global_menu_phase1.py`

Tasks:

1. Require valid two-decimal price only for group-scoped POS rules under `global_menu_shared_pos_catalog_v1`; require `null` in other modes.
2. Reject/quarantine wrong scope, missing capability, invalid price, locator-kind collisions, missing/tombstoned targets and unresolved redirects.
3. Apply the four snapshot sections against one pinned watermark.
4. After the complete snapshot, materialize one transaction:
   - resolve redirect chains;
   - create deterministic local item/variant owners;
   - use the no-variant sentinel when required;
   - upsert `menu_item_variants.order_item_id = locator_value`;
   - set current item, variant, price, active and verified state;
   - preserve restaurant-local eligibility/availability on later refreshes.
5. Roll back the materialization batch and quarantine the payload on failure; keep the previous complete projection usable.
6. Route price-bearing event deltas and tombstones through the same projector.
7. Never update historical order rows from catalog changes.

Tests use two blank profile databases and prove identical catalog/matrix projection, inherited peer rows, no-variant handling, idempotence, price convergence, eligibility preservation on refresh and unchanged historical revenue.

### Phase D — Pull and expose unified history

Files:

- `src/core/global_menu_history.py`
- `src/core/services/cloud_pull_orchestrator.py`
- `src/api/routers/menu.py`
- `tests/test_global_menu_phase1.py`

Tasks:

1. Pull `/desktop-analytics-sync/global-menu/history` with the contract's opaque paging semantics.
2. Strictly validate and upsert rows by `history_id`.
3. Advance `history_cursor` only after a complete valid page commits.
4. Treat history failure as a visible warning, not a catalog-authority failure.
5. Read global-mode `/menu/merge/history` from `global_menu_history` while preserving the frontend envelope.
6. Expose undo only when both `is_undoable` and `mutation_id` are present and the current capability permits it.
7. Never copy local `merge_history` into the new table.

Tests prove both blank profiles hydrate the same ordered history, retries are idempotent, malformed pages retain the old cursor/data, and legacy rows have no false undo action.

### Phase E — Add shared mutations and group-owned UI

Files:

- `src/core/global_menu_mutation.py`
- `src/api/routers/menu.py`
- `src/core/queries/menu_queries.py`
- `ui_electron/src/api.ts`
- `ui_electron/src/pages/Menu.tsx`
- focused backend and React tests

Backend tasks:

1. Normalize `global_locator.price_update` as `{locator_type, locator_value, price}`.
2. Under the shared capability, send group scope, `confirm_group_wide: true` and price for POS mapping changes.
3. Reuse preview → confirmation → commit → timeout reconciliation.
4. Apply accepted responses/events through the common projector.
5. From `shadow`, block every local canonical merge, undo, rename, retype, variant merge and price write before local mutation construction.
6. Keep assignment verify/reopen and other contract-approved restaurant facts available.
7. Audit bulk, indirect and utility entry points for guard bypasses.

UI tasks:

1. Label the shared views **Group Catalog**, **Menu Matrix** and **Group History** when ready.
2. Render active global catalog rows and active group POS rules with current price.
3. Keep Store Resolution and analytics restaurant-specific.
4. Show history origin/source and truthful undo availability.
5. Hide or disable canonical local controls in shadow; route active edits through global preview.
6. Keep All Stores read-only and behind the existing aggregation-ready gate.

Acceptance: switching profiles changes store analytics but not the group catalog, matrix or history; a price mutation converges to both profiles and changes no historical order value.

### Phase F — Clean rebuild, sync orchestration and diagnostics

Files:

- `src/core/services/cloud_pull_orchestrator.py`
- `src/core/db/reset.py`
- the sync operation coordinator
- diagnostics queries and focused profile/sync tests

The operator reset path must archive and recreate the captured profile without first opening or validating the old revision-1.6 schema. The ordinary updated runtime must not attempt to use that old profile before the clean rebuild.

Required rebuild order for each physical profile:

1. Pass the central and recoverability stop gate in §2.
2. Archive the current profile SQLite file; preserve `analytics-control.db` and recovery exports.
3. Reset only the selected physical profile through the existing profile-scoped reset path.
4. Create and bind the fresh canonical schema.
5. Refresh registry/capabilities and pull global status.
6. Pull the complete global catalog snapshot and materialize rules.
7. Drain the global event tail from the pinned snapshot watermark.
8. Replay/import that restaurant's POS orders.
9. Pull/apply the restaurant assignment snapshot after order rows exist.
10. Pull/cache group history; report failure as a warning.
11. Send the current §9 observation after local order/catalog state settles.
12. Run the same sequence for the other restaurant profile.

Diagnostics must expose bootstrap state, catalog revision, mapping count, price count, assignment coverage, history count/cursor and quarantine count.

Exit criterion: both rebuilt profiles have identical group catalog/matrix/history digests, separate restaurant analytics, complete required assignments and no unexplained quarantine.

## 7. Test commands

Run focused suites during implementation:

```bash
python3 -m unittest tests.test_global_menu_phase1
python3 -m unittest tests.test_menu_assignment_bootstrap
python3 -m unittest tests.test_menu_mutation_commit
python3 -m unittest tests.test_sync_operations
python3 -m unittest tests.test_unscoped_routes_and_schema_ownership
```

Run frontend capability, menu-table, mutation and history tests through the existing package scripts. Before handoff, run the broader backend/unit suite used by CI and `git diff --check`.

Contract parity check:

```bash
cmp contracts/central_server_analytics_app_api_contract.md ../db.dachnona/contracts/desktop_analytics_app/central_server_analytics_app_api_contract.md
diff -qr contracts/fixtures/1 ../db.dachnona/contracts/desktop_analytics_app/fixtures/1
```

## 8. Coordinated cutover

Do not reset or activate this from analytics alone.

1. Central deploys revision-1.7 schema, reads, mutations and contract tests without advertising the shared capability.
2. Analytics passes fixture-driven tests and is ready to create a fresh revision-1.7 profile schema.
3. Upload the contract-required observations from both current profiles and verify central digests.
4. Central completes and records the reviewed shared-POS reconciliation.
5. Verify central snapshot, price, history and assignment counts against the §2 baselines.
6. Central advertises `global_menu_shared_pos_catalog_v1` in `shadow`.
7. Archive and reset Dach & Nona; rebuild it using Phase F and verify its baselines.
8. Archive and reset Super Mart; rebuild it using Phase F and verify its baselines.
9. Compare the two group catalog/matrix/history digests and restaurant-local analytics.
10. Enable aggregation and mutations one rung at a time only after both checks pass.
11. Retain the archived profile databases until the rollout has remained stable through the agreed observation window.

## 9. Rollback

If projection or parity is wrong:

1. central withdraws `global_menu_shared_pos_catalog_v1` and higher capabilities;
2. stop sync and do not overwrite archived profile databases;
3. if local continuity is required, restore the archived pre-reset profile databases together with the previous analytics build that understands their schema;
4. retain additive central records and new empty/rebuilt profiles for diagnosis;
5. do not delete recovery artifacts or rewrite order facts;
6. fix centrally, publish a corrected snapshot/event revision and repeat the stop gate before another rebuild.

## 10. Documentation cleanup

After both rebuilt profiles pass:

- update `docs/MENU_SYNC_ARCHITECTURE.md` with the final ownership, pull order and runbook;
- update `docs/SYSTEM_CONTEXT.md` with the shared-POS price exception and clean-rebuild cutover decision;
- update `docs/FILE_INVENTORY.md` for the history module;
- update the `docs/INDEX.md` status snapshot;
- update `contracts/README.md` only if the twin-sync checklist changed.

Then remove this temporary plan and the central temporary plan in the same coordinated cleanup change.

## 11. Definition of done

- Revision-1.7 contract copies and fixtures are byte-identical across repositories.
- Both profile rebuilds start from the fresh canonical schema; no revision-1.6 local data migration is required.
- Both profiles hydrate the same active global catalog, POS mapping/price set and unified history from central.
- Required POS orders and restaurant assignments are restored with pre-reset totals accounted for.
- Super Mart assignments have complete global identity where the reviewed shared locator provides authority.
- Legacy history is visible without fabricated undoability.
- Shadow mode cannot create restaurant-only canonical divergence.
- A price mutation changes current catalog price in both profiles and no historical order value.
- Store analytics remain separated and All Stores remains read-only/appropriately gated.
- Archived pre-reset databases remain available through the rollback window.
- Permanent docs are updated and both temporary plans are retired.
