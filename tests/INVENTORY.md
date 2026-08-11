# Test Inventory

Quick map of unit tests under `tests/`. Run the suite with:

```bash
.venv/bin/python -m pytest tests/ -v
```

**Last full audit:** 2026-07-06. **Last updated:** 2026-07-10 (itemcode projection suites added) — 35 files, 304 tests, all passing. Per-file sections below cover the audited core; files added after 2026-07-06 (customer strict-mode, forecast sync, edit APIs, scope-state pulls) are counted in the totals but only itemcode files have sections so far.

---

## Summary by area

| Area | Files | Tests | Primary modules under test |
|------|-------|-------|---------------------------|
| Menu merge & assignments | 6 | 44 | `utils/menu_utils.py`, `src/core/menu_*_sync*.py`, `src/core/menu_assignment_*` |
| Customer merge & analytics | 7 | 38 | `src/core/customer_*`, `src/core/queries/customer_*` |
| Ingestion & clustering | 3 | 7 | `services/`, `utils/clean_order_item.py`, `scripts/seed_from_backups.py` |
| Itemcode projection | 3 | 44 | `src/core/itemcode_mapping.py`, `services/clustering_service.py`, lifecycle rebuild hooks |
| Sync orchestration | 3 | 8 | `src/core/services/`, `src/core/client_learning_shipper.py` |
| Menu queries & enforcement | 2 | 3 | `src/core/queries/menu_queries.py`, `utils/menu_item_variant_enforcement.py` |

---

## File index

### Menu merge, assignments & bootstrap

#### `test_menu_utils.py` (6 tests)
- **Module:** `utils/menu_utils.py`
- **Class:** `MenuUtilsTests`
- **Covers:** Local merge/undo, variant resolution, preview-merge (same-item variant), suggestion retargeting, forecast-cache clearing.
- **Overlap:** Complements `test_menu_merge_sync.py` (sync events) and `test_menu_assignment_apply.py` (multi-install LWW). Does **not** duplicate pull/push or conflict-matrix tests.

#### `test_menu_merge_sync.py` (10 tests)
- **Modules:** `src/core/menu_merge_sync.py`, `src/core/menu_sync_quarantine.py`, `src/core/sync_cursor_migration.py`
- **Class:** `MenuMergeSyncTests`
- **Covers:** Remote pull apply/undo, remap on merge stream, quarantine drain, cursor reset, sync-conflicts UI data. (Legacy outbox/shipper tests removed with the strict-only cutover.)
- **Overlap:** `test_retry_drains_quarantine_on_next_pull` mirrors the verification-stream quarantine test but targets the **menu merge** stream only.

#### `test_menu_assignment_apply.py` (9 tests)
- **Modules:** `src/core/menu_assignment_apply.py`, `src/core/menu_mapping_verification_sync_events.py`
- **Class:** `MenuAssignmentApplyTests`
- **Helpers exported:** `make_install_db`, `FakeEventServer`, `pull_install` (reused by `test_menu_assignment_bootstrap.py`)
- **Covers:** Contract fixture parity (`contracts/fixtures/1/menu_merge_event_fixtures.json`, `…/menu_mapping_verification_event_fixtures.json`), LWW conflict matrix, echo/ack, stale events, v1 legacy wire derivation, `is_verified` convergence.
- **Overlap:** Uses `menu_utils` for local ops; tests assignment-layer convergence, not HTTP pull (see `test_menu_merge_sync.py`).

#### `test_menu_assignment_bootstrap.py` (5 tests)
- **Modules:** `src/core/menu_assignment_bootstrap.py`, `src/core/menu_bootstrap_sync.py`
- **Classes:** `MenuAssignmentBootstrapTests`, `MenuBootstrapShipperTests`
- **Covers:** Fresh-install snapshot digest vs replay, cursor guard, fetch-error handling, seed-only default, shipper hash-skip.
- **Overlap:** `test_menu_bootstrap_pull.py` tests `apply_menu_bootstrap_snapshot` in isolation; this file tests the full bootstrap orchestration path.

#### `test_menu_bootstrap_pull.py` (3 tests)
- **Module:** `src/core/menu_bootstrap_sync.py` (`apply_menu_bootstrap_snapshot`)
- **Class:** `MenuBootstrapPullTests`
- **Covers:** Seed + relink order items, seed-only mode (no assignment overwrite), legacy snapshot variant-metadata inference.

#### `test_menu_mapping_verification_sync.py` (7 tests)
- **Modules:** `src/core/menu_mapping_verification_sync.py`, `src/core/menu_mapping_verification_sync_events.py`
- **Class:** `MenuMappingVerificationSyncTests`
- **Covers:** Record/pull verified-by-order-item-id, deferred flush, idempotent pull, quarantine poison events, bulk partial apply, verification stream sequence guard. (Legacy shipper-upload tests removed with the strict-only cutover.)

---

### Customer domain

#### `test_customer_merge_rules.py` (10 tests)
- **Modules:** `src/core/queries/customer_merge_queries.py`, `src/core/queries/customer_similarity_queries.py`, `src/core/queries/customer_similarity_helpers.py`
- **Class:** `CustomerMergeRuleTests`
- **Covers:** Merge preview/execute policy (verified/unverified direction), undo state restore, similarity candidate filtering, order snapshots in preview, customer summary normalization.
- **Overlap:** `test_customer_similarity_scoring.py` tests pure scoring functions; this file tests query-layer integration.

#### `test_customer_similarity_scoring.py` (4 tests)
- **Module:** `src/core/queries/customer_similarity_scoring.py`
- **Class:** `CustomerSimilarityScoringTests`
- **Covers:** Name similarity edge cases, quantity-weighted basket overlap.

#### `test_customer_merge_sync.py` (4 tests)
- **Modules:** `src/core/customer_merge_sync_events.py`, `src/core/queries/customer_merge_queries.py`
- **Class:** `CustomerMergeSyncTests`
- **Covers:** `build_*` event payload builders on merge/undo — metadata/locators/attribution, undone-event linkage via legacy outbox row and via `suggestion_context.remote_event_id`. (Legacy shipper-upload and outbox-writer tests removed with the strict-only cutover.)

#### `test_customer_merge_pull.py` (4 tests)
- **Modules:** `src/core/customer_merge_sync.py`, `src/core/customer_merge_sync_events.py`
- **Class:** `CustomerMergePullTests`
- **Covers:** Remote merge/undo apply, duplicate skip, self-origin skip before ambiguous resolution.

#### `test_customer_metric_queries.py` (17 tests)
- **Modules:** `src/core/queries/customer_analytics_queries.py`, `src/core/queries/customer_metric_helpers.py`, `src/core/queries/customer_reorder_rate_queries.py`, `src/core/queries/insights_queries.py`
- **Class:** `CustomerMetricQueryTests`
- **Covers:** Affinity, loyalty, reorder/return/retention/repeat-order rates (analysis + trends), quick view, shared helper edge cases.

#### `test_customer_ingestion_rules.py` (1 test)
- **Module:** `services/load_orders.py` (`get_or_create_customer`)
- **Class:** `CustomerIngestionRuleTests`
- **Covers:** Verified anonymous customer not demoted on POS update.

---

### Itemcode projection

#### `test_itemcode_mapping.py` (24 tests)
- **Module:** `src/core/itemcode_mapping.py`
- **Classes:** `TestNormalizeItemcode`, `TestObserve`, `TestRebuild`, `TestSchemaAndCascade`
- **Covers:** Normalization (null/blank/numeric/whitespace, case preserved), observation semantics (evidence increment, per-restaurant independence, many codes → one parent, deterministic sorted conflict JSON, candidate union), full rebuild (idempotent, stale removal, trusts assignments over order rows, distinct-itemid evidence, scoped rebuild, merge-shaped conflict resolution), schema idempotence + FK cascades. Helpers never commit.

#### `test_itemcode_clustering.py` (11 tests)
- **Modules:** `services/clustering_service.py` (`OrderItemCluster.add` itemcode routing), `src/core/itemcode_mapping.py`
- **Classes:** `TestExistingItemidPriority`, `TestItemcodeHit`, `TestFallbackPaths`, `TestTransactionality`
- **Covers:** itemid-first priority (disagreement marks conflict, never remaps), `itemcode-hit` routing (shared parent + distinct variants, two codes → one parent, C1 verification inheritance), fallback parity for conflicted/blank codes, fuzzy stays suggestion while projection learns, addons produce no itemcode rows, rollback removes all partial itemcode effects.

#### `test_itemcode_lifecycle.py` (9 tests)
- **Modules:** `utils/menu_utils.py`, `src/core/menu_merge_sync.py`, `src/core/menu_assignment_bootstrap.py`, `src/core/services/sync_service.py` (rebuild hooks)
- **Class:** `ItemcodeLifecycleTests`
- **Covers:** Projection rebuild convergence across local merge, variant merge, resolution, undo, assignment-batch epilogue; merge resolves split conflicts; undo restores aliases from assignments; strict-mode capture/rollback leaks nothing; best-effort rebuild survives partial schema.

---

### Ingestion, clustering & durable seed

#### `test_clean_order_item.py` (4 tests)
- **Modules:** `utils/clean_order_item.py`, `services/clustering_service.py` (`OrderItemCluster`)
- **Class:** `CleanOrderItemTests`
- **Covers:** Waffle-cone name normalization and cluster reuse (domain-specific regression fixtures).

#### `test_seed_from_backups.py` (5 tests)
- **Module:** `scripts/seed_from_backups.py`
- **Class:** `SeedFromBackupsExportTests` *(name predates `perform_seeding` test; covers export + seed)*
- **Covers:** `export_to_backups` variant metadata persistence; explicit restore directory handling; missing backup behavior; full restore itemcode rebuild; no-arg CLI refuses implicit restore.

#### `test_menu_item_variant_enforcement.py` (2 tests)
- **Module:** `utils/menu_item_variant_enforcement.py`
- **Class:** `MenuItemVariantEnforcementTests`
- **Covers:** `1_PIECE` stub insertion, backfill counts only missing mappings.

---

### Sync orchestration & API

#### `test_sync_operations.py` (2 tests)
- **Modules:** `src/api/routers/operations.py`, `src/core/services/sync_service.py` (`SyncStatus`)
- **Class:** `SyncOperationsTests`
- **Covers:** `iter_sync_statuses` waits for best-effort cloud pull before `done`; skips cloud pull after sync error.

#### `test_cloud_pull_lock_and_nudge.py` (9 tests)
- **Modules:** `src/core/services/cloud_pull_orchestrator.py`
- **Class:** `CloudPullLockTests`
- **Covers:** Non-blocking pull lock, lock release, reentrancy (already_locked) for menu/customer state pulls, orchestrator lock passing.

#### `test_client_learning_shipper.py` (1 test)
- **Module:** `src/core/client_learning_shipper.py`
- **Class:** `ClientLearningShipperTests`
- **Covers:** `run_all` preserves named rows for downstream shippers in upload batch.

---

### Queries

#### `test_menu_queries.py` (1 test)
- **Module:** `src/core/queries/menu_queries.py`
- **Class:** `MenuQueriesTests`
- **Covers:** `fetch_menu_matrix` groups duplicate mapping rows by variant.

---

## Cross-cutting overlap notes

| Pattern | Files | Note |
|---------|-------|------|
| `test_retry_drains_quarantine_on_next_pull` | `test_menu_merge_sync`, `test_menu_mapping_verification_sync` | Same quarantine helper; **different sync streams** — keep both. |
| Local merge behavior | `test_menu_utils`, `test_menu_merge_sync` | Utils = business logic + caches; merge_sync = outbox/event recording. |
| Similarity | `test_customer_similarity_scoring`, `test_customer_merge_rules` | Pure functions vs query/policy integration. |
| Bootstrap snapshot | `test_menu_bootstrap_pull`, `test_menu_assignment_bootstrap` | Unit apply vs full fresh-install orchestration. |

---

## Coverage gaps (no dedicated tests)

| Module / behavior | Notes |
|-------------------|-------|
| `src/api/routers/config.py` (`reset_db_section`) | Recent change: clears `menu_assignments_bootstrapped`, catalog-only reseed. |
| `src/core/db/reset.py` | Catalog-only `perform_seeding(seed_mappings=False)`. |
| `src/core/services/sync_service.py` (`sync_database`) | Only `iter_sync_statuses` wrapper tested; not full order-ingest path. |
| Deleted scripts (`export_cluster_review.py`, `export_combo_cluster_review.py`, `resolve_unclustered.py`) | Never had tests under `tests/`; no cleanup needed. |
| `ui_electron/` | No frontend unit tests in this repo. |
| Forecasting / AI mode | No tests in `tests/` (see `docs/CENTRAL_FORECASTING_NIGHTLY_PLAN.md`, `src/ai_mode/`). |

---

## Running subsets

```bash
# Menu sync cluster
.venv/bin/python -m pytest tests/test_menu_*.py -v

# Customer cluster
.venv/bin/python -m pytest tests/test_customer_*.py -v

# Single file
.venv/bin/python -m pytest tests/test_seed_from_backups.py -v
```
