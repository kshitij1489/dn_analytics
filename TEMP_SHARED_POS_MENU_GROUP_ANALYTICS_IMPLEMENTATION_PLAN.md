# Temporary Analytics Implementation Plan: Group 1 Canonical Menu and POS Aliases

**Status:** revision-1.8 central and Analytics runtime implementation completed 2026-08-12; coordinated dormant deployment, production evidence, initial reconciliation and lifecycle rollout remain open

**Frozen wire contract:** `contracts/central_server_analytics_app_api_contract.md`, revision 1.8, especially §9 and §25.12

**Golden fixtures:** `contracts/fixtures/1/global_menu_alias_fixtures.json`; revision-1.7 fixtures remain unchanged

**Central plan:** `../db.dachnona/TEMP_SHARED_POS_MENU_GROUP_CENTRAL_IMPLEMENTATION_PLAN.md`

**Deployment model:** one controlled desktop installation containing the Dach & Nona and Super Mart restaurant profiles

**Created:** 2026-08-11

**Rewritten for revision 1.8:** 2026-08-12

**Remove after:** Group 1 is active under the alias policy, rollout evidence is recorded in permanent documentation and both temporary plans are retired

## 1. Outcome

Dach & Nona and Super Mart must consume one Group 1 canonical menu even though Petpooja issued different numeric item/addon locators to each outlet.

- Dach & Nona remains the reviewed source of canonical items, variants and redirect history.
- Multiple reviewed outlet POS locators may resolve to the same global item/variant pair.
- A stable provider `itemcode` may suggest a parent item when current group evidence makes it unique; it never guesses a variant.
- Both profiles cache the same canonical items, variants, redirects, itemcode rules, active group POS aliases and unified history.
- The UI renders one row per canonical item/variant, not one row per POS alias.
- Orders, assignments, quantities, revenue, availability and eligibility remain restaurant-scoped.
- Alias decisions are stored centrally and do not mutate local menu rows, assignments or history when the operator clicks approve.
- Unknown future locators fail closed into the central unresolved/quarantine workflow.

Example:

```text
itemcode:Tiramisu
pos_item:1283777806  ─┐
pos_item:1312789339  ─┴─> one canonical Classic Tiramisu item/variant
```

The central server is durable authority. Each local SQLite profile is a rebuildable cache and restaurant-specific analytics projection.

| Data | Analytics storage/presentation | Authority |
|---|---|---|
| Canonical items | `global_menu_items` | central menu group |
| Canonical variants | `global_variants` | central menu group |
| POS aliases and itemcodes | `global_menu_mapping_rules` | central menu group |
| Redirects | `global_menu_redirects` | central menu group |
| Convergence events | `global_menu_events` | central menu group |
| Human audit history | `global_menu_history` | central unified history endpoint |
| Alias observations | uploaded private evidence; not a shared local catalog | selected restaurant |
| Alias decisions | central queue/API; UI view only | central menu group |
| Local catalog projection | `menu_items`, `variants`, link tables and `menu_item_variants` | rebuildable cache |
| Orders, assignments and sales facts | existing restaurant tables | selected restaurant |

## 2. Why revision 1.7 is not the Group 1 policy

Revision 1.7 `global_menu_shared_pos_catalog_v1` requires the same numeric Petpooja locator to mean the same item, variant and price wherever it appears. Group 1 does not satisfy that invariant: Dach & Nona and Super Mart share logical products and many `itemcode` values, but their numeric locator sets do not overlap.

Therefore:

1. Keep revision-1.7 shared-POS behavior and fixtures unchanged for a future genuinely identical-locator group.
2. Do not advertise or emulate `global_menu_shared_pos_catalog_v1` for Group 1.
3. Implement the separate `global_menu_group_pos_aliases_v1` client behavior.
4. Never activate shared behavior from menu-group membership, cached rules or settings inferred by the client.
5. Do not execute or consume the saved revision-1.7 Group 1 reconciliation plan.

Revision 1.8 is additive and keeps wire `schema_version: 1`.

## 3. Current Analytics baseline

Already available from revisions 1.6/1.7 and retained:

- local global-menu schema, snapshot/event/assignment application and redirect handling;
- canonical menu and unified history caches;
- server-authoritative global mutation flow;
- restaurant selection and profile-scoped sync;
- shared-POS code guarded by `global_menu_shared_pos_catalog_v1`;
- recoverable profile archive/reset workflow;
- revision-1.8 twin contract and `global_menu_alias_fixtures.json`, byte-identical to central (all four twin artifacts verified with `cmp`);
- fixture ownership tests in `tests/test_global_menu_alias_contract_v18.py`, covering all 23 fixtures.

Completed in Analytics Phase C:

- distinct alias advertised/ready status, mutual-exclusion failure and frontend capability gates;
- group-scoped POS rule acceptance under either valid policy with policy-specific assignment parity errors;
- redirect-resolved, many-to-one alias projection through snapshot and event sync;
- two-profile convergence with restaurant-local assignment/availability state preserved;
- shadow canonical-catalog access before alias activation;
- fail-closed unknown-locator quarantine without restaurant-local canonical creation.

Implemented for revision 1.8:

- alias observation generation/upload and acknowledgement handling;
- central alias queue/preview/commit/status/plan client operations, including the editor credential on the queue read and the `{"v","g","locator_type","locator_value"}` exclusive cursor;
- the operator resolution UI;
- alias-aware readiness, diagnostics and sync behavior;
- focused backend/frontend tests and packaging verification.

All revision-1.8 fixtures are now owned by live client/transport/projection tests. This proves the dormant implementation, not production activation: coordinated deployment, live observations, review, backup/digest evidence and reconciliation remain operator gates.

## 4. Locked Analytics invariants

1. `global_menu_group_pos_aliases_v1` and `global_menu_shared_pos_catalog_v1` are distinct and mutually exclusive policies.
2. The alias capability is absent until central commits the initial reconciliation. Before then, `global_menu_resolution_v1` must still permit canonical target selection and alias review.
3. The client never accepts or sends a caller-selected `menu_group_id`; the selected `X-Restaurant-ID` resolves group scope centrally.
4. `group_pos_alias_observation` and `shared_pos_catalog` are separate evidence channels and must never occur in the same bootstrap request.
5. Raw locator and `itemcode` values are trimmed but otherwise preserved. Do not case-fold or synthesize provider identifiers.
6. Decimal price and variant values use exact two-decimal strings. Never round-trip them through binary floating point.
7. `itemcode` identifies only a parent item unless exact current evidence proves an unambiguous variant.
8. Candidate reasons are visible suggestions. Name or itemcode matching never silently approves a decision.
9. Decision preview/commit writes no local `menu_items`, variants, merge history or assignments.
10. Only central's digest-pinned reconciliation changes rules/assignments and activates the alias capability.
11. Group-scoped aliases may be many-to-one. Local presentation deduplicates by redirect-resolved global item/variant.
12. Assignments and facts remain restaurant-filtered even though every member receives the group alias rules.
13. A known alias resolves consistently in every member; a contradictory assignment is a parity error.
14. An unknown later locator is unresolved/quarantined. It never falls back to restaurant-owned canonical creation.
15. Historical order prices, totals, revenue and legacy merge events are immutable.
16. Snapshot, event, assignment, decision and history operations are idempotent and cursor-safe. Failure never advances a cursor or displays false success.
17. Normal rollout uses Sync DB. No local or central fact reset is required.

## 5. Boundary with the central repository

Analytics can implement against frozen revision-1.8 fixtures, but activation depends on the central implementation.

| Central dependency | Analytics behavior before it exists | Required rollout evidence |
|---|---|---|
| §9 accepts `group_pos_alias_observation` | fixture-driven shipper tests | live acknowledgement is `group_pos_alias_observation_updated: true` |
| Alias resolution queue | UI remains gated with an actionable unavailable state | authenticated group-filtered evidence loads |
| Decision preview/commit/status | mocked client and UI tests | stale and timeout paths pass live contract tests |
| Reconciliation plan/status | read-only diagnostics remain gated | exact digests/counts render without client reinterpretation |
| Alias-aware snapshot/events/assignments | parser/projector fixture tests | central serializers and filtering pass |
| `global_menu_group_pos_aliases_v1` | no alias projection readiness | advertised only after one applied `pos_aliases` run |

Central deploys additive schema and dormant endpoints first. Analytics must be deployed before Group 1 alias observations are collected. Capability activation is controlled only by central policy plus a successful initial reconciliation.

## 6. Analytics implementation phases

### Phase A — Maintain the frozen revision-1.8 contract gate

Primary files:

- `contracts/central_server_analytics_app_api_contract.md`
- `contracts/fixtures/1/global_menu_alias_fixtures.json`
- `contracts/fixtures/1/README.md`
- `tests/test_global_menu_alias_contract_v18.py`

Tasks:

1. Keep the Analytics contract and alias fixture byte-identical to the central copies.
2. Keep every revision-1.7 fixture meaning unchanged.
3. Pin every new fixture to a real client parser, service, route or UI test as runtime implementation lands.
4. Preserve exact capability names, error codes, cursor rules, digest fields, idempotency behavior and `schema_version: 1`.
5. Reject unreviewed contract drift before runtime changes continue.

Exit criterion: parity commands pass and every revision-1.8 fixture has executable Analytics ownership, not only structural assertions.

### Phase B — Build and upload truthful alias observations

Primary files:

- `src/core/menu_catalog_seed.py`
- `src/core/menu_bootstrap_shipper.py`
- `src/core/global_menu_schema.py`
- related bootstrap tests

Tasks:

1. Add a pure deterministic `build_group_pos_alias_observation(conn)` over actually observed raw order/addon evidence.
2. Emit the exact §9 fields: locator kind/value, raw nullable `itemcode`, local diagnostic ids, item/variant identity and current observed price.
3. Do not describe sold evidence as a complete active Petpooja catalog.
4. Preserve raw `petpooja_itemid`, addon id and provider `itemcode`; reject duplicate kind/value rows and cross-kind locator collisions.
5. Serialize decimals as exact two-decimal strings and sort rows deterministically.
6. Select exactly one observation channel:
   - use `shared_pos_catalog` only when the selected store advertises the revision-1.7 shared-POS capability;
   - otherwise, for the Group 1 resolution/alias workflow, send `group_pos_alias_observation` as the dormant review evidence;
   - never put both keys in one request.
7. Include the selected observation and its channel name in the bootstrap hash so identity, itemcode or price-only changes upload.
8. Persist the hash only after the matching acknowledgement field is `true`.
9. Treat omission differently from explicit `[]`; never erase prior evidence accidentally.
10. Keep `cluster_state` as `seed_only`; an observation is not canonical authority.

Tests cover items, addons, no-variant rows, raw itemcode preservation, deterministic ordering/hash, price-only and itemcode-only changes, unchanged skip, explicit empty, wrong acknowledgement, retries, invalid decimals, duplicates, cross-kind collisions and strict single-channel payloads.

### Phase C — Add alias capability and policy-aware projection — **complete 2026-08-12**

Primary files:

- `src/core/global_menu_schema.py`
- `src/core/global_menu_sync.py`
- `src/core/global_menu_identity.py`
- `src/api/routers/menu.py`
- `ui_electron/src/globalMenuCapabilities.ts`
- `ui_electron/src/types/api.ts`
- related backend/frontend tests

Tasks:

1. Add the `global_menu_group_pos_aliases_v1` constant and explicit advertised/ready status fields.
2. Fail closed if a store advertises both alias and shared-POS capabilities.
3. Keep canonical items, variants and redirects readable under `global_menu_v1`/`global_menu_resolution_v1` during shadow, before alias readiness.
4. Accept group-scoped `pos_item`/`pos_addon` rules under either valid POS policy, while preserving their different readiness/status semantics.
5. Resolve every item/variant target through redirects and reject missing, inactive or out-of-group targets.
6. Materialize many locator rules onto one deterministic canonical local item/variant projection without duplicate canonical rows.
7. Keep restaurant assignments filtered; reject a local assignment that contradicts an active reviewed group alias.
8. Apply known future aliases immediately through normal snapshot/event/assignment sync.
9. Surface unknown aliases as quarantine/unresolved diagnostics; never create a restaurant-local canonical identity automatically.
10. Preserve restaurant-local availability/eligibility and all historical order values during projection refresh.
11. Make snapshot and event application transactional, idempotent and cursor-safe.
12. Generalize existing shared-POS helpers only where semantics truly overlap; retain policy-specific guards and error codes.

Tests use two profile databases and prove many distinct POS ids converge to one canonical pair, canonical rows are deduplicated, both profiles converge on catalog/history digests, assignments remain isolated, redirects converge, known aliases link, unknown aliases quarantine, contradictory assignments fail, capability absence preserves old behavior and both policy capabilities together fail closed.

Implementation evidence: `tests/test_global_menu_alias_contract_v18.py` applies the frozen many-alias snapshot and reconciliation event through the real projector, exercises two independent SQLite profiles, redirect survivors, assignment isolation/parity refusal and unknown-locator fail-closed ingest. The revision-1.7 global-menu suite remains green, and frontend capability tests cover shadow catalog access plus mutually exclusive alias/shared readiness.

### Phase D — Implement the central alias-resolution client

Primary files:

- focused new core alias-resolution client/service module
- `src/core/central_api.py` or the existing scoped request abstraction
- `src/api/routers/menu.py`
- `ui_electron/src/api.ts`
- `ui_electron/src/types/api.ts`
- related API/service tests

Required operations:

- paged `GET global-menu/alias-resolutions` with status filter and opaque cursor;
- `POST global-menu/alias-resolutions/preview`;
- `POST global-menu/alias-resolutions/commit`;
- `GET global-menu/alias-resolutions/{mutation_id}` after an uncertain commit;
- read-only `GET global-menu/alias-reconciliation/plan`;
- read-only `GET global-menu/alias-reconciliation/status`.

Tasks:

1. Use selected restaurant sync authorization on every call and editor credentials on protected decision operations.
2. Never expose a client-supplied menu-group scope parameter.
3. Strictly validate fixture-pinned rows, digests, prices, candidate reasons, decision states, conflicts and pagination.
4. Carry expected group revision and observation digest through preview and commit.
5. Generate one UUID mutation id per semantic commit and reuse it only for byte-equivalent retry.
6. On timeout, look up that mutation id before offering another commit.
7. Map revision, observation and preview conflicts to `refresh_alias_resolution`; map mutation-id conflicts to a new mutation id.
8. Do not apply decision responses to local menu, assignment or history tables.
9. Preserve central error codes and machine-readable fields for the UI; do not reduce them to ambiguous prose.

Tests cover queue pagination, filters, invalid cursor, preview writes nothing locally, commit/replay, timeout lookup, unknown/malformed mutation id, stale revision/evidence, changed preview digest, mutation-id collision, authentication/editor gating and restaurant switching.

### Phase E — Build the alias resolution UI

Primary files:

- `ui_electron/src/pages/Menu.tsx`
- `ui_electron/src/globalMenuCapabilities.ts`
- `ui_electron/src/api.ts`
- `ui_electron/src/types/api.ts`
- focused frontend tests

Tasks:

1. Show the alias-review experience when `global_menu_resolution_v1` is available, without waiting for alias capability activation.
2. Load canonical targets from the local global snapshot, never from the selected restaurant's local menu.
3. Present source evidence and canonical target side by side: restaurant, raw locator, raw itemcode, item/type, variant dimensions and outlet price. Show the authority/current canonical price separately, never default it from the selected outlet, and require its own confirmation when prices differ.
4. Display candidate reason (`exact_existing_alias`, `unique_itemcode`, `exact_identity`, `manual`) and conflicts explicitly.
5. Require visible operator confirmation; never bulk-approve or silently accept a name/itemcode suggestion.
6. Allow pending drafts and approved decisions according to the contract, with reviewer reason and attribution.
7. Preview before commit, refresh after success and reconcile uncertain commits by mutation id.
8. On stale evidence/revision/preview, show that the earlier choice was not saved and reload current evidence/targets.
9. Distinguish pending, approved, stale, applied and quarantined states.
10. Keep canonical create/merge operations separate. A truly new product must be created through a reviewed global mutation before its locator is mapped.
11. Keep restaurant switching from changing the canonical target list while source evidence remains clearly restaurant-labelled.
12. Deduplicate Group Catalog/Menu Matrix by canonical global item/variant and do not render every alias as another product.
13. Keep All Stores read-only and never expose private cross-restaurant evidence outside the authorized resolution view.

Acceptance: approving a decision changes only central decision state; local menu rows, merge history, assignments and facts remain byte/count stable until central reconciliation and subsequent sync.

### Phase F — Integrate sync, status and diagnostics

Primary files:

- `src/core/services/cloud_pull_orchestrator.py`
- `src/core/services/sync_service.py`
- sync operation coordinator/routes
- diagnostics/status queries
- related profile and sync tests

Tasks:

1. Pull canonical snapshot data for resolution during shadow even when neither POS-policy capability is ready.
2. Upload the selected profile's alias observation after its local order/catalog evidence is settled.
3. Do not use All Stores for rollout evidence; sync Dach & Nona and Super Mart individually.
4. After activation, pull the same group catalog, redirects, itemcodes, aliases and unified history into both profiles.
5. Pull only the selected restaurant's assignments and facts.
6. Expose policy, initial reconciliation state/digests, canonical/alias/verified coverage, pending/stale/quarantined counts and per-restaurant observation coverage.
7. Treat `0/0` as incomplete for every activation gate.
8. Keep history failure a visible warning rather than a catalog-authority mutation.
9. Preserve old cursors/data on malformed or failed pages.
10. Verify no code path interprets group membership alone as shared-POS or alias readiness.

Exit criterion: both profiles converge on the same canonical catalog/history digests, each retains only its own assignments/facts, all status gates render truthfully and no unexplained quarantine remains.

### Phase G — Test, build and package

Minimum backend coverage:

- revision-1.7 behavior remains unchanged without alias capability;
- alias observation raw fields, hashing, omission/empty semantics and profile privacy;
- canonical target availability before alias readiness;
- alias queue/preview/commit/status and timeout behavior;
- many POS ids to one canonical pair and repeated-itemcode variant ambiguity;
- redirect convergence, deduplication and policy mutual exclusion;
- known future alias linking and unknown alias quarantine;
- no decision-click mutation of local authority/facts;
- normal sync and optional archive/reset remain profile-scoped and recoverable.

Minimum frontend coverage:

- capability gates and mutual exclusion;
- canonical target picker source;
- evidence/target visual separation;
- explicit candidate confirmation;
- stale/conflicting commit handling;
- state labels and restaurant switching;
- no duplicate canonical rows and no membership-only shared-POS activation.

Run the focused suites established by the implementation, then the repository's broader Python suite, frontend unit tests, TypeScript/Vite build, packaged-backend build and Electron macOS ARM64 package. Finish with:

```bash
cmp contracts/central_server_analytics_app_api_contract.md ../db.dachnona/contracts/desktop_analytics_app/central_server_analytics_app_api_contract.md
cmp contracts/fixtures/1/global_menu_alias_fixtures.json ../db.dachnona/contracts/desktop_analytics_app/fixtures/1/global_menu_alias_fixtures.json
python3 -m unittest tests.test_global_menu_alias_contract_v18
git diff --check
```

Use the exact current package scripts documented by this repository for broader tests and packaging.

## 7. Coordinated rollout

Do not activate this from Analytics alone.

1. Capture fresh central backup/checksum and read-only baselines. Keep Group 1 `shadow` and absent from both POS-policy settings.
2. Verify revision-1.8 contract/fixture parity in both repositories.
3. Deploy dormant central schema/endpoints without advertising alias capability.
4. Deploy the compatible Analytics build.
5. Sync Dach & Nona individually, then Super Mart individually, and verify raw alias observations against Petpooja evidence samples.
6. Configure Group 1 only in `GLOBAL_MENU_GROUP_POS_ALIAS_GROUPS`; confirm shared-POS remains absent and alias capability is still absent.
7. Load and review the central plan/queue. Resolve all pending aliases; treat itemcode and exact identity as suggestions and review ambiguous variants/prices manually.
8. Re-run the plan until unresolved, stale and blocking counts are zero. Review projected assignment changes and immutable-fact checksums.
9. After a second fresh backup, central executes the exact digest-pinned `reconcile_global_menu_group_aliases` plan.
10. Verify one applied `pos_aliases` run/event/revision, preserved assignment counts and unchanged facts/history.
11. Confirm `global_menu_group_pos_aliases_v1` is advertised to both members and `global_menu_shared_pos_catalog_v1` remains absent.
12. Sync Dach & Nona, then Super Mart. Compare catalog/history digests, assignment coverage and quarantine counts.
13. Advance `shadow` to `aggregating`, observe, then `active` only after canonical, alias-decision and verified-assignment gates all pass.

Normal rollout does not reset either local profile. After central activation, an operator may optionally archive/reset only the five-day Super Mart profile and Sync DB from cursor zero as a clean-rebuild proof. Record the archive path and preserve `analytics-control.db` and recovery exports. Never delete central facts, `core_event`, orders or order lines.

## 8. Stop conditions

Stop before central execute or lifecycle advancement if:

- Group 1 is not `shadow`;
- a fresh backup/checksum or preflight evidence is missing;
- central and Analytics contracts or alias fixtures differ;
- either client advertises/infers shared-POS behavior for Group 1;
- both POS-policy capabilities appear together;
- authority catalog, redirect or observation digest changes after review;
- any decision is unresolved, ambiguous, stale or blocking;
- a locator has contradictory meaning or kind;
- the plan proposes dropped assignments or changes to orders/facts/history;
- the plan digest differs from the reviewed digest;
- Analytics cannot load the canonical target catalog during shadow;
- either profile duplicates canonical rows, leaks restaurant facts or hides quarantine.

## 9. Rollback

Before initial reconciliation, rollback is withdrawal of the dormant Analytics build/central code or policy configuration; no alias authority should exist.

After successful reconciliation:

1. central withdraws `GLOBAL_MENU_GROUP_POS_ALIAS_GROUPS` or returns Group 1 to `provisioning` to stop capability advertisement;
2. stop affected sync operations if projection is unsafe;
3. retain central alias rules, decisions, run/event evidence, assignments and facts for diagnosis;
4. retain local caches or archived profiles; do not destructively rewrite them;
5. fix forward through reviewed central mutation/alias workflows and publish a corrected snapshot/event revision;
6. re-enable only after status, snapshots and both profiles agree.

Do not delete global ids, redirects, legacy events, decisions, applied runs, assignments or facts as rollback.

## 10. Permanent documentation and cleanup

After successful rollout, update:

- `docs/MENU_SYNC_ARCHITECTURE.md` with alias ownership, resolution flow, sync order and quarantine behavior;
- the Analytics sync/resolution runbook with decision preview/commit/timeout handling;
- capability and lifecycle documentation with the mutually exclusive POS policies and three activation gates;
- `docs/FILE_INVENTORY.md` for any new focused modules;
- `docs/INDEX.md` status snapshot;
- `contracts/README.md` twin-sync checklist if required;
- rollout evidence index with backup, observation, plan, result, catalog and history digests.

Delete this temporary Analytics plan and the central temporary plan only after permanent documentation is reviewed and Group 1 is active.

## 11. Definition of done

- Revision-1.8 contract and alias fixture copies are byte-identical across repositories.
- Raw locator plus raw `itemcode` observations are private, profile-scoped and acknowledged durably.
- Operators resolve aliases against the canonical global target catalog during shadow.
- Decisions are audited, digest-bound and do not mutate local authority on click.
- Multiple outlet POS ids resolve to one canonical item/variant without duplicate menu rows.
- Both profiles converge on identical canonical catalog and unified-history digests.
- Assignments, orders and facts remain restaurant-isolated and historical values are unchanged.
- Known aliases link normally; unknown aliases quarantine without guessing.
- `global_menu_group_pos_aliases_v1` appears only after one successful initial reconciliation.
- `global_menu_shared_pos_catalog_v1` remains absent for Group 1.
- Canonical, alias-decision and verified-assignment coverage gates pass before `active`.
- Normal rollout completes without a required profile reset; any optional reset is archived, recoverable and profile-scoped.
- Backend/frontend tests, builds, package verification and parity checks pass.
- Permanent runbooks/evidence are complete and temporary plans are retired.
