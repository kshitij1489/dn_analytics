# Dachnona Cloud Sync API Contract — Version 1

**Contract revision:** 1.10
**Contract version:** 1 (fixture directory: `contracts/desktop_analytics_app/fixtures/1/`)
**Audience:** Dachnona backend engineers / Codex agent implementing or maintaining the cloud-side sync work
**Status:** **Revisions through 1.10 are implemented** in the Dachnona central repository (`backend/desktop_analytics_app_sync/`) and Analytics client. The revision-1.7 shared-Petpooja catalog policy and the revision-1.8 group-owned POS alias policy are **removed** (§25.12); neither was ever advertised on a production group, so nothing carries an adapter for either. Baseline collaboration sync (Sections 5–15), assignment/verification sync (**Section 17**), analytics telemetry ingest (**Section 18**), server-authoritative menu commits (**Section 19**), normalized catalog (**Section 20**), and server-authoritative customer commits (**Section 21**) are implemented. Sync is **strict-only as of 2026-07-09**: the central server is the sole source of truth and the legacy batched ingest endpoints (`POST …/customer-merges/ingest`, `…/menu-merges/ingest`, `…/menu-mapping-verifications/ingest`) have been **removed** — interactive/client mutations land exclusively through the server-authoritative commit endpoints (§19, §21), while the operator-only baseline import remains available for cutover/recovery. Strict mode had been live in prod for both scopes since 2026-07-07 (the removed endpoints previously returned HTTP 426 when the scope flag was set); the per-scope `strict_mode_enabled` gate column and its `set_menu_strict_mode`/`set_customer_strict_mode` commands are gone. The `strict_mode_enabled` response key was **removed in revision 1.2**: it existed only so already-deployed clients kept committing, and its stated removal condition — every install running the always-strict release — was confirmed before this build shipped. **Section 22 is implemented except the §22.8 standalone weather read** (`GET …/forecasts/weather`), which is still unrouted; weather rows ship inline as `weather_rows` on §22.6 bootstrap and §22.7 delta. **Section 23 (restaurant selection and the raw analytics streams) is implemented** as of revision 1.1, and is a **breaking change**: `X-Restaurant-ID` is required on every scoped route with no default and no query-parameter alternative, the four `/analytics/` streams filter on the restaurant that owns each row's current parent header, the child streams drop the ambiguous `order_pk`/`order_item_pk` fields in favour of `event_id` plus order-line identity, and `GET /analytics/restaurants/` is the desktop's profile selector source. A revision-1.0 client does not work against 1.1; there is no compatibility mode. **Revision 1.2 removes every remaining backward-compatibility affordance** — the `strict_mode_enabled` response key, the client-supplied `order_line_key` / `petpooja_order_id` / `petpooja_itemid` on assignment writes (now `400`), the inert `legacy=1` / `scope_key` query parameters (now `400`), the `forecasts/bootstrap` route alias (now `404`), and the collapsed-`menu_item_id` filter that shaped the assignment snapshot around one desktop release's local catalog. The full list is **§24**. The central server carries no backward compatibility at all: a 1.1 client does not work against 1.2, and that is the intended state — the desktop is rebuilt against this revision, not accommodated by it. Forecasting is server-authored and downstream-only: the central nightly job (`backend/forecasting/`) trains and publishes, and clients pull via `forecasts/status` / `forecasts/delta` / `forecasts/central-bootstrap`. Desktop clients never upload forecasts. **Revisions 1.1 and 1.2 are live on the central server as of 2026-08-08**, the two-restaurant cutover that made `X-Restaurant-ID` mandatory; desktop client traffic stays blocked at the edge until the client is rebuilt against this revision. **Revision 1.3 introduced Section 25. Revision 1.4 froze its production wire**: versioned snapshot/event/assignment envelopes, one `after` / `next_cursor` vocabulary, complete semantic event deltas and group validation on assignment pulls. **Revision 1.5 closed the first set of gaps a pre-activation review found, and revision 1.6 closed the remaining undo, lifecycle, event-scope and backfill gaps. Revisions 1.7 and 1.8 added two POS-ownership policies — a shared-Petpooja catalog and group-owned POS aliases — and revision 1.9 removes both** (§25.12).
**Revision 1.9 is breaking, and it is a removal.** Neither POS policy was ever advertised on a production group, and neither survives: every `pos_item` / `pos_addon` rule is restaurant-qualified by database check, mapping rules carry no price, and the alias review queue, the alias/shared-POS reconciliations and their bootstrap observation channels are gone. The lifecycle ladder goes with them — `provisioning`, `shadow` and `aggregating` no longer exist, `manage.py set_menu_group_status` and `manage.py backfill_global_menu` are removed, and a configured member of a group that owns a canonical catalog advertises all four global-menu capabilities at once. **Enrolment is membership plus ordinary sync**: a joining restaurant's unmapped rows arrive globally unlinked, stay store-qualified, and a human resolves them through the ordinary `global_locator.map` mutation. Coverage is a diagnostic and gates nothing. The full removal list is **§25.12**.
**Revision 1.10 is breaking.** Display-name aliases are no longer mapping authority, `locator_type: "alias"` is refused, itemcode rules are human-confirmed group-wide parent links with an empty `global_variant_id`, and coverage counts require complete item/variant identity. Migrations `0033` and `0034` archive every changed row and append semantic rule updates or tombstones so clients already carrying legacy rules converge. See §25.13.
**Scope:** Full `desktop_analytics_app_sync` wire contract: collaboration replay streams (Sections 5–15, 17), menu bootstrap ingest/pull, analytics telemetry ingest (Section 18), server-authoritative menu commits (Section 19), normalized catalog (**Section 20**), server-authoritative customer commits (Section 21), downstream-only central forecasting sync (Section 22), restaurant selection plus the raw `/analytics/` streams (Section 23), and global menu groups (Section 25).
**Base path:** All sync routes are mounted at `/desktop-analytics-sync/` (see `config/urls.py`). Trailing slashes are optional on every route. **Exception:** the raw analytics streams and the allowed-restaurants endpoint (**Section 23**) are mounted at `/analytics/` and authenticated with `X-API-Key`, not the sync Bearer token.

## 1. Purpose

This contract documents the full **`desktop_analytics_app_sync`** Django app: collaboration replay streams, menu bootstrap, assignment ground truth, and analytics telemetry ingest.

The analytics client already implements:

- customer merge push
- customer merge pull/apply
- device/install attribution on merge payloads
- menu bootstrap latest pull/apply
- menu merge push/pull

**Baseline (Section 5):** The endpoints and persistence described in **Section 5** and the detailed sections through **Section 15** are **already implemented** on the Dachnona central server (ingest + cursor pull for customer/menu merges, menu-bootstrap latest, attribution persistence). Treat that work as **complete** for collaboration sync.

**Single source of truth (now Section 17, implemented):** The verification-replay and richer-snapshot needs are **built and live** on the central server, using the assignment-applier design documented in the analytics repo `docs/MENU_SYNC_ARCHITECTURE.md`. See **Section 17** for the as-built contract (server-ordered `server_seq`, commit/import event persistence, the pull-only mapping-verification stream, the materialized `order_item_assignments` ground truth, and the assignment snapshot endpoint).

This document is intentionally **additive**, not a rewrite request. The backend Codex agent should:

- preserve existing auth, tenant scoping, middleware, error handling, and route grouping patterns already used by the Dachnona backend
- preserve already-working sync endpoints and tables
- add or extend only what is required for the missing merge/bootstrap contracts below
- prefer adapting current serializers/models/controllers over introducing a parallel sync subsystem

If an endpoint below already exists in the backend, keep the existing route and implementation style, and only make it compatible with the required request/response shape.

## 2. Source Of Truth

This contract is derived from the current analytics client implementation and the as-built Dachnona server (`backend/desktop_analytics_app_sync/`). Client sources (analytics repo):

- [src/core/customer_merge_sync.py](../src/core/customer_merge_sync.py)
- [src/core/customer_merge_sync_events.py](../src/core/customer_merge_sync_events.py)
- [src/core/menu_merge_sync.py](../src/core/menu_merge_sync.py)
- [src/core/menu_merge_sync_events.py](../src/core/menu_merge_sync_events.py)
- [src/core/menu_bootstrap_shipper.py](../src/core/menu_bootstrap_shipper.py)
- [src/core/menu_bootstrap_sync.py](../src/core/menu_bootstrap_sync.py)
- [tests/test_customer_merge_sync.py](../tests/test_customer_merge_sync.py)
- [tests/test_customer_merge_pull.py](../tests/test_customer_merge_pull.py)
- [tests/test_menu_merge_sync.py](../tests/test_menu_merge_sync.py)
- [tests/test_menu_bootstrap_pull.py](../tests/test_menu_bootstrap_pull.py)

Server implementation and tests: `backend/desktop_analytics_app_sync/` (views, services, `tests.py`).

**Contract Fixtures (version 1.0):**
The full suite of Request/Response payloads and extraction schemas is pinned in the shared fixture tree (`contracts/fixtures/1/` in `analytics`, `contracts/desktop_analytics_app/fixtures/1/` in `db.dachnona`):
- **Customer Sync:** `customer_merge_sync_fixtures.json` (ingest/pull), `customer_merge_event_fixtures.json` (event details), `customer_mutations_fixtures.json`
- **Menu Sync:** `menu_merge_sync_fixtures.json` (ingest/pull), `menu_merge_event_fixtures.json` (event details), `menu_mutations_fixtures.json`
- **Verification Sync:** `menu_mapping_verification_sync_fixtures.json` (ingest/pull), `menu_mapping_verification_event_fixtures.json` (event details)
- **Menu Bootstraps & Snapshots:** `menu_bootstrap_fixtures.json`, `menu_assignments_snapshot_fixtures.json`, `menu_catalog_snapshot_fixtures.json`
- **Telemetry & Forecasts:** `telemetry_fixtures.json`, `forecast_sync_fixtures.json`
- **Error Responses:** `error_response_fixtures.json` (common error/edge-case payloads across all endpoints)

## 3. Non-Goals

This change should **not**:

- rename or replace already-live Dachnona sync endpoints unrelated to merge/bootstrap sync
- force a new auth model
- require the desktop client to send new mandatory query params
- depend on local SQLite IDs from the desktop client
- normalize away the raw payloads such that the cloud can no longer replay the same event back to another device

## 4. Shared Rules

### 4.1 Auth

- **Setting:** `SYNC_API_KEY` in Django settings (env var `SYNC_API_KEY`). Implemented in `desktop_analytics_app_sync/auth.py`.
- **Header:** `Authorization: Bearer <SYNC_API_KEY>` when the key is configured.
- **Dev/test:** When `SYNC_API_KEY` is unset (`None`), auth is **not enforced** on any sync route.
- **Failures:** Missing/invalid Bearer → HTTP `401` with `{"error":"Missing or invalid Authorization header"}` or `{"error":"Invalid API key"}`.
- **Not used here:** `SYNC_READ_API_KEY` / `SYNC_INGEST_API_KEY` (those apply to `ai_store`, not this app). There is no `Idempotency-Key` header; dedupe keys are in request bodies (see §4.3).
- Do not make new headers mandatory unless the client is updated separately.

### 4.2 Restaurant selection and scope keys

- All replay-event storage and pull queries are scoped by a `scope_key` string (model column, max 64 chars).
- **Clients select a restaurant, never a scope key.** Send `X-Restaurant-ID`; the server maps it to the internal `scope_key` through the settings registry. `scope_key` is storage partitioning — a client that could name one could read another restaurant's menu, assignment and forecast state. Full selector rules, error codes and normalization: **§23.2**.
- **The header is required.** There is no default restaurant and no query-parameter alternative: a scoped request without `X-Restaurant-ID` is `400`. The historically original restaurant keeps the stored `scope_key = "default"`, but current code derives that value only from its explicitly selected registry entry; no runtime constant or model default selects it.
- The sync Bearer token's grants come from `SYNC_RESTAURANT_GRANTS`, which is **required**: an unset or malformed list authorizes nothing and every scoped request is refused. Grant restaurants there **and** in `ANALYTICS_PRINCIPAL_GRANTS` together — the desktop lists its profiles with the analytics credential (§23.6) and syncs with the Bearer token.
- `?scope_key=` is not read anywhere. Clients must not send it.
- **A second scope exists for menu groups (§25).** One `X-Restaurant-ID` resolves to *both* the restaurant scope above and, when that restaurant is configured into a menu group, the group scope that owns canonical menu identity. They are never substituted for one another: sharing a menu never shares orders, customers, forecasts, prices or availability, and every POS locator stays restaurant-owned (§25.4). Clients never name a menu group either — the group is not an authorization boundary, the restaurant is.
- Error/learning telemetry rows are **not** scope-keyed; they are keyed by uploader and composite natural keys instead, and those endpoints ignore the selector (§23.3). Server-authored central forecasting rows in §22 are scope-keyed and downstream-only. Desktop clients never upload forecast rows.

### 4.3 Idempotency

- Ingest endpoints must be safe to retry. There is **no** `Idempotency-Key` header.
- **Replay event streams** (customer merges, menu merges, mapping verifications): dedupe by `(scope_key, remote_event_id)`. Re-uploads increment `duplicate_count` and appear in `accepted`; HTTP `200`.
- **Errors:** dedupe by `record_id` (insert-or-ignore; skips blank `record_id` silently).
- **Learning:** `ai_logs` upsert by `query_id`; `ai_call_trace` replaces rows for each uploaded `query_id` before inserting the provided per-step rows; `ai_feedback` by `(feedback_id, query_id)`; `llm_cache_feedback` by `(key_hash, call_id)`; tier3 batches always append.
- **Conversations:** upsert by `conversation_id` and `(conversation_id, message_id)`.
- **Menu bootstrap:** append ingest log; latest row merges forward (§9).
- **Forecasts:** nothing to dedupe on ingest — desktop clients never upload forecasts. Server-authored rows are read through the §22 bootstrap/delta endpoints.

### 4.4 Cursor Semantics (replay event streams)

Applies to `GET .../customer-merges`, `GET .../menu-merges`, and `GET .../menu-mapping-verifications`. Full detail in **§17.1**.

- Query params: optional `cursor`, optional `limit` (default **100**, max **500**).
- **Replay order is server ingestion order only: `(ingested_at, id)`.** Client `occurred_at` is display metadata and is **not** used for ordering.
- Cursors are opaque **v2** tokens: base64url of `{"v":2,"ingested_at":<iso-8601>,"id":<int>}`. Each stream has its own cursor space.
- Response includes `events` and `next_cursor` (last row in the page, or echoes the input cursor when the page is empty).
- Invalid tokens → HTTP `400` with `{"error":"Invalid cursor."}`. v1 (or other unsupported version) → HTTP `400` with `{"error":"Unsupported cursor version."}`.
- Every event in a delta response also includes injected `server_seq` (= row `id`) and `server_ingested_at` (ISO-8601).

### 4.5 Raw Payload Preservation

- Persist the raw incoming JSON event payload.
- It is fine to also project searchable fields into columns.
- The pull endpoints should return payloads that remain semantically equivalent to what was ingested.
- Do not rebuild payloads from scratch if that risks dropping fields.

### 4.6 Forward Compatibility

- Ignore unknown top-level keys on lenient serializers (`LenientSerializer` strips unknown fields).
- Treat `schema_version` as informational and persist it.
- `uploaded_by`, `uploaded_from`, `attribution.employee`, and `attribution.device` may be missing or partially populated on some rows.

### 4.7 HTTP status codes (common)

| Code | When |
|------|------|
| `200` | Success (including idempotent mutation replay and empty pull pages) |
| `400` | Envelope/serializer validation failure; invalid cursor on pull; batched menu mutation commit |
| `400` | Restaurant selector missing or invalid (§23.2) |
| `401` | Bearer auth required but missing/invalid (`SYNC_API_KEY` set) |
| `403` | Restaurant selector not granted to this credential (§23.2); invalid `X-API-Key` on `/analytics/` routes (§23.4) |
| `409` | Stale `expected_menu_revision` on menu mutation commit (§19.6) — non-destructive |
| `409` | Stale `expected_customer_revision` on customer mutation commit (§21.6) — non-destructive |
| `422` | Malformed customer mutation (e.g. undo references unknown `reverts_remote_event_id`) |
| `500` | Unhandled server error → `{"error":"Internal error"}` |

The legacy batched replay-ingest endpoints (`POST .../ingest`) have been removed (strict-only); requests to those paths now return `404`. Commit endpoints validate and persist their accepted event payloads transactionally (§19, §21), while the operator-only baseline import retains internal batch accounting (§17.1).

### 4.8 PII, retention, rate limiting

See `backend/desktop_analytics_app_sync/PII_AND_RETENTION.md`. Ingest may contain sensitive fields (`user_query`, `message`, `content`, `context`, etc.). Server-side retention purge is **not** implemented yet. Optional throttling: `desktop_analytics_app_sync.throttling.SyncIngestThrottle` (not enabled by default).

## 5. Implemented API surface

All paths in §5.1–§5.8 are relative to `/desktop-analytics-sync/`, and each is registered **with and without** a trailing slash. §5.9 lists the `/analytics/` routes, which require the trailing slash.

### 5.1 Collaboration replay streams

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `customer-merges` | Pull customer merge deltas |
| `GET` | `menu-merges` | Pull menu merge deltas |
| `GET` | `menu-mapping-verifications` | Pull mapping verification deltas |
| `GET` | `menu-assignments/snapshot` | Paged materialized assignment snapshot (§17.6) |

> **Pull-only.** These streams have no push counterpart; new events land through the commit endpoints (§19, §21). Unrouted paths are listed in §24.

### 5.2 Menu bootstrap

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `menu-bootstrap/ingest` | Upload bootstrap snapshot |
| `GET` | `menu-bootstrap/latest` | Read latest merged bootstrap |

### 5.3 Analytics telemetry ingest

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `errors/ingest` | Error/crash logs |
| `POST` | `learning/ingest` | AI logs, per-step call traces, feedback, tier3, LLM cache feedback |
| `POST` | `conversations/sync` | Conversation + message upsert |
| `GET` | `forecasts/central-bootstrap` | Server-authored forecast bootstrap (§22.6). `forecasts/bootstrap` and `POST forecasts/ingest` are unrouted — see §24 |

### 5.4 Baseline collaboration capabilities (summary)

The desktop client relies on the following, all **implemented** (strict-only: the merge/verification streams are pull-only; writes go through the commit endpoints in §19/§21):

- `GET /desktop-analytics-sync/customer-merges` — cursor pull with v2 cursors and `server_seq` injection
- customer merge rows persist and surface `device_id` / `install_id` attribution
- `POST` + `GET /desktop-analytics-sync/menu-bootstrap/...` — bootstrap ingest and latest pull
- `GET` menu merge and mapping-verification streams — same replay engine as customer merges
- `GET /desktop-analytics-sync/menu-assignments/snapshot` — materialized ground truth + dual watermarks (§17)

### 5.5 Server-authoritative menu mutations (implemented — §19)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `menu-mutations/commit` | Single human menu mutation commit (§19.4) |
| `GET` | `menu-mutations/{mutation_id}` | Idempotency / timeout reconcile (§19.8) |

### 5.6 Server-authoritative customer mutations (implemented — §21)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `customer-mutations/commit` | Single human customer merge/undo commit (§21.4) |
| `GET` | `customer-mutations/{mutation_id}` | Idempotency / timeout reconcile (§21.8) |

### 5.7 Normalized catalog snapshot (optional Phase C — implemented — §20)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `menu-catalog-snapshot/latest` | Normalized catalog checkpoint (§20.3) |

### 5.8 Central forecasting sync (§22 — implemented except `forecasts/weather`)

| Method | Path | Purpose | Status |
|--------|------|---------|---------|
| `GET` | `forecasts/status` | Latest server-authored forecast run metadata | Implemented |
| `GET` | `forecasts/central-bootstrap` | Full current server-authored forecast cache for empty/reset desktop cache (§22.6) | Implemented |
| `GET` | `forecasts/bootstrap` | ~~Route alias for `forecasts/central-bootstrap`~~ | **Removed** in revision 1.2 — `404` (§24) |
| `GET` | `forecasts/delta` | Cursor-based forecast rows for Sync DB | Implemented |
| `GET` | `forecasts/revenue` | Revenue chart payload for latest or selected generated date (§22.9) | Implemented |
| `GET` | `forecasts/items` | Item-demand chart payload (§22.9) | Implemented |
| `GET` | `forecasts/volume` | Volume-demand chart payload (§22.9) | Implemented |
| `GET` | `forecasts/weather` | Weather rows used by central forecasts | **NOT YET IMPLEMENTED** |

### 5.9 Raw analytics streams and restaurant selection (§23 — implemented)

Mounted at `/analytics/`, **not** `/desktop-analytics-sync/`, and authenticated with `X-API-Key` rather than the sync Bearer token. Documented here because the desktop consumes them with the same restaurant profile it syncs with.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/analytics/orders/` | Order headers for the selected restaurant (§23.4) |
| `GET` | `/analytics/order-items/` | Order lines, filtered through their current parent header |
| `GET` | `/analytics/addons/` | Addon lines, filtered through their current parent header |
| `GET` | `/analytics/discounts/` | Discount lines, filtered through their current parent header |
| `GET` | `/analytics/restaurants/` | Restaurants this credential may select — the desktop profile selector source (§23.6) |

### 5.10 Global menu groups (§25 — revision 1.10)

Restaurants that share a business menu belong to one server-managed menu group. Membership comes from the settings restaurant registry, never from a client. Every route below still sends `X-Restaurant-ID`; the server derives the group.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `global-menu/snapshot` | Canonical catalog, redirects or mapping rules, paged by `section` (§25.5) |
| `GET` | `global-menu/events` | Ordered semantic event tail from a client watermark (§25.6) |
| `GET` | `global-menu/status` | Capabilities, catalog counts and per-restaurant coverage (§25.7) |
| `GET` | `global-menu/history` | Unified legacy plus global human audit timeline (§25.10) |
| `POST` | `global-menu/mutations/preview` | Group-wide impact of one mutation, writing nothing (§25.8.1) |
| `POST` | `global-menu/mutations/commit` | Strict global mutation commit (§25.8.2) |
| `GET` | `global-menu/mutations/{mutation_id}` | Replay one accepted mutation after a POST timeout (§25.8.3) |

The two `mutations/` POST routes additionally require the `X-Global-Menu-Key` editor credential (§25.3); the reads do not. The revision-1.7 shared-Petpooja policy and the revision-1.8 alias-review routes are **removed** — see §25.12 for what happened to each name.

## 6. Recommended Persistence Shape

Use the backend's current ORM/schema style. Do not treat the names below as mandatory. They are the minimum data that must survive storage.

### 6.1 Customer Merge Event Storage

Minimum persisted fields:

- tenant/store scope key
- `remote_event_id` unique within scope
- `event_type`
- `schema_version`
- `occurred_at`
- `reverts_remote_event_id` nullable
- `payload_json`
- `uploaded_by_json` nullable
- `uploaded_from_json` nullable
- `employee_id` nullable
- `employee_name` nullable
- `device_id` nullable
- `install_id` nullable
- `device_label` nullable
- `ingested_at`

### 6.2 Menu Merge Event Storage

Minimum persisted fields:

- tenant/store scope key
- `remote_event_id` unique within scope
- `event_type`
- `schema_version`
- `occurred_at`
- `reverts_remote_event_id` nullable
- `payload_json`
- `uploaded_by_json` nullable
- `uploaded_from_json` nullable
- `employee_id` nullable
- `employee_name` nullable
- `device_id` nullable
- `install_id` nullable
- `device_label` nullable
- `ingested_at`

### 6.3 Menu Bootstrap Snapshot Storage

If the backend already persists menu bootstrap uploads, it is enough to ensure the latest snapshot can be served back in the response shape described below.

Minimum persisted fields:

- tenant/store scope key
- `id_maps` JSON
- `cluster_state` JSON
- `uploaded_by_json` nullable
- `uploaded_from_json` nullable
- `device_id` nullable
- `install_id` nullable
- `created_at` / `updated_at`

## 7. Customer Merge Event Shape

Customer merges land through the server-authoritative commit endpoint (§21), which persists the event shape defined here. The pull side is `GET /customer-merges` (§8).

### 7.1 Persistence contract

`POST /customer-mutations/commit` (§21.4) is the only writer.

### 7.2 Storage Rules

- each event is stored idempotently by `(scope_key, remote_event_id)`
- naive `occurred_at` strings (no timezone) are stored as **UTC**
- the backend preserves the full event payload, including `local_refs`, even though cloud logic must not depend on local SQLite IDs

### 7.3 Applied Event Example

This is the singular `event` object inside the current customer mutation commit envelope (§21.4), not a standalone or batched request body.

```json
{
  "remote_event_id": "4f3198a5f96f4750b8f2dcdab0e2d89f",
  "schema_version": 1,
  "event_type": "customer_merge.applied",
  "occurred_at": "2024-02-05 12:00:00",
  "attribution": {
    "employee": {
      "employee_id": "0001",
      "name": "Owner"
    },
    "device": {
      "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
      "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
      "device_label": "MacBook-Pro",
      "platform": "Darwin",
      "platform_release": "24.5.0",
      "machine": "arm64"
    }
  },
  "source_customer": {
    "snapshot": {
      "name": "Rahul Sharma",
      "phone": "9999999999",
      "address": "HSR Layout",
      "gstin": null,
      "total_orders": 1,
      "total_spent": 80.0,
      "last_order_date": "2024-02-03 10:00:00",
      "is_verified": false
    },
    "portable_locators": {
      "customer_identity_key": "phone:source",
      "phone_hash": "8c1f1046219ddd216a023f792356ddf127fce372a8d304f8115b01f9501ef7c3",
      "name_address_hash": "7cb95a5f8f495f7b4454d7b2207c1c12ab57f2e1d1d5656e2e6ae4d3e15cf287",
      "name_normalized": "rahul sharma",
      "address_normalized": "hsr layout",
      "address_book_hashes": [
        "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
      ]
    }
  },
  "target_customer": {
    "snapshot": {
      "name": "Rahul S.",
      "phone": null,
      "address": "HSR Layout",
      "gstin": null,
      "total_orders": 1,
      "total_spent": 120.0,
      "last_order_date": "2024-02-04 10:00:00",
      "is_verified": false
    },
    "portable_locators": {
      "customer_identity_key": "addr:target",
      "phone_hash": null,
      "name_address_hash": "c2e16ebf6f3f5f7dab2db4b74df4c8f3817afef27506f3ccb452fc6d33f34551",
      "name_normalized": "rahul s.",
      "address_normalized": "hsr layout",
      "address_book_hashes": [
        "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
      ]
    }
  },
  "merge_metadata": {
    "similarity_score": 0.98,
    "model_name": "duplicate_matcher_v1",
    "reasons": [
      "phone exact match"
    ],
    "copied_address_count": 0,
    "target_before_fields": {
      "phone": null,
      "address": "HSR Layout",
      "gstin": null,
      "is_verified": false
    },
    "target_is_verified_after_merge": true,
    "mark_target_verified": true
  },
  "moved_orders": {
    "count": 1,
    "portable_refs": [
      {
        "petpooja_order_id": "PP-101",
        "stream_id": 5001,
        "event_id": "evt-101",
        "aggregate_id": "agg-101",
        "created_on": "2024-02-03 10:00:00",
        "total": 80.0,
        "local_order_id": 101
      }
    ]
  },
  "local_refs": {
    "merge_id": 999,
    "source_customer_id": 11,
    "target_customer_id": 22,
    "moved_order_ids": [
      444
    ],
    "inserted_target_address_ids": [],
    "removed_target_address_ids": []
  }
}
```

### 7.4 Undo Event Example

The current client sends undo events with the same `source_customer`, `target_customer`, and `merge_metadata` structures as the applied event. The example below is shortened to emphasize the undo-specific fields. Backend storage and replay should preserve the full payload when those fields are present.

```json
{
  "remote_event_id": "74fc71320f9b4b598c7dcb2f6de716ef",
  "schema_version": 1,
  "event_type": "customer_merge.undone",
  "occurred_at": "2024-02-05 13:00:00",
  "reverts_remote_event_id": "4f3198a5f96f4750b8f2dcdab0e2d89f",
  "attribution": {
    "employee": {
      "employee_id": "0001",
      "name": "Owner"
    },
    "device": {
      "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
      "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
      "device_label": "MacBook-Pro",
      "platform": "Darwin",
      "platform_release": "24.5.0",
      "machine": "arm64"
    }
  },
  "source_customer": {},
  "target_customer": {},
  "merge_metadata": {},
  "undo_metadata": {
    "restored_order_count": 1,
    "restored_target_fields": [
      "address",
      "gstin",
      "is_verified",
      "phone"
    ],
    "original_merged_at": "2024-02-05 12:00:00"
  },
  "moved_orders": {
    "count": 1,
    "portable_refs": []
  },
  "local_refs": {
    "merge_id": 999
  }
}
```

## 8. Customer Merge Delta Pull

### 8.1 Endpoint

`GET /desktop-analytics-sync/customer-merges`

### 8.2 Query Params

- `cursor` optional string
- `limit` optional integer

Recommended defaults:

- default `limit = 100`
- maximum `limit = 500`

### 8.3 Response Requirements

- return events in deterministic **server ingestion** order (`ingested_at`, then `id`)
- include `events` and `next_cursor`
- each event payload is the stored `payload_json` plus injected `server_seq` and `server_ingested_at`
- include `device_id` / `install_id` in returned payloads via the preserved `attribution.device` object (also projected to DB columns)

### 8.4 Canonical Response

```json
{
  "events": [
    {
      "remote_event_id": "4f3198a5f96f4750b8f2dcdab0e2d89f",
      "schema_version": 1,
      "event_type": "customer_merge.applied",
      "occurred_at": "2024-02-05 12:00:00",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_customer": {
        "snapshot": {
          "name": "Rahul Sharma",
          "phone": "9999999999",
          "address": "HSR Layout",
          "gstin": null,
          "total_orders": 1,
          "total_spent": 80.0,
          "last_order_date": "2024-02-03 10:00:00",
          "is_verified": false
        },
        "portable_locators": {
          "customer_identity_key": "phone:source",
          "phone_hash": "8c1f1046219ddd216a023f792356ddf127fce372a8d304f8115b01f9501ef7c3",
          "name_address_hash": "7cb95a5f8f495f7b4454d7b2207c1c12ab57f2e1d1d5656e2e6ae4d3e15cf287",
          "name_normalized": "rahul sharma",
          "address_normalized": "hsr layout",
          "address_book_hashes": [
            "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
          ]
        }
      },
      "target_customer": {
        "snapshot": {
          "name": "Rahul S.",
          "phone": null,
          "address": "HSR Layout",
          "gstin": null,
          "total_orders": 1,
          "total_spent": 120.0,
          "last_order_date": "2024-02-04 10:00:00",
          "is_verified": false
        },
        "portable_locators": {
          "customer_identity_key": "addr:target",
          "phone_hash": null,
          "name_address_hash": "c2e16ebf6f3f5f7dab2db4b74df4c8f3817afef27506f3ccb452fc6d33f34551",
          "name_normalized": "rahul s.",
          "address_normalized": "hsr layout",
          "address_book_hashes": [
            "93d0a31ea8c636f0f4096f3c490769f2f255f78e7baf8854b45ea0f59052785e"
          ]
        }
      },
      "merge_metadata": {
        "similarity_score": 0.98,
        "model_name": "duplicate_matcher_v1",
        "reasons": [
          "phone exact match"
        ],
        "copied_address_count": 0,
        "target_before_fields": {
          "phone": null,
          "address": "HSR Layout",
          "gstin": null,
          "is_verified": false
        },
        "target_is_verified_after_merge": true,
        "mark_target_verified": true
      },
      "moved_orders": {
        "count": 1,
        "portable_refs": [
          {
            "petpooja_order_id": "PP-101",
            "stream_id": 5001,
            "event_id": "evt-101",
            "aggregate_id": "agg-101",
            "created_on": "2024-02-03 10:00:00",
            "total": 80.0,
            "local_order_id": 101
          }
        ]
      },
      "local_refs": {
        "merge_id": 999,
        "source_customer_id": 11,
        "target_customer_id": 22,
        "moved_order_ids": [
          444
        ],
        "inserted_target_address_ids": [],
        "removed_target_address_ids": []
      },
      "server_seq": 10,
      "server_ingested_at": "2024-02-05T12:00:02Z"
    }
  ],
  "next_cursor": "customer-merge-cursor-000001",
  "customer_revision": 42
}
```

Each event in the `events` array includes server-injected `server_seq` (int, = row `id`) and `server_ingested_at` (ISO-8601) per §4.4. The top-level `customer_revision` is required per §21.3; `strict_mode_enabled` was removed in revision 1.2.

### 8.5 Compatibility Note

The server returns **`events` + `next_cursor` only** (no `items` / `cursor_after` aliases). Older desktop builds that still accept those alias keys when **reading** responses should prefer `events` / `next_cursor` from this server.

## 9. Menu Bootstrap

### 9.1 Ingest

`POST /desktop-analytics-sync/menu-bootstrap/ingest`

#### Request

- `Authorization: Bearer <token>` when configured
- `Content-Type: application/json`
- **Required:** `id_maps` (object), `cluster_state` (object)
- **Optional:** `uploaded_by`, `uploaded_from`, `snapshot_role`

```json
{
  "id_maps": {
    "menu_id_to_str": {"item_cold_coffee": "Cold Coffee"},
    "variant_id_to_str": {"variant_large": "Large"},
    "type_id_to_str": {"type_beverage": "Beverage"}
  },
  "cluster_state": {
    "item_cold_coffee:type_beverage": {"101": [["101", "variant_large"]]}
  },
  "uploaded_by": {"employee_id": "0001", "name": "Owner"},
  "uploaded_from": {
    "device_id": "device-…",
    "install_id": "install-…",
    "device_label": "MacBook-Pro"
  },
  "snapshot_role": "seed_only"
}
```

#### Server behavior

- Appends a row to `ingest_menu_bootstrap` (audit log) and updates the single `menu_bootstrap_latest` row for the scope.
- **`id_maps` merge forward** per sub-map: incoming keys win; a smaller catalog from a fresh install cannot drop entries other installs still reference.
- **`cluster_state` replace** on the latest row **unless**:
  - `snapshot_role` is `"seed_only"` **and** a latest row already exists → incoming `cluster_state` is ignored (`cluster_state_updated: false`), but `id_maps` still merge forward; or
  - Django setting `MENU_BOOTSTRAP_TRUST_CLIENT_CLUSTER_STATE=false` → same as `seed_only` for cluster state (ops toggle; materialized `order_item_assignments` is ground truth for clustering).
- First-ever push may seed `cluster_state` even when `snapshot_role` is `seed_only` (no prior latest row).
- `shared_pos_catalog` and `group_pos_alias_observation` are **retired** (§25.12). They carried private per-restaurant POS evidence for the review queues those policies needed. Nothing stores or reads them, and an unexpected key is ignored per §4.6 — the ingest is a snapshot push, not an assignment write, so there is no honoured-versus-ignored ambiguity for it to hide.

#### Success response (HTTP `200`)

```json
{"received": 1, "cluster_state_updated": true}
```

`shared_pos_catalog_updated` and `group_pos_alias_observation_updated` are gone with the observations they acknowledged (§25.12).

Validation failure → HTTP `400` (serializer errors).

### 9.2 Latest Pull

`GET /desktop-analytics-sync/menu-bootstrap/latest`

### 9.3 Server Behavior

- return the latest bootstrap snapshot for the authenticated scope
- backed by `menu_bootstrap_latest` (maintained by §9.1 ingest)
- when no row exists yet, HTTP `200` with `{"id_maps":{},"cluster_state":{}}`

### 9.4 Required Response Shape

The client accepts either:

1. top-level `id_maps` and `cluster_state`, or
2. `snapshot.id_maps` and `snapshot.cluster_state`

Optional metadata the client will preserve if present:

- `updated_at`
- `created_at`
- `snapshot_id`
- `cursor`
- `version`

### 9.5 Preferred Response Example

```json
{
  "snapshot_id": "menu-bootstrap-2026-04-14T10:00:00Z",
  "version": 1,
  "updated_at": "2026-04-14T10:00:00Z",
  "id_maps": {
    "menu_id_to_str": {
      "item_cold_coffee": "Cold Coffee"
    },
    "variant_id_to_str": {
      "variant_large": "Large"
    },
    "type_id_to_str": {
      "type_beverage": "Beverage"
    }
  },
  "cluster_state": {
    "item_cold_coffee:type_beverage": {
      "101": [
        [
          "101",
          "variant_large"
        ]
      ]
    }
  },
  "menu_revision": 42
}
```

The top-level `menu_revision` is required per §19.3; `strict_mode_enabled` was removed in revision 1.2.

### 9.6 Compatibility Note

Do not force a new envelope if the backend already returns:

```json
{
  "snapshot": {
    "id_maps": {},
    "cluster_state": {}
  }
}
```

That shape is also accepted by the current client.

## 10. Menu Merge Event Shape

Menu merges land through the server-authoritative commit endpoint (§19) and the `import_menu_assignment_baseline` ops command, both of which persist and materialize the event shape defined here. The pull side is `GET /menu-merges` (§11).

### 10.1 Persistence contract

`POST /menu-mutations/commit` (§19.7) is the only online writer.

### 10.2 Storage Rules

- same attribution and event metadata conventions as the customer merge event
- same idempotency by `(scope_key, remote_event_id)`
- preserve raw payload
- persist attribution including `device_id` / `install_id`

### 10.3 Applied Event Example

This is the singular `event` object inside the current menu mutation commit envelope (§19.4), not a standalone or batched request body.

```json
{
  "remote_event_id": "remote-menu-merge-1",
  "schema_version": 2,
  "event_type": "menu_merge.applied",
  "occurred_at": "2026-04-14T10:00:00Z",
  "attribution": {
    "employee": {
      "employee_id": "0001",
      "name": "Owner"
    },
    "device": {
      "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
      "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
      "device_label": "MacBook-Pro",
      "platform": "Darwin",
      "platform_release": "24.5.0",
      "machine": "arm64"
    }
  },
  "source_item": {
    "menu_item_id": "item_source",
    "name": "Iced Coffee",
    "type": "Beverage",
    "is_verified": true
  },
  "target_item": {
    "menu_item_id": "item_target",
    "name": "Cold Coffee",
    "type": "Beverage",
    "is_verified": true
  },
  "merge_payload": {
    "kind": "basic_merge_v1",
    "assignments": [
      {
        "order_item_id": "order-item-101",
        "menu_item_id": "item_target",
        "variant_id": null,
        "is_verified": 1
      }
    ],
    "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
  },
  "local_refs": {
    "merge_id": 123
  }
}
```

### 10.4 Supported `merge_payload.kind` Values

The backend persists and replays the payload faithfully and materializes its explicit assignments and catalog effects; it does not reproduce the desktop's merge-decision logic.

Supported kinds emitted/consumed by the current client:

- `basic_merge_v1`
- `variant_merge_v1`
- `resolution_variant_v1`
- `order_item_remap_v1`
- `derived_assignment_v1`

#### `variant_merge_v1` example

```json
{
  "kind": "variant_merge_v1",
  "variant_mappings": [
    {
      "source_variant_id": "variant_small",
      "source_variant_name": "Small",
      "target_variant_id": "variant_large",
      "target_variant_name": "Large"
    }
  ],
  "history_payload": {
    "kind": "variant_merge_v1"
  },
  "operation_signature": "6f2f4f2bfde0dce3ab670301a5ebecfe95d59cb6d4a0f8d8182424efc3021cef"
}
```

#### `resolution_variant_v1` example

```json
{
  "kind": "resolution_variant_v1",
  "resolution": {
    "source_variant_id": "variant_small",
    "source_variant_name": "Small",
    "target_variant_id": "variant_large",
    "target_variant_name": "Large"
  },
  "history_payload": {
    "kind": "resolution_variant_v1"
  },
  "operation_signature": "f7a7d7cc59b118baf2da5d9d22f0af0d2d95f503b2bbcc43ed47bdadf129e506"
}
```

### 10.5 Undo Event Example

```json
{
  "remote_event_id": "remote-menu-merge-undo-1",
  "schema_version": 1,
  "event_type": "menu_merge.undone",
  "occurred_at": "2026-04-14T10:05:00Z",
  "reverts_remote_event_id": "remote-menu-merge-1",
  "source_item": {
    "menu_item_id": "item_source",
    "name": "Iced Coffee",
    "type": "Beverage",
    "is_verified": true
  },
  "target_item": {
    "menu_item_id": "item_target",
    "name": "Cold Coffee",
    "type": "Beverage",
    "is_verified": true
  },
  "merge_payload": {
    "kind": "basic_merge_v1",
    "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
  },
  "undo_metadata": {
    "original_merged_at": "2026-04-14T10:00:00Z"
  }
}
```

## 11. Menu Merge Delta Pull

### 11.1 Endpoint

`GET /desktop-analytics-sync/menu-merges`

### 11.2 Query Params

- `cursor` optional string
- `limit` optional integer

### 11.3 Response Requirements

- return events in server ingestion order (`ingested_at`, `id`)
- include `events`, `next_cursor`, and per-event `server_seq` / `server_ingested_at`
- return each event payload unchanged enough for faithful client replay
- preserve `reverts_remote_event_id` on undo events

### 11.4 Canonical Response

```json
{
  "events": [
    {
      "remote_event_id": "remote-menu-merge-1",
      "schema_version": 1,
      "event_type": "menu_merge.applied",
      "occurred_at": "2026-04-14T10:00:00Z",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_item": {
        "menu_item_id": "item_source",
        "name": "Iced Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "target_item": {
        "menu_item_id": "item_target",
        "name": "Cold Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "merge_payload": {
        "kind": "basic_merge_v1",
        "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
      },
      "server_seq": 100,
      "server_ingested_at": "2026-04-14T10:00:02Z"
    },
    {
      "remote_event_id": "remote-menu-merge-undo-1",
      "schema_version": 1,
      "event_type": "menu_merge.undone",
      "occurred_at": "2026-04-14T10:05:00Z",
      "reverts_remote_event_id": "remote-menu-merge-1",
      "attribution": {
        "employee": {
          "employee_id": "0001",
          "name": "Owner"
        },
        "device": {
          "device_id": "device-5fd6f0df7d8ef4d7fba2a134",
          "install_id": "install-0b3185b5294f4da1b1d13c39d637f5ec",
          "device_label": "MacBook-Pro",
          "platform": "Darwin",
          "platform_release": "24.5.0",
          "machine": "arm64"
        }
      },
      "source_item": {
        "menu_item_id": "item_source",
        "name": "Iced Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "target_item": {
        "menu_item_id": "item_target",
        "name": "Cold Coffee",
        "type": "Beverage",
        "is_verified": true
      },
      "merge_payload": {
        "kind": "basic_merge_v1",
        "operation_signature": "f7182ce9505bd3f7fbe750179741c181be1f5146142c86cc3d6b7a884fda06fa"
      },
      "undo_metadata": {
        "original_merged_at": "2026-04-14T10:00:00Z"
      },
      "server_seq": 101,
      "server_ingested_at": "2026-04-14T10:05:02Z"
    }
  ],
  "next_cursor": "menu-merge-cursor-000002",
  "menu_revision": 42
}
```

Each event includes server-injected `server_seq` and `server_ingested_at` per §4.4. The top-level `menu_revision` is required per §19.3; `strict_mode_enabled` was removed in revision 1.2.

### 11.5 Compatibility Note

Same as §8.5: server returns `events` + `next_cursor` only.

## 12. Attribution Persistence Requirement

The plan item for attribution should only be marked complete when both of the following are true:

1. `device_id` and `install_id` are persisted server-side for customer merge and menu merge events.
2. those values come back to the client via pull responses inside `attribution.device`, or via the preserved raw payload that contains `attribution.device`.

Minimum fields to preserve:

- `device_id`
- `install_id`
- `device_label`
- `platform`
- `platform_release`
- `machine`
- `employee_id`
- `employee_name`

The backend may additionally expose these in admin views, audit screens, or reporting tables if useful.

## 13. Implementation Notes For The Backend Codex Agent

- Reuse existing Dachnona models/tables if they already cover part of this data.
- If an existing sync/event table already stores JSON payloads, extend it rather than creating a redundant second store.
- If `menu-bootstrap/latest` is already live, do not replace it. Only confirm it emits `id_maps` and `cluster_state` in one of the accepted shapes.
- Do not make the backend depend on `local_refs`. Keep them only for audit/debug payload parity.
- Do not rewrite customer/menu payloads to local server IDs. Keep portable locators and raw payload fidelity.
- Prefer additive migrations:
  - new tables if no equivalent exists
  - new nullable columns if equivalent tables already exist
  - new indexes for `(tenant_scope, remote_event_id)` and pull ordering
- Preserve older rows that may not have attribution fields.

## 14. Completion Checklist

### 14.1 Baseline collaboration sync (complete)

The following items are **done** on the Dachnona central server (aligned with Section 5):

- Dachnona backend customer merge delta endpoint (pull-only)
- Dachnona backend attribution persistence
- Dachnona backend menu bootstrap ingest + latest endpoints
- Dachnona backend menu merge delta endpoint (pull-only)
- Analytics telemetry ingest endpoints (§18)

### 14.2 Assignment / verification sync (implemented — Section 17)

Delivered on the central server via the assignment-applier design in the analytics repo `docs/MENU_SYNC_ARCHITECTURE.md`. Full wire contract in **Section 17**:

- [x] Server-ordered replay: `(ingested_at, id)`, v2 cursors, `server_seq` / `server_ingested_at` injected into every delta event
- [x] Commit/import event validation and `(scope_key, remote_event_id)` dedupe; the public replay streams are pull-only
- [x] Menu merge events carry an explicit `assignments` list (schema v2); v1 derivation pinned by the shared `fixtures/1/menu_merge_event_fixtures.json`
- [x] Mapping verification commit/import write paths + pull; the verification stream is the **sole owner** of `is_verified` (merge only seeds it on insert); shape pinned by the shared `fixtures/1/menu_mapping_verification_event_fixtures.json`
- [x] Materialized `order_item_assignments` ground truth + `rebuild_order_item_assignments` + `import_menu_assignment_baseline`
- [x] `GET /menu-assignments/snapshot` for fresh-install seeding (both merge and verification watermarks + cursors)
- [ ] Optional: server-side compaction / retention policy for high-volume verification events

### 14.3 Server-authoritative menu mutation commits (implemented — Section 19)

Implemented, **strict-only**. Full wire contract is in **Section 19**. Commit is the only online/client menu writer; the operator-only baseline import remains available for cutover/recovery. There is no per-scope activation flag (removed 2026-07-09).

- [x] Migration: `MenuScopeState` + `MenuMutationLog` (`backend/desktop_analytics_app_sync/models.py`)
- [x] Backfill one `MenuScopeState` per scope
- [x] Add `menu_revision` to pull/snapshot responses (§19.3) — the `strict_mode_enabled` key it shipped alongside was removed in revision 1.2
- [x] `POST …/menu-mutations/commit` with §19.7 semantics
- [x] `GET …/menu-mutations/{mutation_id}` (§19.8)
- [x] ~~Legacy ingest gate — HTTP `426` (§19.9)~~ — gate + legacy ingest endpoints **removed 2026-07-09** (strict-only)

### 14.4 Server-authoritative customer mutation commits (implemented — Section 21)

Implemented, **strict-only**. Server Phases 1–2 and the analytics client strict-mode path (Phases 3–5) shipped; Phase 7 validation shipped 2026-07-06 and the prod flip live-validated 2026-07-07. As of 2026-07-09 the per-scope gate and legacy ingest are removed — commit is the only customer writer. Full wire contract in **Section 21**; validation record in analytics `docs/MENU_SYNC_ARCHITECTURE.md` §15.

- [x] Migration: `CustomerScopeState` + `CustomerMutationLog` (`backend/desktop_analytics_app_sync/models.py`)
- [x] Backfill one `CustomerScopeState` per scope
- [x] Add `customer_revision` to `GET …/customer-merges` pull (§21.3) — the `strict_mode_enabled` key it shipped alongside was removed in revision 1.2
- [x] `POST …/customer-mutations/commit` with §21.7 semantics
- [x] `GET …/customer-mutations/{mutation_id}` (§21.8)
- [x] ~~Legacy customer ingest gate — HTTP `426` (§21.9)~~ — gate + legacy ingest endpoint **removed 2026-07-09** (strict-only)
- [x] ~~Client rollout drain helpers — `customer_outbox_drain.py`; API `GET /api/sync/customer-rollout-status`, `POST /api/sync/drain-customer-outbox` (§21.12)~~ — served the rollout, then **removed 2026-07-09** with the legacy layer
- [x] Phase 7 validation — strict-mode scenarios green in analytics `tests/test_customer_strict_mode_validation.py` (client) and `CustomerMutationPhase7ValidationTests` in `backend/desktop_analytics_app_sync/tests.py` (server); see analytics `docs/MENU_SYNC_ARCHITECTURE.md` §15
- [x] Prod deploy server + client release + strict activation per scope (§21.12) — **done 2026-07-07**; strict made unconditional 2026-07-09

## 15. Final Compatibility Summary

For the current analytics client to work without further changes, the backend must satisfy these client expectations:

- customer mutations commit through `POST /customer-mutations/commit` with the singular §21.4 envelope
- menu mutations commit through `POST /menu-mutations/commit` with the singular §19.4 envelope
- customer/menu/mapping-verification pulls accept `cursor` and `limit`, return `events` + `next_cursor` + `server_seq`
- menu bootstrap latest returns `id_maps` and `cluster_state` either at top-level or under `snapshot`
- merge pull responses preserve event payloads, including attribution and undo links
- assignment snapshot + verification stream per §17

Telemetry ingest (§18) is independent of collaboration sync but shares the same Bearer auth.

Anything beyond that may follow existing Dachnona backend conventions.

---

## 17. As-built assignment & verification sync (implemented — freeze target)

This section is the **authoritative, as-built wire contract** for the collaboration-sync redesign in the analytics repo `docs/MENU_SYNC_ARCHITECTURE.md`. It is what the central server runs today and what the desktop client depends on. **Freeze this before handoff.** It reuses **Section 4** (auth, tenant scope, idempotency, raw-payload preservation) unchanged.

### 17.1 Server ordering, cursors, and internal event persistence

Applies to all three replay streams (customer merges, menu merges, mapping verifications) — they share ordering, cursor, validation, and persistence machinery.

- **Replay order is server ingestion order only: `(ingested_at, id)`.** Client `occurred_at` is display metadata and is **not** used for ordering (an offline install's backlog must still reach peers whose cursors have advanced).
- **`server_seq` = the event row's autoincrement `id`.** Every delta event is returned with two injected keys: `"server_seq"` and `"server_ingested_at"` (ISO-8601). These are additive; the client keys conflict resolution on `server_seq`.
- **Cursors are opaque v2 tokens**: base64url of `{"v":2,"ingested_at":<iso>,"id":<int>}`. A v1 (or otherwise unparseable) cursor **must** be rejected with HTTP 400 and a distinct message (the client resets its cursor and re-pulls). Each stream has its **own** cursor and its **own** id space — never compare a `server_seq` from one stream against a row guarded by another.
- **Event persistence is internal.** The public `POST .../ingest` routes do not exist. The commit endpoints (§19, §21) validate their singular mutation envelopes and persist accepted event payloads transactionally. The operator-only `import_menu_assignment_baseline` command may batch events and retains internal accepted/rejected accounting. Every path deduplicates stored events by `(scope_key, remote_event_id)`.

### 17.2 Menu merge events carry explicit `assignments`

Menu merge events (schema v2) include an explicit assignment list so any peer applies them without needing the source cluster to still exist:

```json
"merge_payload": {
  "kind": "resolution_variant_v1",
  "assignments": [
    {"order_item_id": "…", "menu_item_id": "<target>", "variant_id": "<id-or-null>", "is_verified": 1}
  ]
}
```

- A normalized assignment always carries `order_item_id` + `menu_item_id`; `variant_id` / `is_verified` are **omitted** when the event does not specify them; `variant_id: null` is the SQL NULL variant (`__NULL_VARIANT__` normalizes to null).
- **`order_line_key`, `petpooja_order_id` and `petpooja_itemid` are removed from `merge_payload.assignments[]` as of revision 1.2, and an assignment carrying any of the three is rejected with `400` (`code: retired_field`).** They were accepted-and-ignored in 1.1; a field the server silently discards leaves a client believing it wrote a locator that does not exist, so the refusal is the honest behaviour. The other two are the locator's metadata and travelled with the key — a client still sending them is still describing a locator it believes it is writing, so they are refused rather than merely unread. This is the *request* shape only: `petpooja_itemid` on an **assignment snapshot response** (§17.6) is unaffected and still returned.
- Locators are materialized **centrally**, from current order-line facts joined to current assignments, and that is the only source of locator keys: there is no client-supplied path and no operator import command. The central hash leads with the restaurant — SHA-256 of `restID:normalized_orderID:line_index:petpooja_itemid:raw_item_name:quantity:total`, zero-based line index — because two restaurants legitimately issue the same `orderID`, and the pre-cutover formula hashed their identical lines to the same value. An old-format key and a current one are both 64 hex characters, so shape validation could never have told them apart. There is no client-side implementation of the hash; if one is ever added it must pass the central fixtures in `analytics.tests`.
- Legacy v1 events (no `assignments`) are derived from `history_payload` (`mapping_rows`, or `affected_order_item_ids` + target).
- **Extraction parity is pinned by the shared `fixtures/1/menu_merge_event_fixtures.json`, which must be byte-identical in both repos.** Client extractor: `src/core/menu_assignment_apply.extract_assignments`; server extractor: `services/assignment_state.extract_assignments`. Both are tested against the fixtures.

### 17.3 Materialized ground truth (`order_item_assignments`)

The server maintains a per-scope, per-`order_item_id` row `→ (menu_item_id, variant_id, is_verified)` as events ingest:

- **Merge apply** (`apply_menu_merge_event_to_assignments`) upserts guarded by `last_seq < server_seq` (a conditional UPDATE, else INSERT). It writes `menu_item_id` / `variant_id` on every apply, but writes **`is_verified` only on INSERT** (a create-only seed).
- The table is the queryable ground truth and the source for the snapshot endpoint. `rebuild_order_item_assignments` replays the whole log (all merges, then all verifications) to reconstruct it identically; `import_menu_assignment_baseline` publishes a golden install's full state as authoritative v2 events.

### 17.4 `is_verified` has a single owning stream

**Invariant (plan I5):** the mapping-verification stream is the **sole** authority for `is_verified` on an existing row. The merge stream may only *seed* it when it first creates a row; it must never rewrite the flag on UPDATE. This removes any cross-stream ordering hazard — the flag can no longer diverge on merge-vs-verification interleaving.

Consequences the server enforces / relies on:

- The verification apply (`apply_menu_mapping_verification_event_to_assignments`) is guarded by **`last_verification_seq`** (the verification table's own id space), **never** by `last_seq`.
- Every flag-changing desktop operation — resolve, remap, verify/reopen, bulk actions, and undo — emits a verification event. `import_menu_assignment_baseline` therefore emits **both** merge events (mapping) **and** verification events (the golden flag), so a cutover converges `is_verified` on already-materialized rows.
- Snapshot bootstrap / force-reseed adopt the flag from the full-state snapshot; that is the one non-verification path that sets the flag, and it reads the server's already-materialized value.

### 17.5 Mapping verification stream

- **Write path**: verification events land through the menu commit endpoint (§19, `verify` mutation type) and the `import_menu_assignment_baseline` ops command, both persisting with the per-event dedupe/materialization this section describes.
- **Pull**: `GET /desktop-analytics-sync/menu-mapping-verifications` — `cursor`, `limit`; returns `{"events":[...],"next_cursor":...}` with `server_seq` injected (§17.1).
- **Event shapes** (top-level fields, `schema_version: 1`):
  - `mapping.verified` — `order_item_id`, `menu_item_id`, `variant_id`, `is_verified` (default 1).
  - `mapping.reopened` — `order_item_id`, `is_verified` (default 0).
  - `mapping.bulk_verified` — `mappings: [{order_item_id, menu_item_id, variant_id, is_verified}, ...]` (each default 1).

**Canonical Pull Response Example:**
```json
{
  "events": [
    {
      "remote_event_id": "vfx-verified-1",
      "schema_version": 1,
      "event_type": "mapping.verified",
      "occurred_at": "2026-06-01 10:00:00",
      "order_item_id": "101",
      "menu_item_id": "item_a",
      "variant_id": "variant_x",
      "is_verified": 1,
      "server_seq": 100,
      "server_ingested_at": "2026-06-01T10:00:02Z",
      "attribution": {
        "employee": { "employee_id": "0001", "name": "Owner" },
        "device": { "device_id": "device-123", "install_id": "install-456" }
      }
    }
  ],
  "next_cursor": "verification-cursor-0001",
  "menu_revision": 42
}
```

- The normalization from an event to its `(order_item_id, is_verified)` rows is **pinned by the shared `fixtures/1/menu_mapping_verification_event_fixtures.json`** (byte-identical in both repos). Client helper: `src/core/menu_mapping_verification_sync_events.extract_verification_entries`; server helper: `services/assignment_state.extract_verification_entries`. Verification apply is flag-only — it never rewrites `menu_item_id` / `variant_id`.

### 17.6 Assignment snapshot endpoint (fresh-install seeding)

`GET /desktop-analytics-sync/menu-assignments/snapshot` — auth per §4.1.

- **Query params**: `limit` (bounded), `after` = last `order_item_id` of the previous page.
- **Response**:

  ```json
  {
    "schema_version": 1,
    "menu_group_id": "group-1",
    "menu_group_revision": 7,
    "assignments": [
      {"order_item_id":"…","menu_item_id":"…","variant_id":null,
       "is_verified":1,"last_seq":123,"last_verification_seq":45,"last_event_id":"…",
       "petpooja_itemid":"…","petpooja_itemcode":"…",
       "global_menu_item_id":"…","global_variant_id":"…"}
    ],
    "watermark_seq": 123, "watermark_cursor": "<v2 merge cursor>",
    "verification_watermark_seq": 45, "verification_watermark_cursor": "<v2 verification cursor>",
    "next_page": "<order_item_id or null>"
  }
  ```

- **`order_line_key` and `petpooja_order_id` are not in this snapshot.** One `order_item_id` spans every line that sold that menu item, so neither has a single value for a row. `petpooja_itemid` is included when materialized. The provider's distinct `petpooja_itemcode` is included only when every observed line for that POS item agrees; ambiguous line evidence is omitted rather than chosen arbitrarily.
- **Two independent watermarks, one per stream.** The client seeds its **merge** cursor from `watermark_cursor` and its **verification** cursor from `verification_watermark_cursor`, then tails each with `seq > watermark`. Because the streams have separate id spaces, both are required — omitting the verification watermark forces fresh installs to replay the entire verification stream.
- **Both watermarks are captured *before* the page is read**, so a cursor can never sit ahead of a row's materialized state; anything newer is re-delivered by the tail and re-applied idempotently under the per-row seq guard.
- **The top-level global envelope is mandatory as of revision 1.4.** `schema_version` is `1`. `menu_group_id` / `menu_group_revision` are non-null only while the selected restaurant advertises `global_menu_v1`; otherwise both are `null`. A global client must validate the group before applying any assignment row.
- **`global_menu_item_id` / `global_variant_id` appear only when a menu group has linked the row** (§25). They are omitted rather than sent blank, and their absence is meaningful: an unlinked row must stay store-qualified in All Stores rather than being grouped with another restaurant's row by name.
- **The snapshot serves every current assignment, unfiltered.** Revision 1.1 excluded a hardcoded pair of collapsed `menu_item_id`s so one desktop release's local `FOREIGN KEY` would not abort its menu pull; that filter is removed in 1.2 (§24). A client whose catalog cannot map an id the server holds must resolve it at bootstrap — the server does not shape its ground truth around a client's local schema.

### 17.7 Freeze checklist for the central server team

Frozen surfaces the client relies on (changing any is a breaking change):

1. `(ingested_at, id)` replay order; v2 cursors; v1 rejected with 400.
2. `server_seq` + `server_ingested_at` on every delta event.
3. Per-event `accepted` / `rejected` ingest result.
4. `order_item_assignments` upsert rules: merge guarded by `last_seq`, `is_verified` seeded on INSERT only; verification guarded by `last_verification_seq`, flag-only. `order_line_key` on an assignment is **rejected** with `400 retired_field` as of revision 1.2 (§17.2, §24), not accepted and ignored; assignment materialization is otherwise unchanged and no assignment write produces a locator.
5. Snapshot response fields incl. **both** watermarks + cursors and per-row `last_seq` / `last_verification_seq`.
6. The two contract fixtures, kept byte-identical across repos.

Additive changes (new response keys, new event kinds behind new `kind` values, new nullable columns) remain safe under §4.6.

---

## 18. Analytics telemetry ingest (implemented)

These endpoints share §4.1 auth. They do **not** use replay cursors or `scope_key` (except where noted). Unknown top-level keys are ignored only where the serializer is a `LenientSerializer` (menu bootstrap); other serializers reject unknown keys.

### 18.1 Errors ingest

`POST /desktop-analytics-sync/errors/ingest`

**Body:**

```json
{
  "uploaded_by": {"employee_id": "0001", "name": "Owner"},
  "records": [
    {
      "record_id": "err-uuid-or-hash",
      "payload": {
        "ts": "2026-04-14T10:00:00Z",
        "level": "ERROR",
        "message": "…",
        "exception": "…",
        "traceback": "…",
        "context": {}
      }
    }
  ]
}
```

- Idempotent by `record_id` (insert-or-ignore). Records with blank `record_id` are skipped silently.
- `payload` fields are all optional except the wrapping `record_id`.

**Response (HTTP `200`):** `{"received": <int>, "created": <int>}`

### 18.2 Learning ingest

`POST /desktop-analytics-sync/learning/ingest`

**Body** (all top-level arrays/objects optional):

| Field | Idempotency key | Notes |
|-------|-----------------|-------|
| `ai_logs[]` | `query_id` (UUID) | Upsert. Fields: `user_query`, `intent`, `sql_generated`, `response_type` (`text`\|`table`\|`chart`\|`multi`), `response_payload`, `error_message`, `execution_time_ms`, `created_at`, `raw_user_query`, `corrected_query`, `action_sequence`, `explanation`, `model`, `total_prompt_tokens`, `total_completion_tokens`, `llm_calls`, `cache_hits` |
| `ai_call_trace[]` | `query_id` (replace rows) | Per-step trace rows for uploaded `ai_logs`. Replace existing trace rows for each `query_id` before inserting the provided ordered rows. Fields: `query_id`, `step`, `source` (`llm`\|`cache`), `model`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `created_at` |
| `ai_feedback[]` | `(feedback_id, query_id)` | `is_positive`, `comment`, `created_at` |
| `cache_stats` | — | Tier3 batch; always appends when any tier3 field present. Current shape: `total_entries`, `by_call_id`, `hit_miss_counters` |
| `aggregated_counters` | — | Tier3 batch. Current shape: `intents_per_day`, `response_type_counts`, `total_ai_logs_7d` |
| `schema_hash` | — | Tier3 batch |
| `llm_cache_feedback[]` | `(key_hash, call_id)` | `value`, `is_incorrect`, `created_at`, `last_used_at` |
| `uploaded_by` | — | Optional attribution |

**Response (HTTP `200`):** `{"ai_logs": N, "ai_call_trace": N, "ai_feedback": N, "tier3": 0|1, "llm_cache_feedback": N}` (counts of rows processed per section)

The client marks rows uploaded from this response, not just from the 2xx status. It treats a section count `>=` the number of rows it sent as "all accepted"; a short count means "some rejected, retry the whole batch" (ingest upsert makes the re-send safe). **Optional (recommended for precise partial-accept):** the server may also echo `accepted_ai_logs_ids` and `accepted_ai_feedback_ids` (lists of the `query_id` / `feedback_id` values it persisted); when present the client marks exactly that subset, so a single bad row no longer forces the whole batch to retry. Omitting these keys keeps the count-based behavior.

### 18.3 Conversations sync

`POST /desktop-analytics-sync/conversations/sync`

**Body:**

```json
{
  "conversations": [
    {
      "conversation_id": "uuid",
      "title": "…",
      "started_at": "…",
      "updated_at": "…",
      "synced_at": "…",
      "messages": [
        {
          "message_id": "uuid",
          "role": "user",
          "content": "string or JSON object/array",
          "type": "…",
          "sql_query": "…",
          "explanation": "…",
          "query_id": "uuid",
          "query_status": "complete|incomplete|ignored",
          "created_at": "…"
        }
      ]
    }
  ]
}
```

- Upsert by `conversation_id` and `(conversation_id, message_id)`.
- `content` accepts a string or JSON object/array (stored as string).

**Response (HTTP `200`):** `{"conversations": N, "messages": N}`

---

## 19. Server-authoritative menu mutation commits (IMPLEMENTED)

> **Implemented extension.** This section is the authoritative wire contract for the server-authoritative menu mutation flow. Every covered human menu mutation becomes an online-required, server-authorized commit before the desktop writes local SQLite when strict mode is active. Snapshots + pull replay (§17) remain for fresh install, peer convergence, and recovery.

**Server implementation target:** `backend/desktop_analytics_app_sync/` — models, views, and tests alongside the existing replay streams. Reuse `services/assignment_state.py` for materialization.

### 19.1 Goal

Make this server the commit authority for per-order-item assignment state, verification state, merge/undo history, and operator-authored catalog-only edits. Catalog metadata still flows through bootstrap `id_maps` for fresh-install seed/recovery, but catalog-only human edits commit synchronously through this endpoint and are merged into the bootstrap latest row inside the same revision transaction.

**In scope (catalog + assignment human edit paths):**

| Operation | `mutation_type` |
|-----------|-----------------|
| Merge items (incl. variant mappings) | `menu_merge.applied` |
| Undo merge | `menu_merge.undone` |
| Resolve variant | `resolution_variant` |
| Remap order-item cluster | `order_item_remap` |
| Verify item | `verify` |
| In-place catalog rename/retype, catalog-only verify, standalone variant create | `catalog_update` |
| Machine-derived POS-backed assignment flush | `derived_assignment.sync` |

`catalog_update` carries a non-empty `catalog_delta`, no merge event, and no verification events. Catalog-only verify may use `catalog_update`; verify operations with mapping rows continue to use `verify`.

`derived_assignment.sync` carries a v2 menu-merge-shaped event whose `merge_payload.kind` is `derived_assignment_v1`. It is emitted by the desktop for local machine-derived rows that have POS backing and no server sequence yet. The server treats it as create-only per `order_item_id`: existing `OrderItemAssignment` rows are skipped and echoed in `skipped_existing`; no existing row is overwritten.

### 19.2 Scope state row (`MenuScopeState`)

Add one row per `scope_key` (Django model in `desktop_analytics_app_sync`):

```
MenuScopeState(
  scope_key              text primary key,
  menu_revision          bigint not null default 0,   -- monotonic; +1 per accepted mutation
  latest_menu_merge_seq  bigint,
  latest_verification_seq bigint,
  updated_at             timestamptz not null
)
```

- `menu_revision` is a **global** per-scope counter (intentionally conservative — may reject non-overlapping concurrent edits; the client auto-pulls and retries once).
- The `strict_mode_enabled` column was **dropped (2026-07-09, strict-only)**; there is no per-scope mode state. Sync behaves as if strict were always on.

### 19.3 Revision advertisement in pull responses (Phase 1.3)

These responses must include `menu_revision` (an additive key under §4.6):

- `GET /desktop-analytics-sync/menu-bootstrap/latest`
- `GET /desktop-analytics-sync/menu-assignments/snapshot`
- `GET /desktop-analytics-sync/menu-merges`
- `GET /desktop-analytics-sync/menu-mapping-verifications`

`strict_mode_enabled` is **removed as of revision 1.2**. It was a hardcoded `true`, never scope state, kept only so already-deployed clients — which fell back to legacy push, now `404`, when the key was absent — kept committing. Every install runs the always-strict release, so the key has no reader left. The desktop mirrors `menu_revision` into local `system_config` as `menu_state_revision`.

### 19.4 Commit endpoint

`POST /desktop-analytics-sync/menu-mutations/commit` — **exactly one** mutation per request; reject arrays/batches with HTTP `400`.

**Request:**

```json
{
  "schema_version": 1,
  "mutation_id": "uuid-v4 — stable across retries",
  "mutation_type": "menu_merge.applied | menu_merge.undone | resolution_variant | order_item_remap | verify | catalog_update | derived_assignment.sync",
  "expected_menu_revision": 123,
  "event": {
    "remote_event_id": "...",
    "event_type": "menu_merge.applied",
    "schema_version": 2,
    "occurred_at": "...",
    "source_item": {},
    "target_item": {},
    "merge_payload": { "kind": "resolution_variant_v1", "assignments": [] }
  },
  "verification_events": [],
  "catalog_delta": { "items": [], "variants": [] },
  "uploaded_by": {},
  "uploaded_from": {}
}
```

- `event` reuses the §17 menu merge event shape (schema v2, first-class `assignments`) where applicable. It is omitted or `null` for `verify` and `catalog_update`. For `derived_assignment.sync`, `merge_payload.kind` is `derived_assignment_v1` and every assignment must send `is_verified: 0`.
- `verification_events` carries flag updates currently emitted on the mapping-verification stream. It is empty for pure `catalog_update`.
- `catalog_delta` carries menu items/variants created or renamed by the operation (applied by the client to local `menu_items` / `variants`). For `catalog_update`, it is required and must contain at least one item or variant.
- `mutation_id` is the idempotency key per `(scope_key, mutation_id)`.

### 19.5 Accepted response — HTTP `200`

```json
{
  "status": "accepted",
  "mutation_id": "...",
  "menu_revision": 124,
  "accepted_events": [
    { "remote_event_id": "...", "server_seq": 456, "server_ingested_at": "..." }
  ],
  "assignment_rows": [
    { "order_item_id": "...", "menu_item_id": "...", "variant_id": null,
      "is_verified": 1, "assignment_seq": 456, "verification_seq": 89 }
  ],
  "catalog_delta": { "items": [], "variants": [] },
  "skipped_existing": [],
  "merge_cursor": "...",
  "verification_cursor": "..."
}
```

The desktop applies this in one SQLite transaction. `assignment_rows` are for client-side parity checking — not a second server write path.
`skipped_existing` is present for `derived_assignment.sync` and contains assignment keys that already had server rows. The client leaves those local guesses untouched until the normal pull stream/snapshot brings down the authoritative row.

### 19.6 Conflict response — HTTP `409`

Non-destructive — the server persists **nothing** for the rejected mutation:

```json
{
  "status": "conflict",
  "error": "Menu state changed on the server. Pull latest menu state and retry.",
  "expected_menu_revision": 123,
  "current_menu_revision": 124,
  "conflicting_events": [
    { "remote_event_id": "...", "server_seq": 456, "server_ingested_at": "...",
      "attribution": {}, "order_item_ids": ["...", "..."] }
  ],
  "recommended_action": "pull_latest_menu_state"
}
```

`conflicting_events` lists events committed between `expected_menu_revision` and `current_menu_revision`. Each entry **must** include `order_item_ids` (from stored event assignments) so the client can detect genuine overlap vs. a spurious global-revision conflict for assignment-bearing mutations.

Pure `catalog_update` plans have no `order_item_ids`, so the desktop intentionally fails closed on any 409: it surfaces a user-visible conflict and does not silently pull/retry. Operators should refresh the menu state and retry the catalog edit. This is the Phase 6 Option A rule; no `catalog_item_ids` / `catalog_variant_ids` metadata is required on 409.

### 19.7 Server transaction semantics

Everything below runs in **one** Postgres transaction:

1. Resolve `scope_key` (`scope.get_sync_scope_key()`).
2. Lock the scope row: `SELECT … FROM MenuScopeState WHERE scope_key = %s FOR UPDATE` — **before** the idempotency check so concurrent retries with the same `mutation_id` serialize.
3. **Idempotency (inside the lock):** if a `MenuMutationLog` row exists for `(scope_key, mutation_id)`, return its stored accepted response with HTTP `200` and do no further writes. Treat a unique-constraint violation on `MenuMutationLog` insert as a replay: re-read and return the stored response.
4. If `expected_menu_revision != menu_revision`, return **409** (§19.6) and persist nothing. Exception: `derived_assignment.sync` accepts the field but ignores it for OCC and does not bump `menu_revision`.
5. Validate the event, assignments, verification entries, and catalog delta. For `catalog_update`, require a non-empty `catalog_delta` and reject any merge/verification event payloads. For `derived_assignment.sync`, require `merge_payload.kind = derived_assignment_v1`, at least one assignment, and `is_verified: 0` for every assignment.
6. Persist the mutation log row (`MenuMutationLog`, §19.10) **including `response_json`** for replay.
7. Persist menu-merge and verification event rows with server ordering when present, then materialize into `order_item_assignments` via `services/assignment_state.py` (`apply_menu_merge_event_to_assignments` / verification apply). **Preserve single-owner `is_verified` (§17.4):** verification rows update the flag only under `id > last_verification_seq`; merge rows may set `is_verified` only when materializing a previously nonexistent assignment (create-only), never on update. For `derived_assignment_v1`, materialization is create-only for the whole assignment: insert when absent, skip when present. Pure `catalog_update` writes no assignment or verification events.
8. Merge the accepted `catalog_delta` into the server's catalog materialization and `MenuBootstrapLatest.id_maps` inside the same transaction. `GET /menu-bootstrap/latest` must reflect the catalog delta immediately after the commit, without waiting for a desktop bootstrap push.
9. `menu_revision += 1`; update `latest_menu_merge_seq` / `latest_verification_seq` when events were written, and always update `updated_at`. `derived_assignment.sync` updates latest stream heads but leaves `menu_revision` unchanged.
10. Commit and return the accepted response (§19.5). For pure `catalog_update`, `accepted_events` is `[]`, assignment rows are empty, and merge/verification cursors are unchanged or echoed at their current heads. For `derived_assignment.sync`, include `skipped_existing`.

**Orphaned commits are safe.** If the server accepts a mutation but the client never learns of it, the mutation reaches that client on its next pull like any peer edit.

### 19.8 Status endpoint

`GET /desktop-analytics-sync/menu-mutations/{mutation_id}`

Scope is resolved server-side from the bearer token (same as commit). Clients must **not** send `scope_key` in the query string or commit body.

- **200** — stored accepted response if `(scope_key, mutation_id)` is in `MenuMutationLog`.
- **404** — mutation was never committed.

**Client reconcile protocol after a POST timeout:**

1. `GET` status. **200** → apply stored response, report success.
2. **404** → one idempotent re-`POST` with the same `mutation_id` (serializes behind any in-flight transaction on the scope lock).
3. If the re-`POST` also fails at the network level → report failure; SQLite unchanged. A mutation that landed server-side converges via the next pull.

### 19.9 Legacy ingest gate — REMOVED (2026-07-09, strict-only)

Historical: batched menu-merge and mapping-verification ingest (§10, §17.5) used to look up `MenuScopeState.strict_mode_enabled` and, when `true`, reject the whole batch with **HTTP `426`** (`{"status":"upgrade_required", …}`). Those ingest endpoints have now been **removed entirely** (they return `404`), and the `strict_mode_enabled` gate column has been dropped — so there is no gate to evaluate. Interactive/client menu mutations flow through §19.7 commit. The operator-only baseline import is the deliberate cutover/recovery exception and writes through the internal persistence services.

### 19.10 Server data model (`MenuMutationLog`)

Required for Phases 1–7 (in addition to `MenuScopeState`, §19.2):

```
MenuMutationLog(
  scope_key, mutation_id, mutation_type,
  expected_menu_revision, accepted_menu_revision,
  request_json, response_json,
  attribution fields, created_at
)
```

Unique `(scope_key, mutation_id)`. Audit parent **and** idempotency store.

Keep existing menu-merge / verification event tables and `order_item_assignments` exactly as in §17 — they remain the replay streams and materialized truth.

**Phase 1 backfill (historical):** inserted one `MenuScopeState` per known scope; set `menu_revision` from current state (monotonic — e.g. current max event `id`, or `0`). The `strict_mode_enabled` column that this backfill set to `false` has since been dropped (strict-only).

### 19.11 Implementation checklist (server — Phases 1–2)

See also §14.3.

- [x] Migration: `MenuScopeState` + `MenuMutationLog`
- [x] Backfill one `MenuScopeState` per scope
- [x] Add `menu_revision` to pull/snapshot responses (§19.3)
- [x] `POST …/menu-mutations/commit` with §19.7 semantics
- [x] Unit tests: accept, duplicate replay, concurrent same-`mutation_id` serialize, stale 409 with `order_item_ids`, malformed event 400, single-owner `is_verified` preserved
- [x] `GET …/menu-mutations/{mutation_id}` (§19.8)
- [x] ~~Legacy ingest gate (§19.9)~~ — gate + legacy ingest endpoints **removed 2026-07-09** (strict-only)

---

## 20. Normalized server catalog (optional Phase C — IMPLEMENTED)

Moves catalog metadata off bootstrap JSON into queryable server tables. **Not required** for strict-mode mutation commits (§19); bootstrap `id_maps` remain supported for backward compatibility.

### 20.1 Server data model

```
MenuCatalogItem(
  scope_key, menu_item_id, name, item_type, is_verified, updated_at
)  -- unique (scope_key, menu_item_id)

MenuCatalogVariant(
  scope_key, variant_id, variant_name, unit, value, metadata_source, is_verified, updated_at
)  -- unique (scope_key, variant_id)
```

`type_id_to_str` from bootstrap is **not** normalized into a separate table in Phase C; it is preserved from `MenuBootstrapLatest.id_maps` when publishing catalog snapshots.

### 20.2 Materialization sources

Normalized rows are updated from (in replay order during rebuild):

1. `MenuBootstrapLatest.id_maps` (`menu_id_to_str`, `variant_id_to_str`)
2. Accepted `MenuMutationLog.request_json.catalog_delta` payloads (strict-mode commits)
3. `MenuMergeEvent.payload_json` `source_item` / `target_item` and variant mappings

Live paths mirror rebuild: bootstrap ingest, mutation commit, and operator baseline-import event persistence each upsert catalog rows.

### 20.3 Catalog snapshot endpoint

`GET /desktop-analytics-sync/menu-catalog-snapshot/latest`

Returns bootstrap-compatible `id_maps` plus explicit `items` and `variants` arrays, `snapshot_id`, `version`, `generated_at`, and the scope field `menu_revision` (§19.3).

```json
{
  "snapshot_id": "menu-catalog-2026-07-06T12:34:56+00:00",
  "version": 1,
  "generated_at": "2026-07-06T12:34:56+00:00",
  "id_maps": {
    "menu_id_to_str": {"item_latte": "Latte"},
    "variant_id_to_str": {"variant_hot": "Hot"},
    "type_id_to_str": {"type_bev": "Beverage"}
  },
  "items": [
    {"menu_item_id": "item_latte", "name": "Latte", "type": "Beverage", "is_verified": true}
  ],
  "variants": [
    {
      "variant_id": "variant_hot",
      "variant_name": "Hot",
      "unit": "ML",
      "value": 250.0,
      "metadata_source": "explicit",
      "is_verified": true
    }
  ],
  "menu_revision": 42
}
```

### 20.4 Ops: rebuild + parity

Management command `rebuild_menu_catalog` replays bootstrap + mutations + merge events into normalized tables. `--verify-bootstrap-parity` compares rebuilt `menu_id_to_str` / `variant_id_to_str` against `MenuBootstrapLatest.id_maps`.

### 20.5 Implementation checklist (server — Phase C)

- [x] Migration: `MenuCatalogItem` + `MenuCatalogVariant` + backfill RunPython
- [x] `services/catalog_state.py` — apply, rebuild, snapshot builder, parity check
- [x] Bootstrap ingest, mutation commit, and operator baseline import update normalized catalog
- [x] `GET …/menu-catalog-snapshot/latest`
- [x] `rebuild_menu_catalog` management command
- [x] Unit tests: bootstrap materialization, commit `catalog_delta`, snapshot endpoint, rebuild parity, merge-event snapshots

---

## 21. Server-authoritative customer mutation commits (IMPLEMENTED)

> **Implemented extension (2026-07-06).** Section 8 remains the freeze target for customer-merge pull replay. This section is the authoritative wire contract for strict-mode human customer edits. Every merge or undo is an online-required, server-authorized commit before the desktop writes local SQLite when strict mode is active. Pull replay (§8) remains for fresh install, peer convergence, and recovery. There is **no** server materialized customer table — the server is an event log + revision guard. Rollout and validation: §21.12 and analytics `docs/MENU_SYNC_ARCHITECTURE.md` §14–§15.

**Server implementation:** `backend/desktop_analytics_app_sync/` — `CustomerScopeState`, `CustomerMutationLog`, `services/customer_mutation_commit.py`, `views/customer_mutations.py`.

### 21.1 Goal

Make this server the commit authority for **customer merge/undo history** and therefore the customer mapping derived from it. Customer merge volume is low; full event replay from a null cursor already converges customers via portable locators — no snapshot endpoint.

**In scope (two human edit paths):**

| Operation | `mutation_type` |
|-----------|-----------------|
| Merge customers | `customer_merge.applied` |
| Undo merge | `customer_merge.undone` |

Customer `is_verified` changes only inside merge/undo events — there is no separate verification stream and no `catalog_delta`.

### 21.2 Scope state row (`CustomerScopeState`)

One row per `scope_key`, **separate from** `MenuScopeState` (§19.2):

```
CustomerScopeState(
  scope_key                  text primary key,
  customer_revision          bigint not null default 0,   -- monotonic; +1 per accepted mutation
  latest_customer_merge_seq  bigint,
  updated_at                 timestamptz not null
)
```

- `customer_revision` is a **global** per-scope counter (intentionally conservative — may reject non-overlapping concurrent edits; the client auto-pulls and retries once when `customer_keys` do not overlap).
- The `strict_mode_enabled` column was **dropped (2026-07-09, strict-only)**; there is no per-scope mode state.

### 21.3 Revision advertisement in pull responses

`GET /desktop-analytics-sync/customer-merges` must include `customer_revision` as an additive top-level key (safe under §4.6).

`strict_mode_enabled` is **removed as of revision 1.2**: a hardcoded `true`, never scope state, kept only so already-deployed clients kept committing. The desktop mirrors `customer_revision` into local `system_config` as `customer_state_revision`.

### 21.4 Commit endpoint

`POST /desktop-analytics-sync/customer-mutations/commit` — **exactly one** mutation per request; reject arrays/batches with HTTP `400`.

**Request:**

```json
{
  "schema_version": 1,
  "mutation_id": "uuid-v4 — stable across retries",
  "mutation_type": "customer_merge.applied | customer_merge.undone",
  "expected_customer_revision": 41,
  "event": { "...": "the existing §7.3/§7.4 customer merge event payload, verbatim" },
  "uploaded_by": {},
  "uploaded_from": {}
}
```

- `event` reuses the **existing** customer merge event shape (§7.3 applied / §7.4 undone): `remote_event_id`, `attribution`, `source_customer` / `target_customer` (snapshots + portable locators), `merge_metadata`, `moved_orders`, `local_refs`, and — for undo — `reverts_remote_event_id` + `undo_metadata`.
- `mutation_id` is the idempotency key per `(scope_key, mutation_id)`.

### 21.5 Accepted response — HTTP `200`

```json
{
  "status": "accepted",
  "mutation_id": "...",
  "customer_revision": 42,
  "accepted_events": [
    { "remote_event_id": "...", "server_seq": 456, "server_ingested_at": "..." }
  ],
  "customer_merge_cursor": "<v2 cursor positioned after the accepted event>"
}
```

The desktop applies this in one SQLite transaction via the existing pull appliers. No `assignment_rows` or `catalog_delta` — the server does not materialize customer state.

### 21.6 Conflict response — HTTP `409`

Non-destructive — the server persists **nothing** for the rejected mutation:

```json
{
  "status": "conflict",
  "error": "Customer state changed on the server. Pull latest customer state and retry.",
  "expected_customer_revision": 41,
  "current_customer_revision": 42,
  "conflicting_events": [
    { "remote_event_id": "...", "server_seq": 456, "server_ingested_at": "...",
      "attribution": {}, "customer_keys": ["<phone_hash>", "<name_address_hash>", "..."] }
  ],
  "recommended_action": "pull_latest_customer_state"
}
```

`conflicting_events` lists events committed between `expected_customer_revision` and `current_customer_revision`. Each entry **must** include `customer_keys` — the deduplicated non-null values of `source_customer.portable_locators.phone_hash`, `source_customer.portable_locators.name_address_hash`, `target_customer.portable_locators.phone_hash`, and `target_customer.portable_locators.name_address_hash` from the stored `payload_json` — so the client can detect genuine overlap vs. a spurious global-revision conflict.

Malformed events (e.g. undo referencing unknown `reverts_remote_event_id`) return HTTP `422`.

### 21.7 Server transaction semantics

Everything below runs in **one** Postgres transaction (same shape as §19.7):

1. Resolve `scope_key` (`scope.get_sync_scope_key()`).
2. Lock the scope row: `SELECT … FROM CustomerScopeState WHERE scope_key = %s FOR UPDATE` — **before** the idempotency check so concurrent retries with the same `mutation_id` serialize.
3. **Idempotency (inside the lock):** if a `CustomerMutationLog` row exists for `(scope_key, mutation_id)`, return its stored accepted response with HTTP `200` and do no further writes. Treat a unique-constraint violation on `CustomerMutationLog` insert as a replay: re-read and return the stored response.
4. If `expected_customer_revision != customer_revision`, return **409** (§21.6) and persist nothing.
5. Validate the event: required top-level fields; `event_type` matches `mutation_type`; for `customer_merge.undone`, `reverts_remote_event_id` must reference an event in this scope.
6. Persist the mutation log row (`CustomerMutationLog`, §21.10) **including `response_json`** for replay.
7. Persist the customer merge event row via the shared event-persistence service (dedupe by `(scope_key, remote_event_id)`), so pull replay (§8) serves it to peers unchanged.
8. `customer_revision += 1`; update `latest_customer_merge_seq` / `updated_at`.
9. Compute `customer_merge_cursor` (v2 token at the accepted event), commit, and return the accepted response (§21.5).

**Orphaned commits are safe.** If the server accepts a mutation but the client never learns of it, the mutation reaches that client on its next pull like any peer edit.

### 21.8 Status endpoint

`GET /desktop-analytics-sync/customer-mutations/{mutation_id}`

Scope is resolved server-side from the required `X-Restaurant-ID` selector (§23.2), same as commit. `?scope_key=` is not read; clients must not send it.

- **200** — stored accepted response if `(scope_key, mutation_id)` is in `CustomerMutationLog`.
- **404** — mutation was never committed.

**Client reconcile protocol after a POST timeout:**

1. `GET` status. **200** → apply stored response, report success.
2. **404** → one idempotent re-`POST` with the same `mutation_id` (serializes behind any in-flight transaction on the scope lock).
3. If the re-`POST` also fails at the network level → report failure; SQLite unchanged. A mutation that landed server-side converges via the next pull.

### 21.9 Legacy ingest gate — REMOVED (2026-07-09, strict-only)

Historical: `POST /desktop-analytics-sync/customer-merges/ingest` used to look up `CustomerScopeState.strict_mode_enabled` and, when `true`, reject the whole batch with **HTTP `426`** (`{"status":"upgrade_required", …}`). That ingest endpoint has now been **removed entirely** (returns `404`) and the `strict_mode_enabled` gate column dropped. `GET /customer-merges` (pull) stays available to everyone; the only writer that bumps `customer_revision` is the §21.4 commit endpoint.

### 21.10 Server data model (`CustomerMutationLog`)

In addition to `CustomerScopeState` (§21.2):

```
CustomerMutationLog(
  scope_key, mutation_id, mutation_type,
  expected_customer_revision, accepted_customer_revision,
  request_json, response_json,
  attribution fields, created_at
)
```

Unique `(scope_key, mutation_id)`. Audit parent **and** idempotency store.

Keep the existing customer merge event table (§6.1) exactly as it is — it remains the replay stream. **No materialized customer table.**

**Phase 1 backfill (historical):** inserted one `CustomerScopeState` per known scope; set `customer_revision` from current max customer-merge event `id` (or `0`). The `strict_mode_enabled` column that this backfill set to `false` has since been dropped (strict-only).

### 21.11 Implementation checklist (server — Phases 1–2)

See also §14.4.

- [x] Migration: `CustomerScopeState` + `CustomerMutationLog`
- [x] Backfill one `CustomerScopeState` per scope
- [x] Add `customer_revision` to pull responses (§21.3)
- [x] `POST …/customer-mutations/commit` with §21.7 semantics
- [x] Unit tests: accept, duplicate replay, concurrent same-`mutation_id` serialize, stale 409 with `customer_keys`, malformed event 400, undo unknown revert 422
- [x] `GET …/customer-mutations/{mutation_id}` (§21.8)
- [x] ~~Legacy customer ingest gate (§21.9)~~ — gate + legacy ingest endpoint **removed 2026-07-09** (strict-only)
- [x] Phase 7 validation — strict-mode scenarios green in analytics `tests/test_customer_strict_mode_validation.py` (client) and `CustomerMutationPhase7ValidationTests` in `backend/desktop_analytics_app_sync/tests.py` (server); see analytics `docs/MENU_SYNC_ARCHITECTURE.md` §15

### 21.12 Rollout (Phase 6) — historical

The staged rollout below flipped a per-scope `strict_mode_enabled` gate. That gate, its `set_customer_strict_mode` command, and the legacy ingest endpoints have since been **removed (2026-07-09)** — sync is unconditionally strict. The table is retained for historical context; there is no per-scope flag to flip anymore, and menu/customer no longer have independent modes.

| Step | Who | Action |
|------|-----|--------|
| 6.1 | Each install | Drain legacy outbox: `POST /api/sync/drain-customer-outbox` (or `drain_customer_outbox` / client-learning cycle). Verify `customer_merge_unsent = 0`. |
| 6.2 | Server | Deploy Phases 1–2 with `strict_mode_enabled = false` everywhere. |
| 6.3 | Client | Release client that mirrors `customer_state_revision` / `customer_strict_mode_enabled` and can commit; stays on legacy push until server flips the scope flag. |
| 6.4 | Each install + server | Re-verify 6.1, then `python manage.py set_customer_strict_mode --enable` (bumps `customer_revision` to latest event ids by default). Legacy batched ingest then returns HTTP `426`. |

**Client rollout helpers (historical, removed 2026-07-09):** `src/core/customer_outbox_drain.py` (`get_customer_outbox_status`, `drain_customer_outbox`); API `GET /api/sync/customer-rollout-status`, `POST /api/sync/drain-customer-outbox`.

**Status (2026-07-07): ROLLOUT COMPLETE.** Prod server deployed; `set_customer_strict_mode --enable` flipped (`customer_revision` 58 == event stream at flip); legacy batched ingest returned HTTP 426. Live-validated same day: client `strict_mode_active = true`, real merge + undo round-trip through `POST /customer-mutations/commit` (revision 58 → 59 → 60), zero outbox rows.

**Follow-up (2026-07-09): STRICT MADE UNCONDITIONAL.** With every install on the strict-capable client, the legacy ingest endpoints, the `strict_mode_enabled` gate columns, and the `set_menu_strict_mode` / `set_customer_strict_mode` commands were removed; strict is now the only behavior. The `strict_mode_enabled` response key remained a hardcoded `true` until revision 1.2 removed it, once every install ran a release that no longer read it.

**Client replay quarantine (2026-07-07, client-local behavior — not a wire change):** replay events whose customers cannot be uniquely resolved against local data (weak/ambiguous `portable_locators`) are quarantined client-side in `customer_merge_unresolved_events` and retried on every later pull instead of halting the stream; the pull cursor and advertised scope state keep applying. See analytics `docs/MENU_SYNC_ARCHITECTURE.md` §9.

---

## 22. Central server-authored forecasting sync (IMPLEMENTED except §22.8)

> Implemented except the §22.8 weather read, which is still unrouted. Forecasting is computed only on the central server (`backend/forecasting/`, nightly Celery task on the `forecasting` queue). The desktop app is a downstream cache/reader. Weather rows are already served inline by §22.6 bootstrap and §22.7 delta as `weather_rows`; only the standalone `GET forecasts/weather` endpoint is missing.

### 22.1 Goal

Move all production forecasting computation, model artifacts, backtests, and weather API integration out of the desktop analytics app and into the central server.

Desktop responsibilities after cutover:

- call forecast bootstrap/delta during Sync DB
- store server-authored rows in local SQLite cache tables
- render existing forecast pages from those local cache tables
- never train, retrain, backtest, upload, or repair forecasts locally

Central server responsibilities:

- run nightly forecast generation after the completed business day is available
- own forecast weather data and weather forecast API calls
- publish complete successful runs only
- expose read-only forecast bootstrap/delta/status APIs to desktop clients

### 22.2 Clean-slate policy

There is no forecast backfill or backward-compatibility requirement for this redesign.

- The central forecasting tables may be created fresh.
- Existing `ingest_*forecast*` rows are legacy cache telemetry and should not shape the new schema.
- `POST /desktop-analytics-sync/forecasts/ingest` is unrouted; the route returns HTTP `404` (§24).
- The nightly task must not scan for missing generated dates or queue automatic gap-fill jobs.
- If a nightly run fails, APIs keep serving the latest previous successful run.
- If a missed/backdated generated date is required, an operator runs a one-time management command for that exact `generated_on` date.

Recommended operator command shape:

```bash
python manage.py run_nightly_forecast --restaurant-id "$REST_ID" \
  --generated-on 2026-07-07 --families revenue,item,volume --force
```

### 22.3 Server data model contract

Exact model names may vary, but the API needs these durable concepts:

- `ForecastRun`
  - `run_id`
  - `scope_key`
  - `generated_on`
  - `training_window_start`
  - `training_window_end`
  - `status`: `running`, `success`, `failed`, or `superseded`
  - `started_at`, `completed_at`, `failed_at`
  - `families`: published families, e.g. `["revenue", "items", "volume"]`
  - `metrics`
  - monotonic `server_seq` for sync
- `ForecastRow`
  - monotonic `server_seq`
  - `run_id`
  - `scope_key`
  - `family`: `revenue`, `items`, or `volume`
  - `kind`: `forward`, `history`, or `backtest`
  - `forecast_date`
  - `model_name` nullable
  - `entity_id` nullable (`item_id` for item/volume)
  - `entity_name` nullable
  - `payload` JSON
- `ForecastWeatherRow`
  - monotonic `server_seq`
  - `scope_key`
  - `weather_date`
  - `payload` JSON, including observed/forecast source metadata

Rows are append-only for sync. Regenerating the same `generated_on` with `--force` should create a new `run_id` and mark the previous run superseded, not mutate old rows in place. Desktop UI chooses the latest successful/non-superseded run from cached run metadata.

### 22.4 Cursor semantics

Forecast sync cursors are independent from collaboration replay cursors in §4.4.

- Query param: optional `cursor`; omitted cursor means "start from the beginning of retained forecast sync rows."
- Cursor value: opaque string. A numeric max `server_seq` encoded as a string is acceptable for v1.
- Ordering: ascending `server_seq`.
- The cursor covers run rows, forecast rows, and weather rows.
- Desktop advances the cursor only after applying the whole response in one SQLite transaction.
- Invalid/expired cursor: return HTTP `400` with `{"error":"Invalid forecast cursor."}`. Desktop should recover by calling bootstrap.

### 22.5 Forecast status

`GET /desktop-analytics-sync/forecasts/status`

Returns the latest publish state for the resolved scope.

**Response (HTTP `200`):**

```json
{
  "schema_version": 1,
  "scope_key": "global",
  "latest_run": {
    "run_id": "uuid",
    "generated_on": "2026-07-08",
    "training_window_start": "2026-03-10",
    "training_window_end": "2026-07-07",
    "status": "success",
    "completed_at": "2026-07-08T02:58:10Z",
    "families": ["revenue", "items", "volume"],
    "metrics": {
      "active_items": 60,
      "assignment_coverage_pct": 99.2
    }
  }
}
```

If no successful run exists, return `latest_run: null` and HTTP `200`.

### 22.6 Forecast bootstrap

`GET /desktop-analytics-sync/forecasts/central-bootstrap`

> **Route note:** `forecasts/central-bootstrap` is the only route. `forecasts/bootstrap` returns `404` — see §24.

Use when the desktop forecast cache is empty/reset or when `forecasts/delta` returns an invalid cursor error.

**Query params:**

| Param | Default | Description |
|-------|---------|-------------|
| `families` | `revenue,items,volume,weather` | Comma-separated families to include |
| `days` | server default | Optional forward horizon cap |
| `history_days` | server default | Optional historical/backtest cap |

**Response (HTTP `200`):**

```json
{
  "schema_version": 1,
  "scope_key": "global",
  "next_cursor": "123456",
  "runs": [
    {
      "server_seq": 1200,
      "run_id": "uuid",
      "generated_on": "2026-07-08",
      "status": "success",
      "completed_at": "2026-07-08T02:58:10Z",
      "families": ["revenue", "items", "volume"],
      "metrics": {}
    }
  ],
  "rows": [
    {
      "server_seq": 1201,
      "run_id": "uuid",
      "family": "revenue",
      "kind": "forward",
      "forecast_date": "2026-07-08",
      "model_name": "gp",
      "entity_id": null,
      "entity_name": null,
      "payload": {
        "revenue": 40500.0,
        "orders": 0,
        "gp_lower": 29000.0,
        "gp_upper": 52000.0
      }
    }
  ],
  "weather_rows": [
    {
      "server_seq": 1210,
      "weather_date": "2026-07-08",
      "payload": {
        "temp_max": 35.0,
        "rain_sum": 0.0,
        "rain_category": "none",
        "source": "forecast"
      }
    }
  ]
}
```

Bootstrap returns retained server-authored rows only. It must not merge in legacy desktop-uploaded forecast cache rows.

### 22.7 Forecast delta

`GET /desktop-analytics-sync/forecasts/delta`

This is the normal Sync DB endpoint after the first bootstrap.

**Query params:**

| Param | Default | Description |
|-------|---------|-------------|
| `cursor` | none | Opaque forecast cursor from prior bootstrap/delta |
| `limit` | `1000` | Max cursor-bearing sync entries; server may cap. Repeated parent-run dependency metadata does not consume this limit. |
| `families` | `revenue,items,volume,weather` | Comma-separated families to include |

**Response (HTTP `200`):** same top-level shape as §22.6, plus:

```json
{
  "has_more": false
}
```

When no rows are available, return empty `runs`, `rows`, and `weather_rows`, echo a valid `next_cursor`, and `has_more: false`.

Every emitted forecast row must have its parent run in the same response's
`runs` array. The server repeats that run as dependency metadata when its
`server_seq` is at or behind the caller's cursor (for example, when weather
advanced the cursor while the run was still `running`). Repeated parent runs
are idempotent, do not consume `limit`, and do not change `next_cursor`.

### 22.8 Weather read (NOT YET IMPLEMENTED)

> **Status:** This endpoint is **planned but not yet implemented** on the server. No route is registered. The response shape below is the target contract for a future revision.

`GET /desktop-analytics-sync/forecasts/weather?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`

Optional direct read endpoint for debugging or UI surfaces that need weather without a full forecast sync. Forecast bootstrap/delta already includes the weather rows needed by normal desktop forecast charts.

**Response (HTTP `200`):**

```json
{
  "schema_version": 1,
  "scope_key": "global",
  "weather_rows": [
    {
      "weather_date": "2026-07-08",
      "payload": {
        "temp_max": 35.0,
        "temp_min": 28.0,
        "rain_sum": 0.0,
        "rain_category": "none",
        "source": "forecast"
      }
    }
  ]
}
```

### 22.9 Forecast chart reads

These read-only endpoints return chart-shaped payloads compatible with the current desktop forecast pages. They are optional for normal Sync DB, which should use bootstrap/delta rows, but they are useful for direct server-backed UI reads and diagnostics.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `forecasts/revenue` | Revenue chart payload for latest or selected generated date |
| `GET` | `forecasts/items` | Item-demand chart payload for latest or selected generated date |
| `GET` | `forecasts/volume` | Volume-demand chart payload for latest or selected generated date |

Common query params:

| Param | Default | Description |
|-------|---------|-------------|
| `days` | family default | Forward horizon cap |
| `history_days` | `30` | Historical/backtest window cap |
| `generated_on` | `latest` | Specific successful generated date or latest family run |
| `item_id` | none | Optional item filter for item/volume endpoints |

If the requested family has not published a successful run, item and volume chart reads return `awaiting_action: true` with `debug_info.missing_prerequisite: "order_line_key"`.

### 22.10 Desktop local cache expectation

The desktop should add local cache tables equivalent to:

- `central_forecast_runs`
- `central_forecast_rows`
- `central_forecast_weather`
- `forecast_sync_cursor` in config/state

Sync DB applies `forecasts/central-bootstrap` or `forecasts/delta` in one transaction. Local forecast routes read only these cache tables. Local ML model files, retraining endpoints, backtest jobs, and forecast upload jobs should be removed from production desktop code after cutover.

## 23. Restaurant selection and the raw analytics streams (IMPLEMENTED)

**Base path:** `/analytics/` (see `config/urls.py`) — **not** `/desktop-analytics-sync/`. These four streams and the allowed-restaurants endpoint are the only routes in this contract mounted outside the sync base path; they are documented here because the desktop consumes them with the same restaurant profile it syncs with.

**Server implementation:** `backend/analytics/views/`, `backend/analytics/queries.py`, `backend/core/restaurant_selector.py`.

> **Breaking change in revision 1.1.** `X-Restaurant-ID` is **required** on every scoped route, there is no default restaurant, and the child streams no longer return `order_pk` / `order_item_pk`. A client written against revision 1.0 does not work against 1.1. There is no compatibility mode, no grace period, and no version negotiation.
>
> **Revision 1.2** removes the remaining tolerances; see §24.

### 23.1 Why a selector exists

Restaurant identity is not a column. It lives inside every immutable source event at `payload.raw_payload.properties.Restaurant.restID`, and the analytics fact tables are filtered on that JSON expression. Two restaurants legitimately issue the same Petpooja `orderID`, so `aggregate_id` is **not** unique across restaurants and is never an isolation boundary. The `order_pk` / `order_item_pk` columns that carried the same ambiguity on the child tables are gone entirely.

Consequently every response is scoped to exactly one restaurant, and the request must say which one.

### 23.2 Selecting a restaurant

```
X-Restaurant-ID: 1c8w7fp500
```

- **Required** on every scoped route. There is no query-parameter alternative and no default: a request that does not name a restaurant is refused, because the only alternatives are guessing and answering with data the caller did not ask for.
- The value is normalized by trimming **ASCII spaces only** and preserving case — the same normalization the server applies to `restID` inside a source event.
- Grants are explicit per credential. A credential with no configured grant is authorized for no restaurant and every scoped request it makes is refused.

Selector errors carry a machine-readable `code` beside the human message:

| HTTP | `code` | When |
|------|--------|------|
| `400` | `restaurant_selector_missing` | No `X-Restaurant-ID` header |
| `400` | `restaurant_selector_invalid` | Header present but blank or unusable |
| `400` | `unknown_restaurant` | The credential is granted a restaurant this deployment does not configure (operator misconfiguration) |
| `403` | `restaurant_forbidden` | A valid credential asked for a restaurant it has no grant for, or holds no grants at all |
| `403` | `restaurant_disabled` | Route-specific: the selected restaurant is configured and granted, but its ingestion is disabled, so an ingest-side route refuses instead of queueing work whose every result would be rejected (only the manual pull — §23.3) |

```json
{ "error": "This credential is not authorized for restaurant '9zz9zz9zz9'.", "code": "restaurant_forbidden" }
```

Every refusal in this table uses that envelope — a human message in `error`, the machine-readable value in `code`. Branch on `code`; the message is free to change.

The grant check runs **before** the registry lookup, so a credential cannot enumerate which restaurants a deployment configures: an ungranted value is `403` whether or not it exists. Authorization is also decided **before** body validation on the write routes, so an unauthorized caller is refused whatever it sent rather than being told what the server thought of its payload.

### 23.3 Where the selector applies

| Surface | Selector | Notes |
|---------|----------|-------|
| `GET /analytics/orders`, `order-items`, `addons`, `discounts` (§23.4) | Required | Filters the returned rows |
| `GET /analytics/restaurants` (§23.6) | n/a | Lists what the credential may select |
| Every scope-keyed `/desktop-analytics-sync/` route (§7–§22) | Required | Resolved to the internal `scope_key`; see §4.2 |
| `POST errors/ingest`, `learning/ingest`, `conversations/sync` (§18) | Ignored | Not restaurant-scoped: these rows are keyed by uploader and natural keys, so a selector on them would imply a partition that does not exist |
| `POST /webhooks/petpooja/sync_petpooja_for_today/` | Required | Authorizes the restaurant, then queues **that** restaurant's own pull task and answers `202` with `status`, `date` and `restaurant_id`. A configured, granted restaurant whose ingestion is disabled is refused with `403` `restaurant_disabled` (plus `restaurant_id`) rather than queued, because every order the pull fetched would then be rejected at ingest |

### 23.4 The four raw analytics streams

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/analytics/orders/` | One current row per logical order of the selected restaurant |
| `GET` | `/analytics/order-items/` | Order lines whose current parent header belongs to the selected restaurant |
| `GET` | `/analytics/addons/` | Addon lines, filtered through the same current parent |
| `GET` | `/analytics/discounts/` | Discount lines, filtered through the same current parent |

**Auth:** `X-API-Key: <ANALYTICS_API_KEYS value>` — **not** the sync Bearer token. Missing or invalid → `403 {"error":"invalid api key"}`.

**Child filtering is by parent, not by key.** A child row belongs to whichever restaurant currently owns its `event_id`; the server joins `order_fact` on `event_id` and filters the parent's `restID`. `order_line_key` identifies a line but is not an authorization boundary. Re-projecting an order replaces its header and all its children under a new `event_id`, so a child never outlives the header it was filtered by.

**Response envelope** (identical on all four):

```json
{
  "data": [ { "stream_id": 1, "event_id": "…", "…": "…" } ],
  "cursor": { "cursor": 1 }
}
```

Per-stream `data` fields:

| Stream | Fields |
|--------|--------|
| `orders` | `stream_id`, `event_id`, `aggregate_type`, `aggregate_id`, `event_type`, `occurred_at`, `raw_event` |
| `order-items` | `stream_id`, `event_id`, `order_item_id`, `order_line_key`, `line_index`, `raw_item` |
| `addons` | `stream_id`, `event_id`, `parent_order_line_key`, `line_index`, `addon_index`, `raw_addon` |
| `discounts` | `stream_id`, `event_id`, `raw_discount` |

**Children link to their parent by `event_id` only.** The bare Petpooja `order_pk` and `order_item_pk` columns were **dropped from the fact tables** (revision 1.2): two restaurants share those values, so nothing could key on them, and no query read them. An addon names its exact parent line with `parent_order_line_key` plus `line_index`/`addon_index`, which is what keeps two occurrences of one menu item in an order distinguishable.

`aggregate_id` is the bare Petpooja order ID (`"19470"`) in the source event, in the fact row, in this response and in the desktop. It is deliberately not `restID:orderID` — the source events are immutable and re-encoding identity there would rewrite history — and it is therefore **not unique across restaurants**. Key on `(restaurant, aggregate_id)` or on `event_id`, never on `aggregate_id` alone.

### 23.5 Cursor and limit semantics

| Param | Default | Rules |
|-------|---------|-------|
| `cursor` | `0` | Integer `stream_id` exclusive lower bound. Non-integer or negative → `400` `invalid_page_parameter` |
| `limit` | `1000` | Integer ≥ 1, clamped to `5000`. Non-integer or `< 1` → `400` `invalid_page_parameter` |

- Rows are ordered by `stream_id` ascending, and each stream has its own `stream_id` space.
- `cursor.cursor` is the `stream_id` of the **last row in the page**, or `null` when the page is empty. A caught-up client must keep its own cursor: storing `null` would restart it from `0`.
- **Cursors are per restaurant.** The page skips other restaurants' rows entirely rather than returning gaps, so a cursor obtained under one restaurant is meaningless under another. A client with two profiles keeps two cursors per stream.
- A recalculation re-issues every `stream_id` above the previous high-water mark, so cursors always move forward; they are never reused or regressed.

### 23.6 Allowed restaurants

`GET /analytics/restaurants/` — the desktop's **profile selector source**. Generated from settings, returning only the restaurants the calling credential is granted. Same `X-API-Key` auth as the streams.

```json
{
  "restaurants": [
    {
      "restaurant_id": "1c8w7fp500",
      "display_name": "Dach & Nona",
      "timezone": "Asia/Kolkata",
      "menu_group_id": null,
      "menu_capabilities": []
    }
  ]
}
```

- Sorted by `restaurant_id`. A credential with no grants gets `{"restaurants": []}`.
- Internal `sync_scope_key` and `credential_ref` are **never** exposed.
- `menu_group_id` and `menu_capabilities` (revision 1.3) are the **only** gate on global menu behavior. `null` / `[]` means this restaurant owns its menu alone and the client stays on restaurant-scoped menu behavior; a grouped restaurant carries its group id and all four capabilities of §25.3. Both keys are **always present**, so a client that cached an enablement is told it is gone rather than keeping it because the server said nothing. A local flag must never enable global writes.
- A grant naming a restaurant the deployment does not configure is silently omitted — listing it would make the desktop create a profile whose every request is refused.

### 23.7 Desktop client requirements

1. Read the profile list from `GET /analytics/restaurants/`; never hard-code restaurant IDs.
2. Send `X-Restaurant-ID` from the shared header builder used by **every** raw stream and sync request — do not special-case the orders stream. A request without it fails.
3. Keep one cursor per `(profile, stream)` and store it inside that profile's database.
4. Link children to orders by `event_id`, and addons to their line by `parent_order_line_key`. Do not reintroduce `order_pk` / `order_item_pk` joins.
5. One SQLite file per restaurant. Refuse to open a profile database whose stored restaurant identity differs from the selected restaurant: the desktop's global `UNIQUE(petpooja_order_id)` is safe only because each database holds one restaurant.
6. Treat `403 restaurant_forbidden` as "this profile is not (or no longer) authorized" — do not retry it as an auth failure or fall back to another profile.

## 24. Retired wire surface (revision 1.2)

> **Revisions 1.4 through 1.6 deliberately replace the provisional global-menu wire, revision 1.9 removes the two POS-ownership policies 1.7 and 1.8 added, and revision 1.10 removes display-name aliases while narrowing itemcode rules to parent-only.** There is one controlled analytics client, so the server carries no adapters for the old unversioned snapshot/event envelopes or retired policy surface. The global-menu removals are listed in **§25.12–§25.13**; the revision-1.2 retired surfaces below remain retired.

The central server carries no backward compatibility. Anything that existed only to
keep an older desktop working is deleted, and deletion is *enforced* rather than
implied: a retired field or parameter is refused, because an unread one is
indistinguishable from one the server honoured. A client that keeps sending it looks
successful while the behaviour it asks for never happens.

| Retired | Was | Now |
|---|---|---|
| `strict_mode_enabled` response key | Hardcoded `true` on menu/customer pull, snapshot and commit responses, so a deployed client did not fall back to legacy push | **Absent** from every payload. Its removal condition — every install on the always-strict release — was verified before this build shipped (production 2026-08-08) |
| `order_line_key` on `merge_payload.assignments[]`, and its `petpooja_order_id` / `petpooja_itemid` metadata | Accepted on the menu commit and ignored | **`400`** with `code: retired_field`, on any of the three. Locators are materialized centrally from current order-line facts; there is no client-supplied path and no operator import command. Request shape only — `petpooja_itemid` on an assignment snapshot *response* (§17.6) is unaffected |
| `legacy` query parameter (any scoped route) | Selected an uploader-keyed forecast payload that no longer exists; latterly inert | **`400`** with `code: retired_parameter` |
| `scope_key` query parameter (any scoped route) | Named internal storage partitioning directly; latterly inert | **`400`** with `code: retired_parameter`. `X-Restaurant-ID` is the only selector, and the server derives the scope from it |
| `restaurant_id` / `restaurant` query parameter (any restaurant-aware route, sync **and** `/analytics/`) | Never read — there has never been a query-parameter selector | **`400`** with `code: restaurant_selector_not_a_parameter`. This is the one silent case that matters: a header naming restaurant A and a query string naming B would be answered with A's rows, and the caller could not tell |
| `order_pk` / `order_item_pk` fact columns | Bare Petpooja IDs two restaurants share, never returned but still stored | **Dropped** from `order_item_fact`, `order_item_addon_fact` and `order_discount_fact` |
| `sync_restaurant_unsupported` response code | The manual pull (§23.3) refused every restaurant but the deployment default, because the pull took no restaurant argument | **Never sent.** The pull is per restaurant and queues the selected one; there is no default to fall back to. Ingest-disabled is the only remaining route-specific refusal, and it is `403` `restaurant_disabled` |
| `POST …/forecasts/ingest` and the three batched ingest routes (`customer-merges/ingest`, `menu-merges/ingest`, `menu-mapping-verifications/ingest`) | Batched desktop uploads of merge/verification events and uploader-authored forecast cache rows | **`404`**. Merge/verification events enter through mutation commits or the operator baseline import. Forecast ingest models were removed from Django state by migration `desktop_analytics_app_sync/0023`; migration `0024` dropped the six legacy tables |
| `GET …/forecasts/bootstrap` | A route alias for `forecasts/central-bootstrap` | **`404`**. Unrouted, not redirected — a redirect would keep the old name working. `forecasts/central-bootstrap` is the only name |
| The collapsed-`menu_item_id` exclusion on the §17.6 assignment snapshot and on materialization | A hardcoded set of two menu ids one desktop release had folded into other items. Serving them made that client's `apply_assignments` fail a local `FOREIGN KEY` and abort the whole menu pull | **Gone.** The snapshot serves the assignment ground truth as it stands, including those ids. A client that cannot map an id the server holds resolves it at bootstrap — a client's local catalog shape is not a server filter |

Two shared bare IDs are **kept**, deliberately:

- **`aggregate_id`** stays the bare Petpooja order ID everywhere. Source events are
  immutable, so re-encoding identity there would rewrite history. It is therefore
  not unique across restaurants and no client logic may treat it as such.
- **`order_item_id`** stays on the `order-items` stream and on assignment rows. It is
  a Petpooja *menu* item id, not a parent order identifier, so it cannot join two
  restaurants' order lines the way `order_pk` could; the desktop keys assignments
  `(scope_key, order_item_id)` and one profile database holds one restaurant. Line
  identity is `order_line_key` + `line_index`, and that is what an addon points at.

## 25. Global menu groups (revision 1.10)

**Base path:** `/desktop-analytics-sync/global-menu/`. Auth per §4.1, selector per §23.2. The two `mutations/` POST routes additionally require `X-Global-Menu-Key` (§25.3).

**Server implementation:** `backend/desktop_analytics_app_sync/services/global_menu_*.py`, `views/global_menu.py`, `global_menu_auth.py`.

Restaurants that use the same business menu belong to one server-managed **menu group**. The group, not a restaurant, owns canonical menu identity and human menu decisions. A rename or a merge is committed once, centrally, and lands in every member restaurant.

This is deliberately **not** a fan-out that repeats one merge in each profile. A fan-out can partially fail, cannot cover POS identifiers a restaurant has not seen yet, and creates several authorities for one question. The central server stays the single commit authority.

**Enrolment is membership plus ordinary sync.** A restaurant added to a group consumes the group's existing canonical catalog immediately. Its unmapped rows arrive globally unlinked, stay store-qualified in every read, and a human resolves them one at a time through the ordinary `global_locator.map` mutation. That is a working state, not a partially-failed rollout: there is no lifecycle rung to advance, no bootstrap to run, and no coverage threshold that must be met before the group answers.

### 25.1 What is global and what is not

Global within a menu group:

- canonical item and variant identity, labels and types;
- human-confirmed, parent-only `itemcode` mapping rules;
- merge redirects and undo history;
- the menu-group mutation revision and event order.

Restaurant-specific, and never shared by grouping:

- orders, order lines, customers, forecasts, prices, availability and sales;
- **every POS locator**, without exception — see §25.4;
- the materialized assignment state for that restaurant's order items;
- audit attribution recording the restaurant a global edit originated from.

A configured group that owns **no canonical catalog** is a deployment error, not a client state. Every `/global-menu/` route answers `500` `global_menu_group_not_provisioned` and `GET /analytics/restaurants/` raises rather than advertising nothing: answering as if the restaurant were ungrouped would look like a working install right up to the point where the desktop re-clusters a shared menu locally. A group is *joined*, never bootstrapped from a member; a group whose catalog does not exist yet is fixed by creating it (§25.8.4) or by removing the `menu_group` setting from its members.

### 25.2 Identity and revision

- A global item/variant id is **server-issued and immutable**. It is never derived from a display name: a rename changes metadata, not identity, so historical analytics keyed on it do not split.
- Uniqueness of *canonical identity* inside a group is enforced separately, on normalized name plus type (items) or name plus unit and value (variants), and only among **active** rows. A merged source releases its identity and keeps resolving through its redirect forever.
- `menu_group_revision` is one monotonic integer per group, advanced by exactly one per accepted mutation. The revision-1.10 data migrations also advance it once per affected group while appending the corresponding semantic rule-normalization or tombstone event; this is the only system-authored exception. It is the OCC token for §25.8.
- Membership is **not on the wire and not in a table a request can change**: it is declared per restaurant in the server's settings registry. Moving a restaurant between groups is a deployment.

### 25.3 Capabilities and the editor permission

`GET /analytics/restaurants/` (§23.6) advertises `menu_capabilities` for each restaurant. There are exactly two answers:

| Registry state | `menu_capabilities` | What the client may do |
|---|---|---|
| No `menu_group` | *(empty)* | Nothing global. The restaurant owns its menu alone and stays on restaurant-scoped menu behavior. |
| A `menu_group` that owns a canonical catalog | `global_menu_v1`, `global_menu_resolution_v1`, `global_menu_aggregation_v1`, `global_menu_mutations_v1` | Pull and cache global state, resolve unlinked identity, group All Stores rows by global identity, and author global mutations. |

The four capabilities are advertised **together**. They are not rungs and there is no partial grant, because the routes they cover are the routes a newly enrolled restaurant's unlinked rows are resolved *through* — withholding any of them would withhold the repair and leave the store permanently unable to reach the state that would have ungated it. Coverage (§25.7) is an operational diagnostic and gates nothing.

Editing a group's menu rewrites every member restaurant's canonical menu, so it is a **separate permission** from reading one restaurant. The `X-Global-Menu-Key` header is matched against `GLOBAL_MENU_EDITOR_KEYS` and the operator's `GLOBAL_MENU_EDITOR_GRANTS` must name the selected restaurant. Unlike `SYNC_API_KEY`, an **unset** editor mapping authorizes nobody — there is no dev "auth skipped" branch, because the credential exists precisely so that a read grant cannot imply group-wide write.

Every edit that changes canonical item identity, canonical variant identity or redirects must use §25.8. The restaurant-scoped §19 menu commit may still verify/reopen an assignment and record non-canonical store facts, but an attempted canonical write — a merge, undo, rename, retype, variant merge or catalog item/variant delta — from a member restaurant is `409` `global_menu_canonical_write_blocked`. It is never accepted locally and reconciled later: that would give one store a private fork of a menu the whole group shares.

### 25.4 Resolution precedence

A locator becomes canonical identity through approved evidence only, strongest first:

1. this restaurant's complete existing materialized assignment;
2. a server-approved **restaurant-qualified** POS locator rule;
3. this restaurant's existing parent-only assignment;
4. a human-confirmed **group-wide itemcode** rule, resolving the parent item only;
5. **unresolved** — which stays visible in Unclustered Data Resolution rather than being guessed at.

Every hit is passed through the redirect chain before it is returned. That is what makes a *future* row carrying an already-known locator land on a merge target it has never seen.

**A POS item/addon id is never group-owned.** `pos_item` and `pos_addon` are issued by one outlet's Petpooja account, and two members legitimately use the same number for different products, so their rules are restaurant-qualified without exception and a database check (`global_menu_rule_pos_restaurant_scoped_ck`) enforces it rather than convention. A request that asks for a group-scoped POS rule is refused.

**Fuzzy similarity never creates authority.** Normalization folds case and whitespace only, so it can stop two identical names both being canonical, but it cannot decide that two different names are the same product. A raw `itemcode` or display name may help a UI *suggest* a candidate. A display name never becomes authority; an itemcode becomes authority only after a human confirms a group-wide parent-item rule.

Scope follows from the locator kind and is not a free choice. `pos_item` / `pos_addon` are restaurant-scoped, which is also their default. `itemcode` is group-wide only and always stores an empty `global_variant_id`; a restaurant-scoped itemcode or a variant-bearing itemcode is refused rather than written. `alias` is retired authority: `locator_type: "alias"` is refused with `400 global_menu_invalid_mutation`, and display names remain UI suggestions only.

An addon never falls through to an itemcode rule: an addon id is a different id space from a menu item's, and a shared itemcode is a statement about menu items.

### 25.5 `GET global-menu/snapshot`

| Param | Default | Rules |
|---|---|---|
| `section` | `items` | One of `items`, `variants`, `redirects`, `rules`. Unknown → `400` `invalid_snapshot_section` |
| `after` | — | Last key of the previous page (`global_item_id`, `global_variant_id`, `source_global_id`, or the rule row id) |
| `limit` | `500` | Clamped to `2000` |

- Paged **per section**: the four collections have different key spaces, and one merged cursor would make "resume where I left off" depend on the order the server concatenated them.
- **Omission is never deletion.** Redirected and tombstoned entities are served explicitly with their `lifecycle_state`.
- **Rules are filtered to the requesting restaurant** — every group-scoped rule plus that restaurant's own restaurant-scoped ones. A peer's POS locator values mean nothing here and are withheld.
- Retired alias rows are absent because migration `0034` archives and deletes them, not because the read hides them. Its explicit `tombstones.mapping_rules` event removes any alias already cached by a desktop; omission alone never means deletion.
- A rule row carries no `price`. Prices are restaurant-owned: the group shares menu *identity*, and a canonical price would be a second authority over a fact each outlet already owns.
- Every response has `schema_version: 1`, `menu_group_id`, `menu_group_revision`, `section`, `rows`, `next_cursor`, `has_more`, and `snapshot_watermark: {event_seq, menu_group_revision}`. `next_cursor` is the value sent as the next request's `after`; `has_more` is authoritative.
- The watermark is captured **before** each page is read. The client pins the first page's watermark for the complete four-section snapshot, rejects a group change mid-snapshot, and tails from that pinned `event_seq`. A concurrent commit is therefore re-delivered rather than missed.

Snapshot plus event tail plus the §17.6 assignment snapshot is also the **client synchronization bootstrap** a fresh install or a new profile runs. That is ordinary data synchronization and is unrelated to any operator bootstrap: it stays exactly as specified here.

### 25.6 `GET global-menu/events`

`after` (integer event sequence, default `0`) and `limit` (default `500`, max `2000`). Non-integer `after` → `400` `invalid_page_parameter`.

Every response has `schema_version: 1`, group identity/revision, `event_head_seq`, `events`, `next_cursor`, and `has_more`. Order is the server's own `event_seq`; the next request sends `after=<next_cursor>`. One commit transaction appends its event and advances the revision under the group lock, so a client that has read up to a sequence has seen every effect at or below the revision that sequence carries.

Each event payload is a complete semantic delta: `action`, authoritative `items`, `variants`, `redirects`, and `mapping_rules`; explicit `tombstones.redirects` / `tombstones.mapping_rules`; `assignment_snapshot_required` and affected restaurant IDs; and the resulting group revision. Applied-count summaries are audit output, not replay authority. A client can converge from snapshot plus tail without interpreting mutation-specific server internals, and applies an event by its payload rather than by recognising its `event_type` — an unrecognised type is applied, not skipped.

`mapping_rules` and `tombstones.mapping_rules` are filtered to the requesting restaurant exactly as §25.5 filters the snapshot — its own restaurant-scoped rules plus every group-scoped one. The audit-shaped `action.locator` follows the same rule and is omitted from a foreign restaurant's event payload. Replay must not route another store's private POS identifiers around the filter paging applies. Catalog rows, redirects and revisions are group-wide and are not filtered.

Historical event JSON is immutable. When migration `0033` normalizes a variant-bearing itemcode rule, it appends the parent-only rule with `global_variant_id: ""`; event-tail projection also masks the stale variant in any older itemcode event. Migration `0034` appends explicit alias rule tombstones before deleting the live rows. A client that already cached either legacy shape therefore converges without treating snapshot omission as deletion.

### 25.7 `GET global-menu/status`

Advertised capabilities, group revision, catalog counts, and per-restaurant coverage. Internal scope keys never appear — restaurants are identified by `restaurant_id` (§23.6).

Each restaurant row carries exactly six counts and no verdict: `assignments`, `assignments_linked`, `assignments_unlinked`, `verified_assignments`, `verified_assignments_linked`, `verified_assignments_unlinked`. `verified_*` is reported apart because an unverified assignment is a mapping nobody has confirmed yet, so the two queues are worked differently.

`assignments_linked` and `verified_assignments_linked` require complete identity: a parent item is enough only when the local assignment has no variant; a variant-bearing local assignment also needs `global_variant_id`. `catalog.mapping_rules` counts only live authoritative rules (`pos_item`, `pos_addon`, `itemcode`); archived alias rows are excluded because they are not in the live rule table.

**Coverage gates nothing.** A newly enrolled member is *supposed* to arrive with unlinked rows, and these numbers are how an operator watches a human work through them. There is no `verified_coverage_complete` flag, because there is no decision left for one to feed.

### 25.8 Preview and strict mutation commit

Mutation types: `global_item.create`, `global_item.merge`, `global_item.rename`, `global_item.verify`, `global_variant.create`, `global_variant.merge`, `global_locator.map`, `global_menu.undo`.

#### 25.8.1 `POST global-menu/mutations/preview`

```json
{"schema_version":1,"mutation_type":"global_item.merge","expected_menu_group_revision":7,
 "payload":{"source_global_item_id":"…","target_global_item_id":"…"}}
```

Writes nothing. Returns per-restaurant and aggregate impact, the catalog rows on both sides, any `conflicts`, `commit_allowed`, and a `preview_digest`. A stale `expected_menu_group_revision` is **reported** here (`revision_current: false`), not refused — telling the client the current revision is exactly what lets it re-preview and ask the human again.

Conflict codes: `variant_reconciliation_required`, `variant_dimension_mismatch`, `canonical_identity_taken`, `undo_not_latest_mutation`, `undo_effects_superseded`, `created_identity_referenced`, `locator_assignment_conflict`.

`locator_assignment_conflict` is raised by `global_locator.map` when the locator already matches assignments that carry neither a blank global link nor the rule's own prior target. Those rows were mapped to something else, by a human or by an earlier rule, and rewriting them under a new rule would silently repoint them; the conflict reports the count and a sample of `order_item_id`s so the operator resolves those rows first.

Preview also sends **`500`** `global_menu_state_corrupt` when walking the stored redirect chain finds a cycle or exceeds the hop cap. It is separated from a generic internal error on purpose: retrying cannot fix it and neither can the client — the group's stored identity needs an operator — so a desktop must stop and report rather than back off and re-send.

#### 25.8.2 `POST global-menu/mutations/commit`

Adds `mutation_id` (UUID v4) and `preview_digest`, plus optional `uploaded_by` / `uploaded_from`.

The server **re-derives the preview under the group lock** and compares digests; it never trusts the client's account of what its mutation would do. That closes the window where an operator approves "this affects 12 rows in 2 restaurants" and a concurrent commit changes it before theirs lands. A commit without `preview_digest` is refused: there is no unpreviewed global mutation.

In one transaction: apply catalog/redirect/rule changes → repoint the affected restaurant assignments and rules → append the global event with audit attribution → store the accepted response → advance the revision and stream head.

- **`200`** — accepted. `applied` and `impact` carry aggregate and per-restaurant counts, never a list of order rows.
- **`200`** — replay. A repeated `mutation_id` returns the stored response byte-for-byte, whatever the rest of the request says.
- **`400`** `global_menu_invalid_mutation` — an unresolvable identity, a redirected source, a cycle, an unconfirmed group-wide locator rule, a locator rule at a scope its kind is never resolved at, or a preview with open conflicts. The body carries `conflicts` with the published §25.8.1 codes when the refusal came from one, so `undo_not_latest_mutation` is machine-readable and not merely prose.
- **`409`** `global_menu_revision_conflict` — stale revision *or* a preview that no longer holds. Non-destructive.
- **`400`** `global_menu_not_enabled` / `global_menu_mutations_disabled`, **`403`** `global_menu_editor_required` / `global_menu_editor_forbidden`, **`500`** `global_menu_group_not_provisioned` — see §25.1 and §25.3.

A merge **never deletes the source**: it writes a permanent redirect, marks the source `redirected`, and repoints existing assignments in the same transaction, stamping them with the mutation id that moved them and the accepted revision. Every mutation that marks rows this way also returns `applied.marked` — how many assignments carry its mutation id and how many mapping rules carry its revision, counted once the mutation had finished writing. Those are row counts, not the update counts beside them: one compound merge that reconciles a variant on a row it already repointed reports that row in both counters and once in `marked`.

A group-wide locator rule requires `rule_scope: "group"` **and** `confirm_group_wide: true`, and only `itemcode` may ask for it. Its `global_variant_id` must be blank: one provider itemcode can repeat across sibling variants and therefore identifies only their parent item. A POS locator's rule is restaurant-scoped and always belongs to the selected restaurant. `restaurant_id` may be omitted or repeat `X-Restaurant-ID`; naming another member is refused. `applied.assignments_repointed` names only the selected restaurant; a peer that uses the same number for a different product is not in the set at all. `locator_type: "alias"` and an itemcode carrying `global_variant_id` are both `400 global_menu_invalid_mutation`.

**Undo is another revisioned mutation, and only of the latest still-applied one.** Rolling back by editing history is forbidden; undoing out of order is where two desktops stop converging, because mutation N+1 may have moved the very rows undoing N would move back. An accepted undo removes its target from this LIFO stack, so after `create → map → undo map`, the create is once again the latest still-applied mutation and can be undone. The server refuses any other ordering with `undo_not_latest_mutation` rather than guessing.

Undo moves assignments back **by the mutation that moved them**, not by the revision they carry. Ingest stamps the current group revision on every row it links, so a row linked straight to a merge target *after* that merge shares the merge's revision while having never been at the source; addressing undo by revision would drag it onto a product it never had. Rows that already pointed at the target, and rows linked afterwards, are both left alone.

When an exact restaurant POS rule completes a parent-only assignment, undo restores that row to its accepted parent-only identity instead of erasing the parent. A legacy itemcode mutation log may retain its old variant-bearing audit payload, but undo normalizes the restored live itemcode rule to parent-only so it cannot violate revision-1.10 authority.

Addressing by mark is also what bounds how far back the LIFO stack can be walked. A later mutation over the same rows **overwrites** those marks, and undoing that later one restores the pointers without restoring the marks it overwrote — so undoing the earlier one would then match nothing, move zero rows, and still delete its redirect and reactivate a source no assignment points at. Before applying an undo the server re-counts the marks and compares them with the `applied.marked` census stored when the mutation was accepted; a mismatch is `undo_effects_superseded` and the undo is refused. Merges over disjoint rows are unaffected and still undo in full LIFO order — `merge X→Y`, `merge P→Q`, `undo P→Q`, `undo X→Y` all succeed. A chain (`merge A→B`, `merge B→C`) can only be undone back to the point where the marks are intact; past that the catalog is corrected by a new mutation, never by an undo that reports success without doing anything.

#### 25.8.3 `GET global-menu/mutations/{mutation_id}`

Replays the stored accepted response for POST-timeout reconciliation; `404` when unknown. Read-only, so it needs the group but **not** the editor permission — an operator whose commit timed out must be able to find out whether it landed even if their write grant was revoked in between.

#### 25.8.4 Growing the shared menu and resolving a joining store

```json
{"schema_version":1,"mutation_type":"global_item.create","expected_menu_group_revision":4,
 "payload":{"canonical_name":"Pistachio Kulfi","canonical_type":"Dessert","is_verified":false}}
```

`global_variant.create` takes `canonical_name` plus `dimension: {unit, value}`, and `is_verified` exactly as the item create above does. The server reads that flag with `bool()`, so omitting it creates an **unverified** canonical variant — and unlike an item, there is no `global_variant.verify` to correct one afterwards.

Create plus `global_locator.map` is the **only** way anything becomes canonical, and it covers both cases that look different but are the same operation: a dish added after the group was formed, and every unlinked row a newly enrolled restaurant arrives with. There is no bootstrap and nothing to rerun.

The enrolment loop is:

1. the store syncs; its unmapped rows arrive globally unlinked and appear in Unclustered Data Resolution;
2. an operator picks the existing canonical item — and the exact variant — the row means;
3. one `global_locator.map` writes a **restaurant-scoped** rule for that store and links only that store's matching assignments;
4. a genuinely new product gets a `global_item.create` / `global_variant.create` first.

Notes:

- The response's `applied.global_item_id` / `applied.global_variant_id` is the server-issued id. Map a locator to it with `global_locator.map` to link the restaurant rows; a create moves no assignment by itself.
- A name and type already held by an **active** row in the group is the `canonical_identity_taken` conflict, not a second canonical row.
- Undo **tombstones** the created row rather than deleting it — a client may already have cached the id, and §25.5 says omission is not deletion. Tombstoning releases the active identity, so the same name may be created again. Undo is refused while any assignment or mapping rule still references the row: retiring it would leave those pointing at a tombstone.
- Nothing here touches orders, order lines, prices, local catalog rows, locator evidence or legacy merge history. Mapping moves *identity*, not sales.

### 25.9 Global identity on the wire

`global_menu_item_id` and `global_variant_id` are **omitted** from an assignment snapshot row when unset, never sent blank. Absence already means "not linked", and a blank value would be one more thing All Stores could group two restaurants' unrelated rows under. An unlinked row must stay store-qualified until a human resolves it, and it must never be merged with another restaurant's by display-name similarity.

Internal `scope_key` never appears at all: per-restaurant impact, coverage and repoint counts are keyed by `restaurant_id`.

### 25.10 `GET global-menu/history`

This is the **human audit timeline**, not the convergence stream in §25.6. It lets every member see the same old and new merge history without pretending old restaurant events were reversible global mutations.

| Param | Default | Rules |
|---|---|---|
| `after` | — | Opaque cursor returned by the previous page; clients must not parse it |
| `limit` | `100` | Integer from `1` through `500` |

The response has `schema_version`, `menu_group_id`, `rows`, `next_cursor`, and `has_more`. Rows are ordered deterministically by `occurred_at`, source kind and source event id, newest first. Each row carries:

- stable `history_id` and `source_event_id`;
- `source_kind`: `legacy_restaurant_event` or `global_menu_event`;
- `event_type`, `origin_restaurant_id`, actor/attribution when known, and timestamps;
- source/target snapshots with names plus nullable global ids;
- `mutation_id` when one exists, `is_undoable`, and a compact audit `detail` object.

The server projects this unified page from the existing restaurant `MenuMergeEvent` rows for **all current group members** plus the group's `GlobalMenuEvent`/`GlobalMenuMutationLog`; it does not copy old rows into the convergence stream and does not manufacture mutation logs. Legacy rows therefore always have `mutation_id: null` and `is_undoable: false`. Global rows appear once, group-wide, and are undoable only when the normal §25.8 preview currently permits it. The client caches this read model separately from its legacy `merge_history` mutation table.

### 25.11 Removing a group

There is no lifecycle to roll back. Removing the `menu_group` setting from a restaurant returns it to restaurant-scoped menu behavior and withdraws every advertised capability; stored global identity is left intact, and the additive `global_*` columns and tables stay. A client is rebuilt against this revision; no older global-menu wire is supported.

### 25.12 Retired in revision 1.9

Revision 1.7's shared-Petpooja catalog policy and revision 1.8's group-owned POS alias policy are **removed**, along with the lifecycle ladder that predated both. They are listed here so a reader of an older revision can see what happened to each name rather than inferring it from silence. Every item below is gone from the server, not merely undocumented.

| Retired | Was | Now |
|---|---|---|
| `global_menu_shared_pos_catalog_v1` capability | Advertised group-owned Petpooja ids and one canonical price per shared POS entry | **Never advertised.** POS locators are restaurant-scoped unconditionally (§25.4) |
| `global_menu_group_pos_aliases_v1` capability | Advertised many reviewed group POS aliases resolving to one canonical pair | **Never advertised.** Same reason |
| `provisioning` / `shadow` / `aggregating` lifecycle rungs and `manage.py set_menu_group_status` | An operator-advanced ladder deciding what a group advertised | **Gone.** A configured member of a group with a catalog advertises all four capabilities (§25.3); `status` is absent from the §25.7 response |
| `verified_coverage_complete`, `has_verified_assignments` and every `shared_pos_*` / `alias_*` field on §25.7 | The activation gate and its evidence | **Absent.** Coverage is six counts and no verdict |
| `price` on a §25.5 / §25.6 mapping-rule row, and the `global_locator.price_update` mutation | The shared canonical POS catalog price | **Absent** from the wire; the mutation type is refused as unsupported. Prices are restaurant-owned |
| `shared_pos_catalog` and `group_pos_alias_observation` on §9.1 menu-bootstrap ingest, and their `*_updated` response keys | Private per-restaurant POS evidence shipped for the review queues | **Absent.** An unexpected key is ignored per §4.6; nothing stores or reads them |
| `GET/POST global-menu/alias-resolutions…` and `GET global-menu/alias-reconciliation/…` | The POS alias review queue, decision preview/commit/status and reconciliation plan/status | **`404`**. Unrouted, not redirected — there is no review queue to forward a client to |
| `409 global_menu_shadow_write_blocked` and its `menu_group_status` field | The §19 refusal of a restaurant-only canonical write once a group reached shadow | **`409` `global_menu_canonical_write_blocked`**, with no `menu_group_status`. The refusal no longer depends on a rung |
| `manage.py backfill_global_menu`, `reconcile_global_menu_shared_pos`, `reconcile_global_menu_group_aliases` | The one-shot operator bootstrap, its authority/reset-replay cutover, and the two policy reconciliations | **Removed.** A restaurant joins a group that already owns a catalog; the menu grows through §25.8.4 |
| `global_menu.shared_pos_reconciled` / `global_menu.group_pos_alias_reconciled` events, `GlobalMenuAliasDecision` | Operator-run reconciliation events and their stored review decisions | **Never appended / dropped** (migration `desktop_analytics_app_sync/0032`). The preserved `GlobalMenuBackfillRun` audit row of the original cutover is kept indefinitely and nothing writes to it |

The client synchronization bootstrap — canonical snapshot download, event-tail convergence, assignment snapshot download, snapshot cursors, revisions and idempotency — is **not** retired and is unchanged (§25.5). Only the operator bootstrap is gone; the two share a word and nothing else.

### 25.13 Retired in revision 1.10

Revision 1.10 removes display-name aliases from authority and narrows human-confirmed itemcode rules to parent-item identity. Unlike silent read filtering, both stored-data changes archive exact pre-migration rows and append semantic convergence events.

| Retired or changed | Was | Now |
|---|---|---|
| `locator_type: "alias"` in `global_locator.map` | A normalized display name could become a group-wide rule | **`400 global_menu_invalid_mutation`.** Display names are suggestions only |
| Live `alias` mapping-rule rows | Served as group-wide resolution authority | Archived with reason `display_alias_retired_1_10`, tombstoned in the group event tail, then deleted by migration `0034` |
| Alias resolution precedence | Followed itemcode as the weakest authoritative rule | Removed. An old cached rule is inert in the revision-1.10 client and is explicitly removed by its tombstone |
| Variant-bearing `itemcode` targets | Could resolve one repeated itemcode to one sibling variant | **Parent-only.** New requests carrying `global_variant_id` are `400`; migration `0033` archives old values, clears them, and publishes the normalized rules |
| Parent-only variant assignment counted as linked | A nonblank `global_menu_item_id` alone counted as linked | Counted unlinked until an exact POS rule supplies `global_variant_id` |
| Alias rows in `catalog.mapping_rules` | Counted while live | Excluded because aliases are archived and no longer live rules |

The archive table is `global_menu_mapping_rule_archive`; it preserves the original rule id, identity, target, provenance, revision and timestamps plus a reason and archive timestamp. Both migration reversals restore from it only when their system event is still the group head; rollback refuses after newer edits rather than overwriting them.

### 25.14 Fixture freeze

Exact revision-1.10 shapes are in `fixtures/1/global_menu_fixtures.json`, and `desktop_analytics_app_sync.tests_global_menu` fails if any fixture in that file has no pinning test. `fixtures/1/global_menu_alias_fixtures.json` remains deleted. `schema_version` remains `1`: invalid legacy mutation shapes are refused, and migration events converge stored legacy rules explicitly.
