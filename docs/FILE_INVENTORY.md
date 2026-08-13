# File Inventory

> **For AI agents:** A grouped, annotated map of the files that matter, so you can jump to the right one without grepping the whole tree. Grouped by **concern**, not by folder — the same concern often spans `services/`, `src/core/`, and `utils/`. Start at [INDEX.md](./INDEX.md) for task routing; use this to locate the file once you know the task.
>
> **Last updated:** 2026-08-14 (revision 1.10 parent-only itemcodes and retired display-name aliases). When you add/rename a load-bearing module, add it here.

Legend: 🧠 durable artifact (never delete casually) · ⚙️ core logic · 🌐 cloud sync · 🖥️ API · 🎨 frontend · 🧪 test/fixture.

---

## Entry points & config

| Path | Role |
|------|------|
| `AGENTS.md` / `CLAUDE.md` | Root AI-agent instructions (CLAUDE.md points to AGENTS.md). |
| `README.md` | Human-facing overview + quick start. |
| `Makefile` | `start`, `backend`, `frontend`, `verify`, `clean`, `sync`, `reload-all`. |
| `docs/INDEX.md` | Doc routing hub + token budget. Read first. |
| `src/api/main.py` | FastAPI app entry; wires routers. |
| `installer/backend_entry.py` | Packaged backend entry point (PyInstaller `sys._MEIPASS` vs source-path resolution). |
| `src/core/config/constants.py` | Global constants. |
| `src/core/config/cloud_sync_config.py` | Cloud sync endpoints/keys, strict-mode flags. |
| `src/core/config/client_learning_config.py` | AI/learning sync config. |
| `src/core/db/control.py` | Canonical `analytics-control.db` schema, global config, and one-time legacy config copy. |
| `src/core/profiles.py` | Server-managed profile registry, hashed paths, existing-DB binding, identity/mismatch guard, and persisted selection (restaurant or All Stores). |
| `src/core/analytics_scope.py` | `AnalyticsScope` value object: one restaurant, or a frozen All Stores snapshot. |
| `src/core/queries/multi_store.py` | Read-only All Stores fan-out, completeness envelope, per-profile timezone binding. |
| `src/core/queries/multi_store_reducers.py` | Declarative combination rules (`Sum`, `Ratio`, `Min`/`Max`, `First`, `AllTrue`/`AnyTrue`) and union/group helpers. |
| `src/core/queries/multi_store_forecast.py` | Forecast combination, durable item grouping, incomplete-store reporting. |
| `src/core/queries/customer_metric_sources.py` | Profile-qualified customer order atoms for combined customer metrics. |
| `src/core/services/all_stores_sync.py` | Sequential All Stores Sync DB coordinator (per-store outcomes, partial/failed semantics). |
| `src/core/central_api.py` | Revision-1.2 scoped/unscoped headers and typed central error decoding. |
| `src/core/menu_catalog_seed.py` / `menu_bootstrap_shipper.py` | 🌐 Catalog seed and seed-only bootstrap ingest (`id_maps` + `cluster_state`). |

## Order ingest (POS → local rows)

See [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md) for the full flow.

| Path | Role |
|------|------|
| ⚙️ `services/load_orders.py` | **The ingest core.** `process_order`: atomic header+children commit, replay wipe/rebuild, customer resolution, stat dispatch. |
| ⚙️ `services/clustering_service.py` | `OrderItemCluster.add` — raw item/addon name → `ClusterMatch(menu_item_id, order_item_id, variant_id, item_type, match_method, match_confidence)`. |
| ⚙️ `src/core/services/sync_service.py` | `sync_database` — drives per-order ingest, addon-eligibility pass, cloud pulls. |
| ⚙️ `src/core/services/cloud_pull_orchestrator.py` | Pulls order/menu/customer streams from cloud in the right order. |
| ⚙️ `src/core/services/cloud_sync_scheduler.py` | Schedules background sync. |
| `services/sync_conversations.py` | Sync AI conversations. |

## Clustering / mapping / parsing

See [item_clustering.md](./item_clustering.md).

| Path | Role |
|------|------|
| ⚙️ `utils/clean_order_item.py` | Legacy regex parser: raw name → `(clean_name, variant)`. Fallback for unseen names; UNKNOWN-variant fallback for unrecognized sizes. |
| ⚙️ `src/core/itemcode_mapping.py` | Itemcode → parent projection (`itemcode_mappings`): observe/lookup in ingest, full rebuild at lifecycle points. Derived, local-only, never exported. |
| ⚙️ `utils/mapping_core.py` | Reduces a raw name to a normalized `"<type>|<flavor>"` core (strips cosmetic tokens). Basis of the silent-reuse guard. |
| ⚙️ `src/core/mapping_anomalies.py` | Silent-reuse detection: remembers cores per id (`mapping_id_cores`), flags identity changes into `mapping_anomalies`. Local diagnostics only. |
| ⚙️ `utils/id_generator.py` | Deterministic `uuid5` menu-item IDs from name. |
| ⚙️ `utils/menu_utils.py` | `_recalculate_menu_item_stats` + menu helpers. |
| ⚙️ `utils/menu_item_variant_enforcement.py` | `mark_addon_eligible_from_usage`, variant-mapping backfills. |
| ⚙️ `utils/variant_metadata.py` | Variant weight/volume metadata (for volume forecasting). |
| ⚙️ `src/core/queries/menu_queries.py` | Menu read queries (Resolutions tab, unverified items, addon stats). |

## Menu sync — cross-install LWW + strict-mode commits 🌐

See [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) and [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md).

| Path | Role |
|------|------|
| `src/core/menu_merge_sync.py` / `_events.py` | Menu merge/undo/remap replay (row-shaped, LWW). |
| `src/core/menu_mutation_commit.py` | **Strict-mode** synchronous mutation commit (server is commit authority). |
| `src/core/menu_assignment_apply.py` / `_bootstrap.py` / `_schema.py` | Per-order-item assignment apply + fresh-install snapshot. |
| `src/core/menu_bootstrap_sync.py` / `_shipper.py` | Catalog snapshot pull + optional seed-mirror push. |
| `src/core/menu_mapping_verification_sync.py` / `_events.py` | Verification-flag pull/apply + `flush_deferred_menu_mapping_verifications`. |
| `src/core/menu_sync_quarantine.py` | Holds un-appliable replay events; surfaced via API. |
| `src/core/derived_assignment_flush.py` | Create-only flush of machine-derived POS-backed assignments (`derived_assignment.sync`). |
| `src/core/order_item_key.py` | Assignment key ↔ local POS row backing (`AssignmentKeyIndex`). |
| `src/core/menu_catalog_seed.py` | In-memory catalog seed + bootstrap-payload builders (`seed_catalog`). |

### Global-menu projection (revision 1.10; active enrollment)

The modules below execute when the selected, authorized physical profile's
server-managed registry row advertises a `menu_group_id` and `global_menu_v1`.
A configured member of a group that owns a canonical catalog advertises all
four capabilities together. Unlinked member assignments are a normal enrollment
state and appear in Unclustered Data Resolution.

| Path | Role |
|------|------|
| `src/core/global_menu_schema.py` | Additive projection validation, structured status, quarantine access, and the single fail-closed capability resolver. |
| `src/core/global_menu_sync.py` | Snapshot/event/assignment adapters; validates group/revision/redirect integrity. POS mapping rules are restaurant-scoped and carry no price. |
| `src/core/global_menu_identity.py` | Stable local/global links, redirect traversal, approved locator precedence, canonical projection planner, and All Stores row annotation. |
| `src/core/global_menu_history.py` | Strict unified-history paging/cache with cursor-safe page commits and legacy undo refusal. |
| `src/core/global_menu_mutation.py` | Stable-ID preview/commit/status transport and trusted local locator context; reconciles uncertain POST outcomes without blind replay. Coverage does not gate commit. |
| `src/core/queries/global_menu_diagnostics.py` | Clean-rebuild counts, coverage/cursor/quarantine state and deterministic catalog/matrix/history digests. |
| `src/core/db/reset.py` | Opaque archive-and-recreate path for one captured profile; never opens a revision-1.6 file before replacement. |
| `ui_electron/src/globalMenuCapabilities.ts` | Pure frontend predicates for catalog/history labels and mutation controls. Coverage is not a gate. |
| `database/schema_sqlite.sql` | Owns the additive `global_menu_*` cache, link, rule, event, and quarantine tables. |
| `tests/test_global_menu_phase1.py` / `tests/fixtures/global_menu_v1_snapshot.json` | Frozen-contract fixtures plus projection, capability, narrow-resolution, locator-scope and federation regression coverage. |

## Customer sync 🌐

| Path | Role |
|------|------|
| `src/core/customer_merge_sync.py` / `_events.py` | Customer merge/undo replay + quarantine. |
| `src/core/customer_mutation_commit.py` | Strict-mode customer mutation commit. |
| `src/core/queries/customer_merge_helpers.py` | `recompute_customer_aggregates` + merge helpers. |
| `src/core/queries/customer_merge_*.py` | Merge history / queries. |

## Sync plumbing (shared) 🌐

| Path | Role |
|------|------|
| `src/core/sync_cursor.py` / `_migration.py` | Per-stream cursor persistence. |
| `src/core/sync_identity.py` | Install/employee identity for sync. |
| `src/core/error_shipper.py` / `error_log.py` | Crash/error log sync. |
| `utils/api_client.py` | HTTP client to the central webhook server (raw data fetch). |
| `src/core/learning_shipper.py` / `client_learning_shipper.py` | AI usage/feedback + per-step telemetry (`ai_call_trace`) sync to central §18. Logs and traces are marked uploaded together; `feedback_id` compared as string. |

## Shared helpers (`src/core/utils/`) ⚙️

Small cross-cutting utilities imported widely; grep here before re-implementing date/format/path logic.

| Path | Role |
|------|------|
| `src/core/utils/path_helper.py` | `get_base_path` / `get_resource_path` / `get_data_path` — frozen-vs-source path resolution. Source of writable `data/` dir. |
| `src/core/utils/business_date.py` | Business-day boundaries (day rollover, date bucketing), parameterized by the active profile's timezone and one captured request instant. |
| `src/core/utils/weather_helpers.py` | Weather lookups/derivations for regressors. |
| `src/core/utils/reorder_utils.py` | Customer/item reorder metric calculations. |
| `src/core/utils/customer_estimate.py` | Heuristic party-size range from order volume. |
| `src/core/utils/formatting.py` | Display formatting (`format_indian_currency`, …). |

## Forecasting ⚙️

**Status (2026-07-09):** Central **revenue forecasting deployed** (prod run `a45fc12b-ec65-4698-93c5-15682d17273e`, 2026-07-08). Item/volume central publish pending locator import. **Desktop Phase 5 complete** — pull-only from central cache. Spec: [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md).

| Path | Role |
|------|------|
| `src/core/forecast_sync.py` | Delta/bootstrap pull into `central_forecast_*` tables. |
| `src/core/central_forecast_cache.py` | Central forecast SQLite DDL helpers + run/row queries. |
| `src/core/central_forecast_projection.py` | Row cache → chart JSON for forecast routers. |
| `src/core/forecast_actuals.py` | Local order-history builders for chart actuals lines. |
| `src/core/forecast_cache.py` | Legacy table helpers (config reset / menu merge cache clears). |
| `scripts/pull_forecasts.py` | Manual central forecast pull (dev/ops). |
| `scripts/check_forecast_status.py` | Local central-cache status check. |

## AI Mode (chat → intent → SQL/chart → explain) ⚙️

See [AI_MODE_PLAN.md](./AI_MODE_PLAN.md). Package lives at top-level `ai_mode/` (not `src/ai_mode/`). Provider is OpenAI (`ai_mode/llm/client.py`), not Anthropic.

| Path | Role |
|------|------|
| ⚙️ `ai_mode/orchestrator.py` | **Pipeline core.** correct → classify → plan → execute action sequence. |
| ⚙️ `ai_mode/planner.py` | Classifier output → ordered action list. |
| ⚙️ `ai_mode/actions.py` | Action vocabulary + intent→actions mapping. |
| ⚙️ `ai_mode/handlers.py` | Per-action handlers `(prompt, context, conn) → (part, context)`. |
| `ai_mode/context.py` | Run context passed between multi-step actions. |
| `ai_mode/telemetry.py` | Request-scoped (`ContextVar`) cost/latency/cache-effectiveness trace (§4); aggregated into `ai_logs` cost columns + persisted per-step to `ai_call_trace`. |
| `ai_mode/debug_log.py` | Request-scoped (`ContextVar`-only) debug log; persisted to `ai_debug_log` by `query_id` (race-free, replaces the old cross-request global). Drives DebugOverlay. |
| `ai_mode/logging.py` | Persist interaction metadata to `ai_logs` (no large payloads); `persist_call_trace` / `persist_debug_log`. |
| `ai_mode/llm/completion.py` | **Single choke point** for non-streaming OpenAI completions; times each call and records model + token usage into telemetry. |
| `ai_mode/llm/client.py` | OpenAI client + model config from DB. |
| `ai_mode/llm/intent.py` | Intent classification. |
| `ai_mode/llm/spelling.py` | Spelling/grammar correction of user question (Phase 1). |
| `ai_mode/llm/followup.py` | Follow-up detection + context rewrite (Phase 7). |
| `ai_mode/llm/sql_gen.py` | NL → SQL generation + read-only execution (`ensure_read_only_sql` + `read_sql_readonly`); `build_console_sql_prompt` for the SQL Console tab. |
| `ai_mode/llm/chart.py` | NL → chart config + data (read-only exec; cache key includes `business_today`). |
| `ai_mode/llm/explanation.py` | NL explanation of query results. |
| `ai_mode/llm/schema.py` | **Curated** allowlist schema context (live-DB introspection; ~7–8k fewer tokens/call) injected into SQL/chart prompts. |
| `ai_mode/llm/schemas.py` | Pydantic models for structured LLM JSON outputs. |
| `ai_mode/cache/llm_cache.py` | SQLite exact-key LLM cache, `get_or_call`; `is_incorrect` misses, no error caching, `llm_cache_counters`. **Clear after prompt changes.** |
| `ai_mode/cache/cache_config.py` | LLM cache config. |
| `ai_mode/prompts/prompt_ai_mode.py` | **Single source of truth** for AI/console prompts (TS `prompts.ts` removed; SQL Console fetches via `/api/ai/prompt-context`). |
| 🎨 `ui_electron/src/pages/AIMode/` | Chat UI: `index.tsx`, `hooks/useChat.ts`, `hooks/useDebug.ts`, `components/DebugOverlay.tsx`. |

## API layer 🖥️

| Path | Role |
|------|------|
| `src/api/main.py` | App + router wiring. |
| `src/api/dependencies.py` | Scope resolution and `ScopedReader` (`read` / `read_together`); refuses All Stores on any route without a reducer. |
| `src/api/routers/orders.py` | Orders + addons view. |
| `src/api/routers/menu.py` | Menu, merges, Resolutions (incl. suspect-mappings endpoints). |
| `src/api/routers/customer_analytics.py` | Customer KPIs. |
| `src/api/routers/forecast*.py` | Revenue / item / volume forecasts (central cache projection, read-only). |
| `src/api/routers/insights.py`, `today.py`, `weather.py`, `operations.py`, `system.py`, `config.py`, `sql.py` | Respective dashboards/consoles. |
| `src/api/routers/ai.py`, `conversations.py` | AI chat mode + debug/telemetry endpoints (`/prompt-context`, `/debug/trace`, `/debug/cache-counters`, `/debug/cache-entries`). See [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) and the **AI Mode** section above. |
| `src/api/job_manager.py` | Long-running job tracking. |
| `src/core/queries/*.py` | SQL query modules backing the routers (customer metrics, menu, insights, tables). |

## Local data & explicit exports

| Path | Role |
|------|------|
| `data/cluster_state_backup.json` | Legacy/dev JSON export artifact when present; not read/written by runtime and not packaged. Never delete casually if it may be the only offline recovery copy. |
| `data/id_maps_backup.json` | Legacy/dev JSON export artifact when present; not read/written by runtime and not packaged. Never delete casually if it may be the only offline recovery copy. |
| `system_config.menu_bootstrap_last_push_hash` | Profile-local bootstrap-push dedup hash (not a shared filesystem file). |
| `data/gp_model.pkl`, `data/models/` | Legacy local ML artifacts (no longer used after Phase 5; safe to delete on disk). |
| `data/restaurant-<hash>/weather_history.csv` | Per-profile weather export, rebuilt from that profile's `weather_daily` rows. Regenerated output — gitignored, never shared between restaurants. |

## Database

| Path | Role |
|------|------|
| `database/schema_sqlite.sql` | **Canonical local schema.** Tables for orders/items/addons, menu catalog, customers, merge/sync events, forecasts, mapping anomalies. |
| `database/schema_llm_cache.sql` | LLM cache schema. |
| `analytics.db` / `profiles/restaurant-<hash>.db` | ⚙️ One transient analytics SQLite file per bound physical restaurant. |
| `analytics-control.db` | Profile registry, global config, and selected restaurant; contains no analytics rows. |
| `src/core/db/connection.py` / `schema.py` / `reset.py` | Connection, schema-create, reset. |

## Scripts

| Path | Role |
|------|------|
| `scripts/seed_from_backups.py` | Explicit JSON export/restore CLI for dev/disaster recovery (`--export --out`, `--restore --from --yes`). |
| `scripts/backfill_mapping_anomalies.py` | Replay history to surface pre-existing silent-reuses (run once after deploy). |
| `scripts/rebuild_itemcode_mappings.py` | Rebuild itemcode → parent projection (dry-run default, `--apply` to write). Ops wrapper over `src/core/itemcode_mapping.py`. |
| `scripts/delete_catalog_stubs.py` | List/delete synthetic catalog-default stub rows in `menu_item_variants`. |
| `scripts/db_migrate.py` | Schema migrations. |
| `scripts/verify_sqlite.py` / `verify_customer_profile.py` | `make verify` + customer checks. |
| `scripts/build_release.sh` / `install_to_applications.sh` | Release build / install (see [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md)). |
| `scripts/start_app.sh` / `start_backend.sh` | Dev launch. |
| `scripts/e2e/`, `scripts/smoke/` | End-to-end / smoke checks. |

## Frontend 🎨 (`ui_electron/`)

| Path | Role |
|------|------|
| `ui_electron/src/pages/Menu.tsx` | Menu + Resolutions (Suspect Mappings, addon stats). |
| `ui_electron/src/pages/Orders.tsx` | Orders + addons view. |
| `ui_electron/src/api.ts` | Frontend → backend API client. |
| `ui_electron/src/contexts/StoreContext.tsx` / `components/StoreSelector.tsx` | Cached server list, persisted selection (restaurant or All Stores), stale-response generation guard, and All Stores availability/completeness state. |
| `ui_electron/src/components/SingleStoreOnly.tsx` | Gate that explains why a state-changing or one-store surface needs a physical restaurant. |

## Contracts & tests 🧪

| Path | Role |
|------|------|
| `contracts/README.md` | Shared contract package layout (keep identical with central). |
| `contracts/central_server_analytics_app_api_contract.md` | Full desktop ↔ central wire contract. |
| `contracts/MUTATION_SYNC_PROTOCOL.md` | Revision/mutation optimistic-concurrency protocol. |
| `contracts/fixtures/1/` | Versioned golden request/response + extraction fixtures. |
| `contracts/fixtures/1/menu_merge_event_fixtures.json` | Wire-format fixtures for menu merge events. |
| `contracts/fixtures/1/menu_mapping_verification_event_fixtures.json` | Verification event fixtures. |
| `tests/` | Pytest suite (clustering, mapping anomalies, variant enforcement, menu queries, …). |
| `tests/test_all_stores_federation.py`, `tests/test_all_stores_sync.py`, `tests/all_stores_test_helpers.py` | All Stores reducers, refusals, collision fixtures, timezone framing, and the sequential sync coordinator. |
| `tests/test_global_menu_phase1.py`, `tests/fixtures/global_menu_v1_snapshot.json` | Phase 1 global identity, projection, mutation reconciliation, capability, and coverage-gated federation tests. |
