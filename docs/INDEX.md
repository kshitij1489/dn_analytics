# Documentation Index

Single entry point for humans and AI agents. Read this first to route to the right doc without loading every plan.

**For session conventions:** [SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) Part A  
**For tool auto-discovery:** [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md), [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc)

---

## Start here

| Priority | Doc | Why |
|----------|-----|-----|
| 1 | This file | Task routing and token budget |
| 2 | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Canonical architecture: Brain vs Muscle, order ingest/replay, menu logic, schema highlights, development caveats |
| 3 | Task-specific doc from the table below | Only when the task matches |
| — | [FILE_INVENTORY.md](./FILE_INVENTORY.md) | Grouped file map — jump here to locate a module by concern |

New session rule: load **this index** + **SYSTEM_CONTEXT** before opening any 300+ line implementation plan. If your tool already auto-loaded `AGENTS.md`, `CLAUDE.md`, or `.cursor/rules`, do not attach those files again.

---

## Task routing

Read files **in order** for the task at hand. Stop when you have enough context; do not read the full chain unless needed.

| If you're working on… | Read (in order) |
|----------------------|-----------------|
| **Menu sync / merge conflicts / verification / fresh-install convergence** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) (§2 design; §3 schema; §8 runbook) → [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §17 |
| **Global menu identity across restaurants (implemented, dormant)** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §17 → [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §25 |
| **Cloud sync API (server-side / contract)** | [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) → [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §2 (as-built design) |
| **Customer merge sync / strict commits / replay quarantine** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §9, §14–§15 → [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §21 |
| **Order loading / ingest / replay / addons / stat counts** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) §2b → [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md) → `services/load_orders.py` |
| **Forecasting algorithms / model behavior** | [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md) §Forecasting Behavior Ported To Central |
| **Telemetry / bootstrap ingest API** | [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §18 (`errors/ingest`, `learning/ingest`, `conversations/sync`, `menu-bootstrap/ingest`) |
| **Central forecasting nightly job / desktop cutover (Phase 5)** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md) (§Implementation status + Phase 5) |
| **All Stores / multi-restaurant analytics / profile routing** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) §Restaurant-scoped desktop routing + §All Stores federation → [SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) Part B |
| **"Where is X?" / file map / unfamiliar module** | [FILE_INVENTORY.md](./FILE_INVENTORY.md) |
| **Build / release / .dmg packaging** | [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md) |
| **macOS app won't open / signing / quarantine** | [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md) §5 |
| **AI Mode (chat, intent, LLM)** | [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) → [FILE_INVENTORY.md](./FILE_INVENTORY.md) §AI Mode → `ai_mode/` source |
| **Item clustering / parsing / merge UX** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [item_clustering.md](./item_clustering.md) |
| **Merge child-variant dedupe (open work)** | [pending_task.md](./pending_task.md) → merge code in `src/core/` / `services/` |
| **General bug fix / unfamiliar area** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → grep codebase |

---

## Token budget guidance

| Tier | Docs | How to use |
|------|------|------------|
| **Always load** (small) | This index, [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Attach or @-mention at session start |
| **Auto-discovered when available** (tiny) | [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md), [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc) | Do not re-attach if the tool already loaded it |
| **Load on demand** (medium–large) | Task row from table above | @-reference specific sections; read headings first |
| **Never paste whole doc into chat** | [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) (~670 lines), [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) (~2200 lines) | Use @file + section anchors; summarize what you need in your own words |
| **Reference only** | [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md), [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) | Load the relevant section, not the full file |

**Progressive disclosure:** index → one doc section → source file.

---

## Status snapshot

Pulled from doc headers (refreshed 2026-08-11). See each doc for detail — do not treat this table as the source of truth. Refresh this table when a phase ships or a feature branch merges.

| Doc | Status (summary) |
|-----|------------------|
| [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md) | **As-built (2026-07-08).** Atomic single-transaction ingest, idempotent event replay (wipe/rebuild + stat recompute), addon storage + derived sync-time eligibility, customer double-count avoidance. |
| [FILE_INVENTORY.md](./FILE_INVENTORY.md) | **Reference.** Grouped, annotated file map. JSON backup artifacts are explicit export/restore only, not runtime stores. Keep in sync when adding load-bearing modules. |
| [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) | **As-built.** Assignment-based LWW sync + **strict-mode mutation commits, LIVE in prod for menu and customer since 2026-07-07** (menu validated §13, customer §15; live round-trips recorded). JSON layer removal Part A complete: runtime no longer reads/writes packaged seed JSON; bootstrap payloads are SQLite-derived; packaged seed JSON removed. **Part B complete (2026-07-10):** catalog-only human edits commit strict (`catalog_update`, fail-closed 409), bootstrap push demoted to seed mirror, machine-derived assignments flush create-only (`derived_assignment.sync`), C1 verify gate + itemcode `route_eligible` gate. Unresolvable customer replay events quarantine per §9. **State-scoped husk GC (§2.4.1, 2026-07-11/12):** post-sync sweep deletes zero-reference menu items/variants and customers past a 7-day grace — invariant "after any completed sync, no out-of-grace husk exists" self-heals older builds without migration (customers also get a source-side fix so anon resync mints no new husk). **§17 global menu groups (2026-08-11):** revision-1.6 client is implemented but dormant — additive projection, fail-closed capability ladder, shadow/aggregating coverage repair, mutation adapters and coverage-gated All Stores reducers are complete; production advertises no capability, and the operator deployment/activation stop gate (§17.3) is open in the central repo runbook. **Revision-1.7 shared-POS clean-rebuild work:** analytics Phases A–F are complete (fresh schema, observation export, shared catalog/price projection, unified history, shared mutations/UI, recoverable profile reset, ordered rebuild orchestration and diagnostics); coordinated central stop gate, profile resets and activation remain open. Open follow-ups in `pending_task.md`. |
| [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) | Shared wire contract. Baseline §5–15, §17 assignments, §19 menu commits, §21 customer commits, and the desktop §23–§24 revision-1.2 migration are **implemented**. Every scoped request carries an explicitly bound `X-Restaurant-ID`; raw analytics uses only `X-API-Key`. Retired wire surface is listed in §24. Desktop uses one SQLite profile per restaurant plus `analytics-control.db`; **All Stores is a local read-only federation and never reaches the wire** — every scoped request still names one physical restaurant. Twin-sync rule + checklist: [contracts/README.md](../contracts/README.md). |
| [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) | **All phases complete; hardened 2026-07-11/12.** Per-query telemetry (`ai_call_trace` + `ai_logs` cost columns, cache counters), single-source prompts (TS `prompts.ts` removed; SQL Console fetches `/api/ai/prompt-context`), curated allowlist schema context, read-only SQL guard on AI paths, cache hardening, race-free `ai_debug_log` + `asyncio.to_thread` offload. See §"Revised behavior & hardening". |
| [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md) | **Phase 1 revenue deployed.** Phases 2–4 code deployed; item/volume publish gated by the 95% assignment-coverage threshold (the server materializes locators from its own current order-line facts). **Phase 5 desktop cutover complete** — desktop is pull-only off the `central_forecast_*` cache. Also the single source for forecasting algorithms/model behavior (§Forecasting Behavior Ported To Central). |
| [SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) | Part A **reference** — cross-tool session conventions. Part B **as-built (2026-08-09)** — All Stores read-only federation: combination rules, dormant menu-identity gate, physical-store-only actions, sequential Sync DB coordinator, runbook, performance budget. |
| [pending_task.md](./pending_task.md) | **Open:** child-variant dedupe/reconcile during parent menu-item merge. |
| [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md) | Operational — release build via `./scripts/build_release.sh`. |
| [item_clustering.md](./item_clustering.md) | Conceptual reference for clustering hierarchy + variant parsing + silent-reuse guard (refreshed 2026-07-08). |
| [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Canonical architecture reference. Brain is central server state plus local SQLite cache; JSON exports are explicit disaster-recovery artifacts only. |

---

## Full doc list

| File | Role |
|------|------|
| [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Architecture & invariants |
| [INDEX.md](./INDEX.md) | This hub |
| [FILE_INVENTORY.md](./FILE_INVENTORY.md) | Grouped, annotated file map |
| [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md) | Order loading — atomic ingest, replay, addons, stat recompute |
| [SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) | Part A cross-tool AI conventions · Part B All Stores federation (combination rules, refusals, sync coordinator, runbook) |
| [../AGENTS.md](../AGENTS.md) | Root AI-agent instructions |
| [../CLAUDE.md](../CLAUDE.md) | Claude Code pointer to AGENTS + INDEX |
| [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc) | Cursor rule that routes agents through this hub |
| [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) | Menu clustering sync — as-built architecture, DB schema, operational runbook (menu strict-mode §12–§13, customer strict-mode §14–§15, replay quarantine §9) |
| [../contracts/README.md](../contracts/README.md) | Contract twin-sync rule (`analytics/contracts` ↔ `db.dachnona/contracts/desktop_analytics_app`) + server vs desktop implementation checklist |
| [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) | Shared server wire contract (canonical) |
| [MUTATION_SYNC_PROTOCOL.md](../contracts/MUTATION_SYNC_PROTOCOL.md) | Shared mutation/revision protocol |
| [CENTRAL_FORECASTING_NIGHTLY_PLAN.md](./CENTRAL_FORECASTING_NIGHTLY_PLAN.md) | Forecasting algorithms + central nightly job + desktop pull-only cutover (Phases 0–5) |
| [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) | AI chat mode architecture & task list |
| [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md) | Build and share .dmg |
| [item_clustering.md](./item_clustering.md) | Clustering logic explained |
| [pending_task.md](./pending_task.md) | Open merge dedupe task |
