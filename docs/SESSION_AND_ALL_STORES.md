# Session Conventions & All Stores

Two independent references in one file.

- **[Part A — AI session conventions](#part-a--ai-session-conventions)**: how agents load context in this repo.
- **[Part B — All Stores](#part-b--all-stores-multi-restaurant-analytics)**: the multi-restaurant read federation, as built.

**Hub:** [INDEX.md](./INDEX.md) · **Invariants:** [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) · **Root pointers:** [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md)

---

# Part A — AI session conventions

For Cursor, Claude Code, Codex, Antigravity, and similar agents.

## Startup checklist

1. **Route** — read [INDEX.md](./INDEX.md); pick the task row matching the goal.
2. **Load core** — [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md), always. Do not re-attach [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md), or Cursor rules the tool already auto-discovered.
3. **Load targeted** — one doc or section from the INDEX routing table; skip unrelated 300+ line docs.
4. **Confirm status** — check the INDEX status snapshot before assuming a phase is done or open.
5. **Trust code over plans** — grep/read `src/` for as-built behavior; plans lag code.

The per-task reading order lives in the [INDEX routing table](./INDEX.md#task-routing) — it is not duplicated here.

## Attachment conventions

| Tool | Convention |
|------|------------|
| **Cursor** | Project rule [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc) routes to the hub. Use `@docs/INDEX.md`, `@docs/SYSTEM_CONTEXT.md`, then one task doc/section. |
| **Claude Code** | `CLAUDE.md` points to `AGENTS.md` and the hub. Attach specific files only; avoid `/add-dir docs` unless doing a docs-wide audit. |
| **Codex / OpenAI** | `AGENTS.md` is the root instruction file. If context is manual, instruct: "Read docs/INDEX.md then docs/SYSTEM_CONTEXT.md only." |
| **Antigravity** | Point to `AGENTS.md` and `docs/INDEX.md` in project instructions; @-reference deep docs per task. |

Rules: @-mention **files**, never folder dumps. For long docs name the section ("MENU_SYNC_ARCHITECTURE §2 only"). When context is dropped, re-@ the section — do not re-paste the doc.

## When to update docs vs code

| Update **code** | Update **docs** |
|----------------|-----------------|
| Fixing behavior, tests, APIs | Phase status changed → plan header + INDEX snapshot |
| Refactor with no contract change | New invariant agents must know → SYSTEM_CONTEXT or AGENTS.md |
| | New major plan or superseded section → INDEX routing + status table |
| | Runbook steps discovered during an incident → relevant plan § |

**Do not** rewrite long plans for small fixes. **Do** add a one-line status note to the plan header and INDEX snapshot when a phase ships or is abandoned.

---

# Part B — All Stores (multi-restaurant analytics)

**Status: as-built, shipped 2026-08-09.** Per-restaurant profiles are Phase 1, shipped 2026-08-08 — see [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) §Restaurant-scoped desktop routing.

## B1. What All Stores is

A **local, read-only query federation** over the bound restaurant profiles. Not a database. Nothing is copied or re-ingested into a mixed file, no cursor is shared, and the `__all__` token never leaves the desktop: the central contract has no such selector, so `X-Restaurant-ID` always names one physical restaurant.

- Membership = profiles that are **authorized** and have an **initialized (bound) database**, sorted by restaurant ID.
- Selecting All Stores requires **two or more** such profiles. An existing All selection keeps working with one survivor; the revoked store is reported as `excluded_profiles`, never dropped silently.
- Local scope travels as `X-Analytics-Scope: __all__`. The backend resolves it to an immutable snapshot for the request (`src/core/analytics_scope.py`).

## B2. Response shape

An All Stores read returns the single-store payload inside a completeness envelope:

```json
{
  "scope": "all",
  "profiles_requested": 2,
  "profiles_included": 1,
  "incomplete_profiles": [{"restaurant_id": "…", "error": "…", "code": "…"}],
  "excluded_profiles": [{"restaurant_id": "…", "code": "restaurant_unauthorized"}],
  "stores": [{"restaurant_id": "…", "restaurant_name": "…", "timezone": "…"}],
  "data": { }
}
```

The frontend unwraps `data` (so every page keeps its single-store shape) and routes the metadata to the sidebar banner. A profile that cannot be read is **never counted as zero** — it appears in `incomplete_profiles`.

Menu-identity endpoints add one optional top-level key, `identity_coverage` (see B3); page data shape is unchanged.

## B3. Combination rules (authoritative)

| Kind | Rule |
|---|---|
| Counts, revenue, tax, discount, quantity, addon counts | Summed |
| Averages, percentages, shares | Recomputed from combined numerator/denominator — never an average of store-level rates |
| Average order value | `sum(revenue) / sum(successful orders)` |
| Time series | Grouped by the returned business-date bucket, summed, then zero-filled **after** combining |
| Hourly average revenue | `sum(revenue) / union of business dates` across stores |
| Row tables (orders, items, addons, taxes, discounts, customers) | Sorted union; every row carries `restaurant_id`, `restaurant_name`, and `row_key = <restaurant_id>:<local key>`. NULL sort values order as SQLite does — first ascending, last descending |
| Same variant priced differently per store | Lowest price shown; each store's own price stays in `contributors` |
| Local integer PKs, Petpooja order IDs, menu IDs, customer IDs | Never treated as globally unique |
| Menu/item analytics | Grouped by durable dimensions (item name, type, variant name, unit, value) with `contributors` back to `{restaurant_id, menu_item_id, variant_id}` — see the identity gate below |
| Customers | Profile-qualified. Two stores' records are never merged by name, phone, address, or local ID. Counts mean "store-customer records" |
| Customer rates | Recomputed by re-running the same pure calculators over combined order atoms (`src/core/queries/customer_metric_sources.py`) |
| Forecast revenue/orders/quantity/volume/prep | Summed per date and model |
| Forecast published bounds | Summed as a **labelled operational envelope** (`interval_basis: summed_store_bounds`), not a recomputed portfolio interval |
| Forecast probability | Weakest store value (`probability_basis: min_store`) — never averaged or multiplied |
| Weather | Stays profile metadata; forward weather overlays are dropped from combined forecasts |

Reducers are declared per endpoint next to the route. There is deliberately no generic "sum every numeric field" helper — see `src/core/queries/multi_store_reducers.py`.

### Menu identity gate

`group_menu_identity_rows` switches to global item/variant IDs **only if every included profile is `aggregation_ready`** (active global menu + aggregation advertised + bootstrap complete). Coverage is diagnostic and does not gate that switch. One lagging store keeps the whole request on the legacy name-based path — it never mixes bases. Rows without a global link are keyed by `restaurant_id` plus local IDs and flagged `identity_unlinked`; they are never name-merged into a global group. Linked/total counts and the quarantine count ride on the envelope's `identity_coverage`. A profile whose database predates the schema degrades to the legacy reducer and reports `active: false` — never a false global-ready.

### Business dates and timezones

One instant is captured per request. Each profile's business date is evaluated in **its own timezone** with the existing 05:00 cutoff, so two stores can never compute "today" from different clock readings. Combined-atom queries (customer metrics) use the furthest-ahead local business date as their calendar frame.

## B4. What All Stores refuses

Anything that changes state, or that resolves one store's records, requires one physical restaurant. The backend answers `409 {"code": "single_restaurant_required"}` **before** opening a database or building a central request:

menu and customer mutations, resolutions, merge/undo, remap, variant creation, customer profile drilldown, similarity/merge preview, manual Petpooja pull, reset, SQL Console, AI Mode, weather sync, and the config user endpoints.

Configuration stays open in All Stores mode — it is where a restaurant gets picked — but its User Profile tab renders the single-store notice and does not call `/config/users` or `/config/sync-identity`.

Any endpoint without an explicit reducer is single-restaurant-only by default — adding a route cannot accidentally make it federated.

## B5. Sync DB in All Stores mode

`POST /api/sync/run` with `restaurant_id: "__all__"` freezes the authorized profile list **before** the job thread starts and runs the ordinary single-store sync once per store, sequentially (`src/core/services/all_stores_sync.py`).

- Each iteration opens that profile's database, builds that profile's central request context, and closes the connection before the next store.
- Progress reads `Store 1 of 2 — <name> — <phase>`.
- A store-local failure is recorded and the loop continues. A process-wide credential/contract failure (`invalid_api_key`, `retired_parameter`, …) stops the loop; remaining stores are reported `not_attempted`.
- Terminal outcome is `completed`, `partial`, or `failed`, with per-store status, counts, and failure codes in `stats.stores`. A `partial` job finishes with job status `completed`, so the desktop reads `stats.outcome` and names the failed stores in both the sync label and a popup — a partial run never reads as clean.
- The whole job holds the process-wide cloud-pull lock, so a five-minute scheduler cycle cannot interleave between two stores.
- The frontend bumps its data epoch once, after the loop.

The five-minute scheduler fans its **cloud-only** cycle over the same profile list (never the raw-order loop). App-global error logs upload once per cycle; scoped menu-bootstrap seeds and per-profile telemetry run for every store.

## B6. Runbook and diagnostics

| Symptom | Meaning / action |
|---|---|
| All Stores option disabled | Fewer than two authorized, initialized profiles. Refresh Stores in Configuration, then select and sync each restaurant once. |
| Banner "Incomplete: \<store\> (…)" | That profile could not be read (missing file, locked, identity mismatch). Its rows are absent from the totals — it is not zero. Check the file under `profiles/` and the identity row. |
| Banner "Excluded (no current authorization)" | The profile is bound but its central grant was revoked. Restore the grant, then Refresh Stores. |
| `single_restaurant_required` | Expected: the action is physical-store-only. Pick a restaurant in the sidebar. |
| `deep_page_not_supported` | Offset fan-out bound (5000 rows) hit. Filter or search instead; keyset pagination is the fix if this becomes routine. The whole request is refused — a bound is never met by dropping a store from the result. |
| Forecast shows "incomplete" | At least one store has no usable central forecast family. Run Sync DB for that store; do not read the total as complete. |
| Sync result `partial` | At least one store completed and at least one failed; per-store errors are in the job stats. Re-run after fixing that store. |

Support queries (read-only): `restaurant_profiles` / `app_selection` in `analytics-control.db`, `restaurant_profile_identity` in each profile database. `app_selection.selection_mode` is `'all'` with a NULL `restaurant_id` when All Stores is selected — `__all__` is never stored as a restaurant ID.

## B7. Performance budget

Measured on a 2-store install with 5,000 orders per store (Apple silicon, local SQLite, warm cache): `/insights/kpis` 4.3 ms single-store vs 6.7 ms federated; `/orders/view` page 1 (50 rows) 5.5 ms vs 10.1 ms. Federated-only figures: `/orders/view` page 20 at 41.7 ms, `/insights/daily_sales` at 9.5 ms.

Cost is linear in store count: one read-only connection per profile per request, opened and closed inside the request. Deep offset pages cost `page × page_size` rows **per store**, which is why the 5,000-row bound exists. At the expected near-term maximum (roughly 5 stores) a page-1 table read stays well under 50 ms; move these endpoints to keyset pagination before growing far beyond that.

## B8. Where the code lives

| Path | Role |
|---|---|
| `src/core/analytics_scope.py` | `AnalyticsScope`, snapshot resolution, `__all__` handling |
| `src/core/queries/multi_store.py` | Read-only fan-out, envelope, per-profile timezone binding |
| `src/core/queries/multi_store_reducers.py` | Declarative rules (`Sum`, `Ratio`, `Min`, `Max`, `First`, `AllTrue`, `AnyTrue`), row/group helpers, menu identity gate |
| `src/core/queries/multi_store_forecast.py` | Forecast combination and incomplete-store reporting |
| `src/core/queries/customer_metric_sources.py` | Profile-qualified customer order atoms |
| `src/core/services/all_stores_sync.py` | Sequential Sync DB coordinator |
| `src/api/dependencies.py` | `ScopedReader` (`read`, `read_together`), scope resolution, All-mode refusals |
| `ui_electron/src/contexts/StoreContext.tsx`, `components/StoreSelector.tsx`, `api.ts` | Selection, envelope unwrapping, completeness banner |

**Tests** — these also encode the shipped Phase 2 stop gate, so keep them green rather than re-deriving the gate:

- `tests/test_all_stores_federation.py` — per-route reducers, collision fixtures, AOV/share/rate recomputation, `union_rows` + `row_key` pagination and tie-breaks, `incomplete_profiles` / `excluded_profiles`, HTTP-level blocked routes, per-profile cursor isolation.
- `tests/test_all_stores_sync.py` — full profile-isolated sync per store, per-store outcomes, `partial` reporting.
- `ui_electron/src/api.scope.test.ts` — envelope unwrapping and scope headers.
