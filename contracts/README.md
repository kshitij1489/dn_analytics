# Shared sync contracts (analytics ↔ central server)

This directory is one byte-identical copy of the **shared source of truth** for
the desktop ↔ Dachnona central-server wire contract.

## Keep both repos in sync (required)

Whenever **anything** in this shared tree changes — the API contract markdown,
`MUTATION_SYNC_PROTOCOL.md`, fixtures under `fixtures/1/`, or this README — you
**must** update the matching file in the other repository:

| Desktop analytics | Central server (`db.dachnona`) |
|-------------------|---------------------------------|
| `analytics/contracts/` | `db.dachnona/contracts/desktop_analytics_app/` |

Absolute twin path (local checkout):

`/Users/kshitijsharma/Documents/projects/analytics/contracts` ↔
`/Users/kshitijsharma/Documents/projects/db.dachnona/contracts/desktop_analytics_app`

**Rule:** treat the two trees as **byte-identical** for shared artifacts (same filenames, same fixture tree, same section numbers). Do not leave a contract change in only one repo.

| Artifact | Analytics path | Central path |
|----------|----------------|--------------|
| API contract | `contracts/central_server_analytics_app_api_contract.md` | `contracts/desktop_analytics_app/central_server_analytics_app_api_contract.md` |
| Mutation protocol | `contracts/MUTATION_SYNC_PROTOCOL.md` | `contracts/desktop_analytics_app/MUTATION_SYNC_PROTOCOL.md` |
| Fixtures v1 | `contracts/fixtures/1/` | `contracts/desktop_analytics_app/fixtures/1/` |

### How to sync

1. Edit **one** side, then copy the changed file(s) into the other repo so both trees match.
2. After a fixture or contract change, run the analytics Python contract tests
   (extraction parity tests load `contracts/fixtures/1/`) and the relevant central tests.
3. Analytics stubs under `docs/` only redirect to `contracts/` — do not maintain
   a second full contract copy there.
4. When you change implementation status (server or desktop), update the **Implementation checklist** below in **both** repos' contract READMEs (or copy this file across).

## Section numbering

Use the numbers in `central_server_analytics_app_api_contract.md` everywhere (menu commits **§19**, catalog **§20**, customer commits **§21**, forecasting **§22**, telemetry **§18**, global menu groups **§25**).

## Extraction parity fixtures

`menu_merge_event_fixtures.json` and `menu_mapping_verification_event_fixtures.json` under `fixtures/1/` pin client/server extraction. Tests and code comments should point at that versioned path.

## Implementation checklist (server vs desktop)

Refresh this table when a contract section ships or a feature branch merges.
Detail lives in the contract header / §5 / §14 and in analytics
`docs/INDEX.md` status snapshot.

| Area | Contract | Central server (`db.dachnona`) | Desktop client (`analytics`) |
|------|----------|--------------------------------|------------------------------|
| Baseline collaboration (customer/menu merge pull, bootstrap, attribution) | §5–15 | Done | Done |
| Assignment / verification streams + assignment snapshot | §17 | Done | Done |
| Analytics telemetry ingest (errors, learning, conversations) | §18 | Done | Done |
| Server-authoritative menu mutation commits (strict mode) | §19 | Done — **live in prod** since 2026-07-07 | Done — **live in prod** since 2026-07-07 |
| Normalized catalog snapshot | §20 | Done | Done |
| Server-authoritative customer mutation commits (strict mode) | §21 | Done — **live in prod** since 2026-07-07 | Done — **live in prod** since 2026-07-07 |
| Central forecasting sync (status / central-bootstrap / delta / chart payloads) | §22 | **Partial** — revenue publish live; item/volume gated by the 95% assignment-coverage threshold, which a restaurant reaches once it has assignment state (there is no locator import: locators are materialized centrally from current order-line facts); `forecasts/weather` (§22.8) **not yet implemented**, weather rows ship inline on bootstrap and delta | Done — pull-only via `forecast_sync` + a profile-local `central_forecast_*` cache. Empty-cursor pulls use `forecasts/central-bootstrap`; the removed `forecasts/bootstrap` alias has no caller |
| Restaurant selection + raw `/analytics/` streams | §23 | Done — **live in prod** since 2026-08-08. `X-Restaurant-ID` required on every scoped route, no default, no query-parameter alternative | Done (desktop Phase 1, 2026-08-08) — one bound SQLite profile per restaurant plus `analytics-control.db`; every scoped request carries the selected `X-Restaurant-ID` and fails closed without it; `GET /analytics/restaurants/` is the only selector source; **All Stores is a desktop-local read-only federation (2026-08-09) that never appears on the wire** |
| Retired wire surface (`retired_parameter`, `retired_field`, `restaurant_selector_not_a_parameter`, removed alias and keys) | §24 | Done — refused, not ignored; each code pinned by a fixture | Done — no `scope_key` body/query, no `legacy=1`, no retired assignment locator metadata, no `strict_mode_enabled` reader, no `order_pk`/`order_item_pk` parsing; each code pinned by analytics `tests/test_analytics_stream_contract_v12.py` |
| Global menu groups (shared canonical menu across restaurants that share a menu) | §25 | Central revision 1.6 implemented and tested — atomic reviewed bootstrap, versioned snapshot/tail/assignment wire, complete semantic deltas, narrow shadow resolution, preview + strict commit/undo, capability ladder and separate editor permission. `group-1` reached **`shadow`** on 2026-08-11 via the authority cutover; no group has advanced past it | Revision-1.6 client complete and tested, including shadow/aggregating canonical create + locator-map coverage repair and coverage-gated All Stores aggregation. **Dormant until the operator rollout** |
| Shared-Petpooja group POS catalog (identical locator ids in every outlet) | §25.9 | Revision-1.7 implemented and tested — private catalog observation, exact cross-store reconciliation, group-scoped POS rules with canonical price, unified history. **`global_menu_shared_pos_catalog_v1` is advertised on no group**, and Group 1 does not satisfy its identical-locator invariant | Revision-1.7 client built. Dormant; not applicable to Group 1 |
| Group-owned POS aliases (different outlet locators, one canonical group menu) | §9, §25.12 | Revision-1.8 implemented and tested: migrations `0030`/`0031`, private observations, authority-price candidates, audited decisions, one-shot reconciliation, quarantine and lifecycle gates. Dormant pending coordinated deployment and policy enablement | Revision-1.8 observation shipping, central transport, operator UI, projection and tests implemented. Dormant pending coordinated deployment |
| Mutation / revision protocol semantics | `MUTATION_SYNC_PROTOCOL.md` | Done (with §19 / §21) | Done (with §19 / §21) |
| Golden fixtures v1 | `fixtures/1/` | Mirrored under `contracts/desktop_analytics_app/fixtures/1/` | Mirrored under `contracts/fixtures/1/` |

**Legend:** Done = wire + behavior shipped; Partial = some endpoints or families still open (see contract §5.8 / forecasting plan); Not started = not yet built against revision 1.2, which is the only supported client contract.

**Deployment state (2026-08-12):** Group 1 is in `shadow` on the revision-1.6 authority catalog and advertises no POS-policy capability. Revision 1.8 is implemented in both repositories and ready for a coordinated dormant deployment; do not enable `GLOBAL_MENU_GROUP_POS_ALIAS_GROUPS` until both deployed revisions are confirmed and fresh observations/backups are captured.

**When updating the contract:** (1) edit shared markdown/fixtures, (2) copy to the twin path above, (3) update this checklist if server or desktop status changed, (4) note the change in analytics `docs/INDEX.md` status snapshot if the feature is user-visible.
