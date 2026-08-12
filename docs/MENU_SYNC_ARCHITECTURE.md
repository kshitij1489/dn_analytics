# Menu clustering sync — architecture, database schema & operational runbook

**Audience:** Engineers on the desktop analytics client (this repo) and the Dachnona cloud (`db.dachnona`, Django app `desktop_analytics_app_sync`).
**Status:** As-built and signed off (2026-07-06). This is the durable reference for how menu clustering converges across installs. Open follow-ups live in [pending_task.md](./pending_task.md).
**Related:** [INDEX.md](./INDEX.md) (doc hub), [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) (general architecture), [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §17 (wire contract — authoritative for on-the-wire format).

**Restaurant scope (contract 1.2, desktop Phase 1):** every menu/customer/bootstrap/assignment/forecast request is made from a bound restaurant-profile connection and carries that identity as `X-Restaurant-ID`. Cursors, mirrored revisions, bootstrap flags, mutation history, and forecast caches therefore remain isolated naturally in separate profile databases. There is no default/scope query parameter, and `__all__` is never a central selector.

---

## 1. What this system does

Multiple installs edit menu clustering concurrently — merges, variant merges, per-order-item resolutions, verifications. The job of this subsystem is to make those human decisions **converge deterministically** across every install and the central server, and to make a **fresh install** reach the same clustering state as a long-lived one after one sync.

**Scope boundary.** Sync is *not* full SQLite replication. Two layers stay separate:

| Layer | What syncs | Role |
|-------|------------|------|
| **Orders** | Incremental order stream from the POS integration | Builds/updates `orders`, `order_items`, POS-derived customers per install |
| **Cloud collaboration** | Append-only **events** (menu merges, mapping verifications, customer merges) + a materialized assignment snapshot | Converges **human-driven** catalog and clustering decisions |

Orders are re-fetched per install from the POS; the cloud never replicates them. Clustering decisions *are* converged, via the design below.

---

## 2. Core design

### 2.1 Root cause of the old split-brain

Merge/resolution replay used to be **cluster-shaped** ("move source cluster into target"). That is non-commutative: it fails or no-ops when the peer's local state differs, producing permanent silent divergence. The durable truth is **row-shaped** — which `(menu_item_id, variant_id, is_verified)` each `order_item_id` belongs to. Row-shaped writes under a total order are commutative-resolvable: last write per `order_item_id` wins, identically on every machine.

### 2.2 Decisions

1. **Unit of truth = per-order-item assignment**: `order_item_id → (menu_item_id, variant_id, is_verified)`. Cluster membership is the aggregation of these rows.
2. **Authority = server ingestion order.** The server assigns a monotonic `server_seq` (the event row's auto-increment `id`) at ingest. All installs apply events in `(ingested_at, id)` order and resolve conflicts per `order_item_id` by highest `server_seq`. Client clocks (`occurred_at`) are display-only.
3. **The server materializes ground truth.** `OrderItemAssignment` on Dachnona is updated as events ingest, using the same per-key LWW rule. It is the queryable ground truth and the fast bootstrap path for fresh installs.
4. **Conflicts are resolved, not prevented.** Pushing sooner only shrinks the window; offline installs always reopen it. Determinism must not depend on timing.
5. **Nothing is silently dropped.** Un-appliable events go to a quarantine table surfaced via API; superseded local decisions produce a user-visible notice.
6. **`is_verified` has a single owning stream.** The mapping-verification stream (guarded by its own `verification_seq`) is the *sole* authority for the flag on existing rows. The merge stream may only **seed** it when creating a previously nonexistent assignment. Because only one stream ever writes the flag on an existing row, merge/verification interleaving can't diverge it and no cross-stream clock is needed. Snapshot bootstrap / force-reseed are the one exception: they adopt the flag from the authoritative full-state snapshot.

### 2.3 Event payload: first-class assignments

Every menu merge event (all kinds) carries an explicit assignment list, so any peer can apply it without needing the source cluster to still exist:

```json
"merge_payload": {
  "kind": "resolution_variant_v1",
  "assignments": [
    {"order_item_id": "…", "menu_item_id": "<target>", "variant_id": "<target-or-null>", "is_verified": 1}
  ]
}
```

- `SCHEMA_VERSION = 2`. Legacy v1 events remain convertible: the applier derives assignments from `history_payload` (`mapping_rows` for variant/resolution kinds; `affected_order_item_ids` + `target_id` for basic merges). Extraction is shared with the server and pinned by `contracts/fixtures/1/menu_merge_event_fixtures.json` in **both** repos (keep byte-identical).
- **Key-omission convention:** a normalized assignment always carries `order_item_id` and `menu_item_id`; `variant_id` / `is_verified` are **omitted** when the event doesn't specify them (v1 basic-merge derivation), and the applier then leaves the current row value untouched. `variant_id: null` (or `__NULL_VARIANT__`) means the SQL NULL variant. v2 emit always includes both keys.
- **`is_verified` is create-only on the merge stream:** written only when materializing a previously nonexistent row, never on UPDATE (the verification stream owns it — §2.2 rule 6).
- `menu_merge.undone` carries the **prior** assignments; an undo is just another LWW write at its own `server_seq` — no special casing.
- **Same-item events** (legacy `mapping_audit_v1` addon consolidations where source `menu_item_id` == target, carrying no derivable assignments) are recorded as **applied no-ops** rather than replayed — see `_is_self_merge_event` / `_record_self_merge_noop` in `menu_merge_sync.py`. Matches the server materializer, which skips underivable events.

### 2.4 Client apply algorithm

For each pulled event, in stream order (`menu_assignment_apply.apply_assignments`):

1. Skip if `remote_event_id` already recorded (dedupe). If it matches an existing local merge (`_find_matching_local_merge`, ignoring `origin='remote'` history rows), stamp `server_seq` / clear `pending_local` on its rows without inserting a new history row. If it's the echo of our own event and a row was meanwhile outranked, emit a "your resolution of X was superseded by {attribution}" notice.
2. Per assignment: `ensure_menu_item_exists` / `ensure_variant_exists` from the event snapshot; if the local row's `assignment_seq` ≥ event's `server_seq`, skip (stale). If the row is `pending_local`, apply the remote value **silently** (server order will judge). Otherwise UPDATE `menu_item_variants` + POS-backed `order_items` / `order_item_addons` to the target, set `assignment_seq = server_seq`, `pending_local = 0`. Rows whose assignment key has no local POS backing via `order_items.petpooja_itemid` or the deterministic generated-name key are skipped (this install never saw that order line).
3. Batch epilogue: recompute stats + resolution state for touched items, run the **state-scoped husk sweep** (§2.4.1), and clear forecast caches (including for swept items).
4. Record a `merge_history` row per applied event with `origin='remote'` so Resolution History and undo keep working.

#### 2.4.1 State-scoped GC (husk sweep) — design rule

**Reference-moving events must end with state-scoped GC, not touched-set GC.**

Touched-set GC ("delete the parent if a batch that names it just removed its
last reference") under-approximates: an applier can strip a parent's last
reference without ever recording that parent's id — e.g.
`update_local_order_rows_for_assignment_key` moves order rows by assignment
key and never captures the rows' *previous* `menu_item_id`. Any missed id was
a **permanent husk**: a `menu_items` / `variants` row with zero references,
visible in `/menu/list` and `/menu/variants/list` dropdowns but absent from
the Menu Matrix (which inner-joins `menu_item_variants`). Found live
2026-07-11: 3 husk items + 13 husk variants on the golden install. Charts
were never affected — aggregations read `order_items` directly.

The fix re-derives liveness from state: `menu_utils.sweep_orphan_menu_entities`
deletes every `menu_items` row with no `menu_item_variants` / `order_items` /
`order_item_addons` reference (clearing `suggestion_id` self-FK pointers
first), and every `variants` row with no reference **and past a 7-day creation
grace window** (`POST /menu/variants/create` legitimately inserts bare variant
rows before a mapping references them; schemas without `variants.created_at`
skip the variant sweep). It runs in the assignment batch epilogue *and* at the
end of every merge pull — even event-less pulls (`_run_orphan_sweep`) — so the
invariant "after any completed sync, no zero-reference husk exists" holds on
every install and self-heals husks left by older builds, with no migration.

**Customers are covered since 2026-07-12.** Their husk source is different:
`compute_customer_identity_key` (`services/load_orders.py`) mints
`anon:<uuid4>` fresh per call for name-only customers, so an order **replay**
could never re-find the original owner — it inserted a duplicate customer,
re-pointed the order, and stranded the old row (found live: 8 husks; plus
attribute drift, e.g. an order that gained a phone number re-keys
`addr:` → `phone:` and strands the `addr:` row). Two-layer fix: (1) source —
`_ensure_customer_no_stats` reuses the order's current owner
(`fallback_customer_id`) when the recomputed key is `anon:`, so replays are
idempotent for anonymous customers; (2) sweep — `sweep_orphan_customers`
(`services/load_orders.py`) deletes customers with zero `orders` references
**and no `customer_merge_history` row (source or target)** — merge lineage
must survive: those FKs have no cascade and undo needs the snapshots — past
the same 7-day `created_at` grace (schemas without the column skip). Runs at
the end of every order ingest (`sync_service.sync_database`) and every
customer merge pull (`pull_and_apply_customer_merge_events`), best-effort.
Zero-order merge sources/targets are **not** husks (24 live rows are legit
lineage). Anonymous customers never sync to the server (locators strip
`anon:` keys), so a swept anon row cannot be resurrected; a swept
deterministic-key customer referenced by a later merge event degrades to the
designed unresolved-event quarantine and retries.

**When adding a new synced entity whose events move references**, give it the
same state-scoped sweep from day one; do not rely on the applier's touched
set to trigger GC.

Local edit paths (`merge_menu_items`, `resolve_menu_item_variant`, `remap_order_item_cluster`, `undo_merge`, …) are unchanged except they set `pending_local = 1, assignment_seq = NULL` on rows they touch and emit events carrying `assignments`.

**Convergence argument:** all installs see the same totally-ordered stream, apply the same per-key LWW rule with a seq guard, and reconcile local provisional edits at ack time against that same order. Any two installs pulled to the same cursor with no pending edits have identical assignments. Fresh installs start from the materialized snapshot at watermark S and tail events with `seq > S` — same fixed point.

### 2.5 Out of scope

- Customer merge conflict semantics (inherits the server ordering fix automatically, but has no assignment applier — see §9 below and [pending_task.md](./pending_task.md)).
- Full SQLite replication; orders stay POS-sourced per install.
- Bit-identical SQLite across installs (would require full replication).

---

## 3. Database schema (as-built)

### 3.1 Client (SQLite `analytics.db`)

`menu_item_variants` — the per-order-item assignment table (PK `order_item_id`):

| Column | Meaning |
|--------|---------|
| `order_item_id` (PK) | Stable per-item-name key: the POS `itemid`, else `uuid5("generated_"+name)`. **Not** the integer `order_items.order_item_id`. Deterministic derivation across installs is what makes clustering converge. |
| `menu_item_id`, `variant_id`, `is_verified` | The assignment. `variant_id` NULL = the NULL variant. |
| `assignment_seq` | Highest merge-stream `server_seq` applied to this row (LWW guard). NULL on a fresh local edit. |
| `verification_seq` | Highest verification-stream `server_seq` applied (independent id space; the merge stream never stamps it). |
| `pending_local` | `1` = changed locally, not yet echoed back by the server. |

Added tables:

- `menu_sync_event_quarantine` (`menu_sync_quarantine.py`) — dead-letter for events that can't apply (pull) or were server-rejected (push). Columns: `remote_event_id` PK, `stream`, `payload`, `error`, `fail_count`, `first/last_failed_at`, `resolved_at`. Streams: `menu_merge`, `mapping_verification`, `*_push`. Surfaced via `GET /api/menu/sync-conflicts`; cleared by a successful retry or user dismiss.
- `menu_sync_supersede_notices` (`menu_assignment_apply.py`) — a peer's decision outranked a locally acknowledged one. Columns: `order_item_id`, `local_merge_id`, `superseded_by_event_id`, `attribution`, `created_at`, `acknowledged_at`.
- `merge_history.origin` — nullable; `'remote'` for sync-applied rows, NULL for local edits.

Pull cursors live in `system_config`: `menu_merge_pull_cursor`, `menu_mapping_verification_pull_cursor`, `customer_merge_pull_cursor` (all v2: `{"v":2, ingested_at, id}`), plus `sync_cursor_schema_version` (a one-time reset gate) and `menu_assignments_bootstrapped`.

### 3.2 Server (Postgres, `desktop_analytics_app_sync`)

`OrderItemAssignment` (migration 0012; `services/assignment_state.py`):

```python
scope_key          # restaurant scope; server-side it has no default and is
                   # NOT NULL with a non-blank check — the server derives it
                   # from the required X-Restaurant-ID header
order_item_id      # unique per (scope_key, order_item_id)
menu_item_id
variant_id         # null = NULL variant
is_verified
last_seq           # MenuMergeEvent.id that set this (LWW guard)
last_event_id
last_verification_seq  # migration 0013 — verification stream's own id space
employee_id / employee_name / install_id  # attribution
updated_at
```

- `apply_menu_merge_event_to_assignments(event_row)` upserts each row iff `event_row.id > last_seq`; verification events update the flag only under `id > last_verification_seq`. `derived_assignment_v1` is the create-only exception: insert absent rows, skip existing rows, and report skipped keys to the committing client. All run inside the ingest transaction.
- Migrations 0011 (indexes + v2 cursors + per-event ingest), 0012 (`OrderItemAssignment`), 0013 (`last_verification_seq`) are **applied to live prod** and the `rebuild_order_item_assignments --scope <key>` backfill has been run (it is also the recovery tool if the table is ever suspect).
- **Bootstrap demotion:** `menu_bootstrap_ingest` stops overwriting `MenuBootstrapLatest.cluster_state` when a payload carries `snapshot_role: "seed_only"` (current clients) or the ops toggle `MENU_BOOTSTRAP_TRUST_CLIENT_CLUSTER_STATE=false` is set. `id_maps` (catalog metadata) still merges forward. The stored cluster_state is deliberately *frozen*, not regenerated from `OrderItemAssignment` — assignments are the authoritative clustering source; the cluster_state format carries extra per-row detail no current client consumes.

---

## 4. Event streams & pull order

Pull order (in `src/core/services/cloud_pull_orchestrator.py` — keep code and this doc in sync):

1. **Menu bootstrap** — broad catalog / `id_maps` + `cluster_state`; seed-only by default.
2. **Menu assignments snapshot** — one-time fresh-install seed (§5), after the catalog exists, before event tails.
3. **Menu mapping verification events** — per-line `is_verified` on `menu_item_variants`.
4. **Menu merge events** — structural merges / resolution history.
5. **Derived assignment flush** — POS-backed machine-derived rows with no server sequence are committed create-only (`derived_assignment.sync`) after ground-truth menu pulls succeed.
6. **Customer merges.**

**Replay pulls and snapshots are permanent.** Even after server-authoritative strict mode is enabled, every install keeps pulling menu-merge and mapping-verification tails and can reseed from the assignment snapshot (`force_reseed_menu_assignments` / server `import_menu_assignment_baseline`). Strict mode changes how **this install authors** human edits (synchronous commit); it does not retire the replay streams.

**Sync DB ground-truth pulls are mandatory** when cloud sync is configured: menu bootstrap (before orders on an empty catalog), assignment snapshot, verification tail, and merge tail failures surface as warnings/errors on the Sync DB job — the job must not report full success when a menu ground-truth pull failed. **Customer-merge pull failures also surface as terminal Sync DB errors** (§15).

**Strict mode is live in prod since 2026-07-07** and sync has been strict-only since 2026-07-09. Live validation: client `strict_mode_active = true`, a real merge + undo round-tripped through `POST /menu-mutations/commit` (revision 371 → 372 → 373), zero outbox rows written, local state restored byte-identical after the undo.

**Global menu history is a head-drained cache, not a cursor stream (2026-08-12).** `GET …/global-menu/history` serves the unified timeline **newest-first** and its `after` cursor pages *backwards* into older rows, so an end-of-feed cursor can never be resumed from — persisting one pins the reader below every entry written later. `pull_global_menu_history` therefore starts every drain at the head and runs to the end of the feed; `global_menu_state.history_cursor` is only a within-drain resume point and is NULL at rest. **The drain must not stop early on an unchanged page.** Rows are re-projected per request — `is_undoable` follows the live undo preview and legacy snapshot global ids resolve later (contract §25.10) — and the page spans all *current* group members, so admitting a member interleaves its legacy rows deep in the feed. A page therefore proves nothing about the pages below it, and a drain cut short by a failed page must be able to reach the tail on the next run. What the drain does avoid is writing: `apply_global_menu_history_page` compares each row against its cached copy and skips byte-identical ones. Before this, each sync re-fetched *and* re-upserted the whole timeline — 8 pages and 744 redundant upserts per run on the live install; now it is 2 pages and 0 writes. Page limit is 500, the server maximum.

**The cache mirrors the projection, so it also prunes.** Because membership is a per-restaurant config field on the server, a restaurant leaving the group retires its legacy rows from the page; an insert/update-only drain would leave them in `global_menu_history` and Resolution History would keep showing them forever. A drain that reaches the end of the feed therefore deletes the group's cached rows it did not see, tracked through a temp table rather than a parameter list so the feed is not bounded by SQLite's variable limit. **Pruning runs only after a complete drain** — an unread tail and a retired row are indistinguishable locally, so an interrupted one must prune nothing. A feed that served zero rows also prunes nothing: an empty group and a wrong answer look the same from the client, and blanking the audit view is the worse outcome.

**Every Sync DB step names itself.** The pull chain is ~15 sequential round trips to one host, so a slow server used to look identical to a hung job: the progress bar simply stopped. `run_best_effort_cloud_pulls` takes an `on_phase` callback, the router streams those names as job statuses (running the pull on a worker thread so they arrive while it waits), and the UI renders the current one under the bar. The wait for `CLOUD_PULL_LOCK` is announced too.

**One Sync DB per restaurant.** `POST /api/sync/run` claims every restaurant a job will write — All Stores claims the token and each member — and returns `409 sync_already_running` while that job is live, so two overlapping runs can no longer serialize against each other inside SQLite.

**Resilience:** both menu pull loops quarantine per-event failures and keep advancing the cursor, with a retry pass at the start of each pull. Human edits commit synchronously through `menu_mutation_commit.py` — there is no outbox. A background pull runs after the scheduler's push phase each cycle, under a process-wide `CLOUD_PULL_LOCK` shared with the Sync DB job and the strict commit paths. **All Stores (2026-08-09):** the Sync DB coordinator runs this same single-store sequence once per authorized profile and holds `CLOUD_PULL_LOCK` for the whole loop, so a scheduler cycle cannot interleave between two stores; menu edits themselves stay physical-store-only ([SESSION_AND_ALL_STORES.md](./SESSION_AND_ALL_STORES.md) Part B).

---

## 5. Fresh-install fast path

`src/core/menu_assignment_bootstrap.py`, run from the pull orchestrator after catalog bootstrap:

- If `menu_assignments_bootstrapped` is unset **and** no menu-merge pull cursor exists (truly fresh), page `GET /desktop-analytics-sync/menu-assignments/snapshot?after=&limit=`, apply rows through the same seq-guarded applier at `server_seq = watermark_seq`, set the menu-merge pull cursor to the server-provided `watermark_cursor` (and the verification cursor to `verification_watermark_cursor`), mark bootstrapped. No history replay → cannot hit the old fresh-install conflict bug.
- Installs that already have a pull cursor are marked bootstrapped **without** reseeding (their state came from replay).
- Fetch errors leave the flag unset so the next pull retries.
- **Escape hatch:** `force_reseed_menu_assignments()` (client) + the `import_menu_assignment_baseline` management command (server) overwrite an install's cluster state from the snapshot regardless of prior state — for a coordinated cutover (edits frozen) or to recover a single diverged install. It overwrites pending local edits, so never run it on a machine with unsynced local work.

Bootstrap shipper (`menu_bootstrap_shipper.py`) builds the seed payload directly from the active profile SQLite database, sends `snapshot_role: "seed_only"`, and skips pushes while that profile's `system_config.menu_bootstrap_last_push_hash` sha256 is unchanged. This push is an optional freshness mirror for bootstrap seeds, not the revision/OCC authority for live catalog edits. Future packaged builds do not include bundled `id_maps_backup.json` / `cluster_state_backup.json` seeds.

---

## 6. Key files

| Topic | Location |
|-------|----------|
| Assignment extraction + seq-guarded apply | `src/core/menu_assignment_apply.py` |
| Assignment key ↔ local POS row backing | `src/core/order_item_key.py` |
| Machine-derived assignment flush | `src/core/derived_assignment_flush.py` |
| Menu merge pull/apply (routing, echo/ack, no-op) | `src/core/menu_merge_sync.py` |
| Fresh-install assignment snapshot seed | `src/core/menu_assignment_bootstrap.py` |
| In-memory catalog seed + bootstrap payload builders | `src/core/menu_catalog_seed.py` |
| Explicit JSON export/restore CLI (dev/disaster recovery only; `--restore --from DIR`, optionally `--catalog-only`) | `scripts/seed_from_backups.py` |
| Assignment schema (conditional ALTERs) | `src/core/menu_assignment_schema.py` |
| Mapping verification emit / pull | `src/core/menu_mapping_verification_sync.py`, `..._events.py` |
| Quarantine helpers | `src/core/menu_sync_quarantine.py` |
| Cursor migration (one-time reset) | `src/core/sync_cursor_migration.py` |
| Sync conflicts API | `src/api/routers/menu.py` (`/api/menu/sync-conflicts`) |
| Best-effort pull orchestration | `src/core/services/cloud_pull_orchestrator.py` |
| Background pull scheduler | `src/core/services/cloud_sync_scheduler.py` |
| Strict-mode commit | `src/core/menu_mutation_commit.py` |
| Local edit paths (stamp pending_local) | `utils/menu_utils.py` |
| Shared extraction fixtures | `contracts/fixtures/1/menu_merge_event_fixtures.json` (both repos) |
| Server: materializer + backfill | `db.dachnona backend/desktop_analytics_app_sync/services/assignment_state.py`, `rebuild_order_item_assignments` |
| Server: snapshot endpoint | `.../views/menu_assignments.py` |
| Server: wire contract | [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §17–§19 |
| Phase 7 validation tests | `tests/test_menu_strict_mode_validation.py` (client), `db.dachnona` `desktop_analytics_app_sync/tests.py` `MenuMutationPhase7ValidationTests` |

---

## 7. Feature flags & ops toggles

| Flag | Default | Effect |
|------|---------|--------|
| `MENU_SYNC_ASSIGNMENT_APPLY` (client env) | on | Assignment-based applier. Off switches to legacy cluster replay (emergency only). |
| `MENU_BOOTSTRAP_APPLY_MODE` / `menu_bootstrap_apply_mode` (config) | `seed_only` | `seed_and_relink_orders` re-enables the old `order_items` relink for restore flows. |
| `MENU_BOOTSTRAP_TRUST_CLIENT_CLUSTER_STATE` (server env) | — | `false` stops the server trusting client bootstrap cluster_state once the fleet is on seed-only clients. |

---

## 8. Operational runbook (symptoms, diagnostics, support)

### 8.1 What "divergence" means

Two installs both complete Sync DB "successfully" (orders OK, HTTP 200) but their SQLite still differs in ways that make cloud pull replay log errors or land events in quarantine. Cloud pulls are best-effort by design — per-stream failures are logged/quarantined, not raised.

### 8.2 Log signatures & cursor behavior

- **Per-event warnings** from `menu_merge_sync.py` / `customer_merge_sync.py` when applying one remote event throws. Menu-merge failures are quarantined.
- **Summary warnings** from `run_best_effort_cloud_pulls`: `Cloud pull <stream> reported error: <message>`.
- **Quarantine rows** in `menu_sync_event_quarantine` or via `GET /api/menu/sync-conflicts` — with `fail_count`, `error`, `remote_event_id`.
- **Supersede notices** (menu only): a peer's resolution outranked a locally acknowledged decision; same API response.
- **Menu-stream cursors advance past failed events**; check `menu_merge_pull_cursor` / `menu_mapping_verification_pull_cursor` in `system_config`. **Customer stream** can leave its cursor unchanged and retry the same head.

### 8.3 SQLite diagnostic queries

Run on `analytics.db` (the live one — see §8.5).

**Menu merge — does the referenced item exist?**
```sql
SELECT menu_item_id, name, type FROM menu_items
WHERE menu_item_id IN ('<source_uuid>', '<target_uuid>');
```
Assignment-based apply can `ensure_menu_item_exists` from the event snapshot, so missing catalog rows are rarer than under legacy replay. If both are absent and the payload lacks snapshot fields, the event may quarantine until catalog bootstrap catches up.

**Menu merge — assignment state for a disputed order line:**
```sql
SELECT order_item_id, menu_item_id, variant_id, is_verified, assignment_seq, pending_local
FROM menu_item_variants WHERE order_item_id = '<order_item_id_from_event>';
```
`assignment_seq` = highest applied `server_seq`; `pending_local = 1` = local edit not yet echoed.

**Customer merge — ambiguous resolution:**
```sql
SELECT customer_id, name, phone, address, total_orders, total_spent FROM customers
WHERE lower(trim(name)) = lower(trim('<name_from_event>'));
```
Many rows with the same normalized name and no usable `customer_identity_key` / phone / address hashes → the resolver can't pick a unique source/target.

### 8.4 Support playbook

1. **For a stuck event id, capture:** cloud payload (redacted); local `menu_items` rows for the UUIDs; the `menu_sync_event_quarantine` row (menu) or pull error (customer); count of name-colliding customers; pull cursor values; `GET /api/menu/sync-conflicts` output.
2. **Document expected warnings** in release notes when portable locators are sparse (anonymous customers, duplicate names).
3. **Manual convergence is dangerous and rare** — only with a backup. Prefer engineering fixes over hand-editing.

### 8.5 Live DB & server access

- **Golden live DB:** `~/Library/Application Support/dn-analytics/analytics.db` — **not** the stale repo-root `analytics.db`. Scripts reading it must set `DB_URL` / connect explicitly.
- **Sync scope_key:** `default` — the value stored for the historically original restaurant. Clients never send it: a `scope_key` query parameter is refused with `400 retired_parameter` as of contract 1.2, and the server derives the scope from the required `X-Restaurant-ID` selector.
- **Server (LIVE prod):** `ssh -i ~/.ssh/id_ed25519 root@72.61.242.220`; repo `/home/appuser/dachnonabackend`; host `https://webhooks.db1-prod-dachnona.store`. Docker compose prod bakes code into the image → deploy = `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`; **restart nginx after** or it 502s on the stale web-container IP. Django shell: `... exec -T web python manage.py shell`.

### 8.6 Disaster recovery tiers

1. **One install diverged:** prefer `force_reseed_menu_assignments()` from the client against the server assignment snapshot. Do not run it with unsynced local edits.
2. **Server assignment table suspect:** run server-side `rebuild_order_item_assignments --scope <key>` from the authoritative mutation/event history, then let desktop installs Sync DB.
3. **Server data lost:** restore the server Postgres backup first. If no suitable server backup exists, import a dev-exported JSON baseline with the server `import_menu_assignment_baseline` flow, restore one scratch/known-good desktop DB with `python scripts/seed_from_backups.py --restore --from <dir> --yes`, then re-push/bootstrap from that repaired state. Use `--catalog-only` only when assignment rows must remain server-owned.

---

## 9. Customer-merge divergence (strict mode + replay caveats)

Human customer merge/undo edits are **online-required** when `strict_mode_active` is true on the client — see §14. That path prevents concurrent conflicting merges via server-authorized commits (global `customer_revision` + `customer_keys` overlap check). **Replay pulls** (fresh install, peer convergence, strict-mode recovery) resolve customers via `_resolve_customer_id` (`customer_merge_sync.py`) using `portable_locators` and normalized snapshots. Weak locators (`anon:*` keys, null phone/address hashes, many customers sharing a normalized name) make the resolver return ambiguous; order reassignment drift compounds it. Remediation items are tracked in [pending_task.md](./pending_task.md).

**Anonymous-customer resolution layers (2026-07-14).** `anon:*` identity keys are random per-install UUIDs (`load_orders.py`), so same-name customers without phone/address used to be unresolvable — including by the install that authored the merge, because strict commits self-apply through the same replay applier (`apply_accepted` → `apply_remote_customer_merge_event`). The applier now resolves in layers:
1. **Self-origin exact ids** — when `attribution.device.install_id` matches this install's `sync_install_id`, `local_refs.source/target_customer_id` are trusted only with independent corroboration: the moved orders' unanimous local owner must be one of the hinted customers (source pre-apply; target when the same pair already merged, i.e. a duplicate commit retry). A snapshot-name match alone is NOT sufficient — a reseeded DB reusing the install_id would pass a name check on same-name anonymous customers. No matching local orders, or an owner outside the pair (order-reassignment drift), discards the hints and falls back to portable resolution. Foreign `local_refs` remain ignored (autoincrement `customer_id` is local-only).
2. **Portable `order_refs` locators** — descriptors now carry up to 20 of the customer's order refs (`ORDER_REF_LOCATOR_LIMIT`, pre-merge ownership reconstructed at capture). An anonymous customer is defined by the orders it owns, and order refs (`stream_id`+`event_id`, else `petpooja_order_id`) replicate identically on every install; a unanimous local owner is an exact match.
3. **Moved-orders fallback (source) + two-candidate elimination (target)** — legacy events without descriptor `order_refs` resolve the source via `moved_orders.portable_refs`; the target then resolves only when its name matches exactly two active customers and one is the source. Elimination is gated to name-only descriptors (`_descriptor_has_strong_locators`): a stronger locator that failed to match means the referenced data has not synced yet, so the event quarantines and retries instead of name-guessing. Three or more candidates fail closed.

**Unresolvable replay events quarantine (2026-07-07).** An event the resolver cannot uniquely match no longer halts the pull stream. It raises `UnresolvedMergeEventError` and is quarantined in `customer_merge_unresolved_events` (payload + attempts + last_error); the cursor and scope state keep advancing, and every later pull retries quarantined events first (oldest-first, so a healed applied event is found by its undo). Transient causes (customer not yet created by order sync, ambiguity that collapses after other merges apply) self-heal; permanent ones (name-only locators over duplicate names) stay quarantined and visible: Sync DB reports a non-fatal warning with the count, and `GET /api/sync/customer-rollout-status` exposes `customer_merge_unresolved`. All non-resolution failures (network, HTTP, invalid payload) keep failing closed. **Deliberate deviation from the menu design:** `customer_state_revision` still advances while events are quarantined — a permanently-ambiguous historical event must not disable server-authorized commits; divergence is bounded to the quarantined events. After the 2026-07-14 resolution layers, replay against the live install healed 25/30 quarantined events; 5 stay correctly failed-closed: 2 foreign name-only events whose target name matches 17 resp. 5 active customers, and 3 self-origin "Nirupam Das" cluster events whose moved orders drifted to customers outside the hinted pair with no local merge rows explaining it (order-reassignment drift — hints uncorroborable). Recreate those merges in-app if wanted; the similarity UI merge path now works for same-name anonymous pairs.

---

## 10. Validation record (2026-07-06 sign-off)

- **Fleet convergence:** full server snapshot (600 rows, `watermark_seq=371`) vs the live golden DB after a Sync DB run: **573/573 shared assignments identical, 0 mismatches**, 0 unverified, 0 pending local, quarantine empty. Local-only rows are clustering-created rows no event touched — deterministically re-derivable, not divergence.
- **27 server-only rows — benign.** Authored by a decommissioned April install (`install-d36c7661…`) for `order_item_id`s absent from the current cloud order stream. No install can apply them (the applier skips assignments with no local order backing); `rebuild_order_item_assignments` would resurrect them from the log, so they stay as inert historical coverage.
- **Fresh-install E2E simulation** (pull-only against prod: fresh schema → seed → full import of 16 872 orders → cloud pulls): snapshot seed at watermark, **0 unverified** (Resolutions starts empty), 567/567 shared mapping keys identical, 32 023/32 074 order-line groups place identically. Residual: 51 line-groups across 3 flavor families (legacy-key placements) need a one-time human remap — see [pending_task.md](./pending_task.md). Fresh install correctly does not reproduce the golden DB's ~705 duplicate `order_items` rows.
- **Same-item legacy events** (4 April `mapping_audit_v1`) now apply as recorded no-ops; live quarantine drained 4/4.

---

## 11. Known caveats

- **Writable seed path.** Resolved in the JSON-layer removal work: runtime menu edits and sync epilogues no longer write `data/id_maps_backup.json` or `data/cluster_state_backup.json`. The remaining JSON export/restore path is an explicit dev/disaster-recovery CLI flow, not normal app runtime.
- **Clustering re-verifies via heuristic (C1).** `services/clustering_service.py` sets `is_verified=1` on insert only when the same `(menu_item_id, variant_id)` is already verified on a server-acknowledged row (`verification_seq IS NOT NULL OR assignment_seq IS NOT NULL`). Human verify pulls and snapshot/merge-seeded assignments can quiet known SKUs; purely local C1 cascades cannot mint further verified rows. New/unknown SKUs still insert `is_verified=0`.

---

## 12. Strict server-authoritative mutation mode (implemented)

Human menu edits (merge, undo, remap, resolution, verify, in-place catalog rename/retype, catalog-only verify, standalone variant create) are **online-required** when strict editing is ready on the client (cloud sync configured with a seen `menu_state_revision`). Each edit is captured in a rolled-back SQLite transaction, submitted as a single `POST /desktop-analytics-sync/menu-mutations/commit`, and applied locally only after the server accepts. Stale `expected_menu_revision` returns HTTP **409**; the client auto-pulls and retries non-overlapping assignment edits once. Pure `catalog_update` conflicts fail closed and surface to the user: refresh menu state and retry.

| Component | Location |
|-----------|----------|
| Client commit + reconcile | `src/core/menu_mutation_commit.py` |
| Batch epilogue (shared by commit path and pull path) | `_run_assignment_batch_epilogue` in `src/core/menu_merge_sync.py` |
| Strict-mode gates on edit paths | `utils/menu_utils.py` |
| API 409/503 mapping | `src/api/routers/menu.py` |
| Server commit + idempotency | `db.dachnona` `services/menu_mutation_commit.py` |
| Wire contract | [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §19 |

**Conflict model for human edits:** global `menu_revision` on the server prevents lost updates; the client masks non-overlapping assignment 409s via auto-pull + one silent retry. Replay pulls + assignment snapshots remain for fresh install and recovery (§4). Pure `catalog_update` carries no `order_item_ids`, so it intentionally does not auto-retry on 409. `assignment_rows` in accepted responses are parity-checked only (`check_assignment_parity` in `menu_mutation_commit.py`) — local apply always goes through the pull appliers. `apply_accepted` also runs the same batch epilogue (`_run_assignment_batch_epilogue` — stats recompute, resolution-state sync/GC, forecast-cache clear) that a peer gets by pulling the same event, so the committing install's local state matches what pull would have produced, not just the row-level assignment.

**Catalog-update delivery:** pure `catalog_update` writes no merge/verification events. The server must merge the accepted `catalog_delta` into `MenuBootstrapLatest.id_maps` in the same revision transaction. Peers receive these label/type/variant changes through the routine menu bootstrap pull; no desktop bootstrap push is required for convergence.

**Catalog authority matrix (post JSON removal + catalog_update):**

| Change class | Authority |
|--------------|-----------|
| Merge / undo / remap / resolve / verify with mappings | Strict mutation commit + event streams + assignment snapshot |
| Catalog-only human edits | Strict `catalog_update` / `verify` carrying `catalog_delta`; server merges the delta into `MenuBootstrapLatest.id_maps` |
| Fresh install with an empty catalog | Bootstrap pull seeds `id_maps`, then assignment snapshot + event tails establish clustering state |
| Scheduler bootstrap push | Optional mirror of the local catalog for seed freshness; never the OCC path for peer convergence |
| Machine-derived assignments | `derived_assignment.sync` create-only commit; first server row wins, existing server rows are never overwritten |

Bootstrap pull intentionally uses upserts: existing `menu_items.name` / `menu_items.type` and variant metadata are refreshed from the server snapshot. "Seed-only" means the routine pull does not relink per-order-item assignments from frozen `cluster_state`; it does not mean "insert missing catalog rows only." Bootstrap has no revision OCC semantics of its own — the OCC boundary is the strict mutation commit that produced the catalog delta.

---

## 13. Strict-mode validation record (Phase 7 — 2026-07-06)

Automated coverage for the strict-mode commit design (formerly Phase 7 of the implementation plan). All scenarios below are **green** in CI-style unit tests unless noted. As-built behavior: §12 above.

| # | Scenario | Client test | Server test |
|---|----------|-------------|-------------|
| 1 | Two-install race: stale merge → 409, SQLite unchanged; non-overlapping auto-retry | `tests/test_menu_strict_mode_validation.py::test_phase7_01_*` | `tests.py::test_commit_stale_revision_returns_409_with_order_item_ids` |
| 2 | Undo race: stale undo rejected; merge history unchanged | `test_phase7_02_*` | `MenuMutationPhase7ValidationTests::test_phase7_02_*` |
| 3 | Verification race: stale verify rejected; flag unchanged | `test_phase7_03_*` | `MenuMutationPhase7ValidationTests::test_phase7_03_*` |
| 4 | Network failure: reconcile GET 404 → error, SQLite unchanged | `test_phase7_04_*` | — |
| 5 | POST timeout + GET 200 reconcile → apply, no duplicate | `test_menu_mutation_commit.py::test_timeout_status_200_applies` | `test_status_returns_stored_response_or_404` |
| 7 | Accepted mutation parity (`assignment_rows` vs local) | `test_phase7_07_*` | `test_phase7_07_assignment_rows_match_server_assignments` |
| 8 | Sync DB: catalog bootstrap before orders | `test_phase7_08_*` | — |
| 9 | Sync DB: menu pull failure surfaces error | `test_phase7_09_*` | — |
| 11 | Duplicate concurrent commit (same `mutation_id`) | — | `MenuMutationConcurrentCommitTests` (Postgres only; skipped on SQLite) |
| 12 | Orphaned accepted mutation converges on next pull | `test_phase7_12_*` | — |
| 13 | Self-apply parity: commit path ≡ peer pull path (row-level; `menu_item_variants` + `merge_history`) | `test_phase7_13_*`, `test_build_payload_in_uncommitted_txn_matches_pull_apply` | — |
| 14 | Self-apply parity: batch epilogue (stats/GC/forecast-cache) also matches peer pull, not just the row-level assignment | `test_menu_mutation_commit.py::test_commit_200_runs_batch_epilogue` | — |

**Run commands:**

```bash
# analytics (client)
python3 -m unittest tests.test_menu_strict_mode_validation tests.test_menu_mutation_commit tests.test_menu_edit_api tests.test_menu_rollout

# db.dachnona (server)
cd backend && DJANGO_SETTINGS_MODULE=config.settings_desktop_sync_test \
  python3 manage.py test desktop_analytics_app_sync.tests.MenuMutationPhase7ValidationTests \
  desktop_analytics_app_sync.tests.MenuMutationCommitPhase2Tests
```

---

## 14. Customer strict server-authoritative mutation mode (implemented)

Human customer merge/undo edits are **online-required**. `strict_mode_active` now means only that cloud sync is configured and the profile has seen a `customer_state_revision`; there is no server-provided strict-mode flag.

> **Contract 1.2 (server live, desktop Phase 1 implemented 2026-08-08):** the server no longer sends `strict_mode_enabled` on any response — menu or customer — and the per-scope gate column is gone; sync is strict-only. The client does not parse or mirror that retired field. Each edit is captured in a rolled-back SQLite transaction, submitted as a single scoped `POST /desktop-analytics-sync/customer-mutations/commit`, and applied locally only after the server accepts. Stale `expected_customer_revision` returns HTTP **409**; the client auto-pulls and retries non-overlapping edits once (keyed on `customer_keys`).

| Component | Location |
|-----------|----------|
| Client commit + reconcile | `src/core/customer_mutation_commit.py` |
| Strict-mode gates on edit paths | `src/core/queries/customer_merge_queries.py` |
| API 409/503 mapping | `src/api/routers/orders.py` |
| Server commit + idempotency | `db.dachnona` `services/customer_mutation_commit.py` |
| Replay quarantine (unresolvable events, §9) | `src/core/customer_merge_sync.py` — `UnresolvedMergeEventError`, `customer_merge_unresolved_events` table |
| Wire contract | [central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md) §21 |

**Conflict model for human edits:** global `customer_revision` on the server prevents lost updates; the client masks non-overlapping 409s via auto-pull + one silent retry keyed on `customer_keys`. Full event replay from a null cursor remains the fresh-install convergence path (no materialized customer table on the server). Replay events the local resolver cannot match are quarantined, not fatal — see §9.

**Strict mode is live in prod since 2026-07-07.** Live validation: client `strict_mode_active = true`, a real merge + undo round-tripped through `POST /customer-mutations/commit` (revision 58 → 59 → 60), zero outbox rows written, local state restored after the undo, merge history row applied via the server-accepted event (`sync_origin = cloud_pull`, `remote_event_id` recorded).

---

## 15. Customer strict-mode validation record (Phase 7 — 2026-07-06)

Automated coverage for the customer strict-mode commit design (formerly Phase 7 of the implementation plan). All scenarios below are **green** in CI-style unit tests unless noted. As-built behavior: §14 above.

| # | Scenario | Client test | Server test |
|---|----------|-------------|-------------|
| 1 | Two-install race: stale merge → 409, SQLite unchanged; non-overlapping auto-retry | `tests/test_customer_strict_mode_validation.py::test_phase7_01_*` | `CustomerMutationPhase7ValidationTests::test_phase7_01_*` |
| 2 | Overlapping race: concurrent merge on same `customer_keys` → conflict surfaced | `test_phase7_02_*` | — |
| 3 | Undo race: stale undo rejected; merge history unchanged | `test_phase7_03_*` | `CustomerMutationPhase7ValidationTests::test_phase7_02_*` |
| 4 | Network failure: reconcile GET 404 → re-POST fails → error, SQLite unchanged | `test_phase7_04_*` | — |
| 5 | POST timeout + GET 200 reconcile → apply, no duplicate | `test_phase7_05_*`, `test_customer_mutation_commit.py::test_timeout_status_200_applies` | `CustomerMutationCommitPhase2Tests::test_status_returns_stored_response_or_404` |
| 7 | Fresh install / reset: full stream replay matches reference install | `test_phase7_07_*` | — |
| 8 | Sync DB: customer pull failure surfaces error | `test_phase7_08_*`, `test_sync_operations.py::test_iter_sync_statuses_marks_customer_pull_failure_as_terminal_error` | — |
| 9 | Self-apply parity: commit path ≡ peer pull path | `test_phase7_09_*`, `test_build_payload_in_uncommitted_txn_matches_pull_apply` | — |
| 10 | Orphaned accepted mutation converges on next pull | `test_phase7_10_*` | — |
| — | Duplicate concurrent commit (same `mutation_id`) | — | `CustomerMutationConcurrentCommitTests` (Postgres only; skipped on SQLite) |
| 11 | Unresolvable replay event quarantined; stream + scope state continue; retry heals; non-resolution errors fail closed (added 2026-07-07) | `tests/test_customer_unresolved_quarantine.py` (7 tests), `test_sync_operations.py::test_iter_sync_statuses_keeps_done_when_customer_pull_only_has_unresolved_warning` | — |

**Live prod validation (2026-07-07):** first Sync DB after the strict flip quarantined the two known unresolvable events and converged (58 events → 12 applied, 44 deduped, 2 quarantined, revision 58 mirrored); merge + undo round-trip `58 → 59 → 60`; menu round-trip `371 → 372 → 373`.

**Run commands:**

```bash
# analytics (client)
python3 -m unittest tests.test_customer_strict_mode_validation tests.test_customer_mutation_commit tests.test_customer_edit_api tests.test_customer_rollout tests.test_sync_operations

# db.dachnona (server)
cd backend && DJANGO_SETTINGS_MODULE=config.settings_desktop_sync_test \
  python3 manage.py test desktop_analytics_app_sync.tests.CustomerMutationPhase7ValidationTests \
  desktop_analytics_app_sync.tests.CustomerMutationCommitPhase2Tests
```

## 16. JSON removal + Part B validation record (2026-07-10, live prod, single install)

Closes the two-store convergence work: JSON backup layer removed from all runtime paths (Part A), and the remaining cross-install divergence gaps closed via `catalog_update`, `derived_assignment.sync` create-only flush, the C1 verify gate, and the itemcode `route_eligible` gate (Part B). Fleet at validation time was exactly one desktop install; two-install correctness paths were exercised by unit suites, and live behavior was validated against the prod server after a server deploy + desktop orders reset + Sync DB.

**Local suite:** full `unittest discover tests` — 304 OK; JSON-removal surface (`test_seed_from_backups`, `test_menu_catalog_seed`, `test_menu_bootstrap_pull`, `test_menu_assignment_bootstrap`, `test_client_learning_shipper`) — 19 OK. Runtime has zero `export_to_backups` calls in `src/`/`utils/`/`services/`; seeding moved to `src/core/menu_catalog_seed.py`; `installer/backend.spec` no longer bundles `data/*.json`.

**Live checks (prod, 2026-07-10):**

1. **Derived flush** — one `derived_assignment.sync` (3 POS-backed assignments) accepted at revision 430 → 430 (no bump), 0 `skipped_existing`, all 3 keys acked (`assignment_seq` stamped) on the client; post-flush candidate rows = 0. Exclusions correct: 102 catalog stubs, 105 name-keyed rows without local POS backing, 1 dead itemid — none flushed.
2. **Catalog commits** — two retypes round-tripped, revision 430 → 433; server merged each `catalog_delta` into `MenuCatalogItem` + refreshed `MenuBootstrapLatest.id_maps` in the commit transaction. (Retype rekeys the deterministic item id, so it ships as a merge event; pure `catalog_update` fires for zero-relink ops.)
3. **Catalog OCC 409 (wire)** — `catalog_update` at stale `expected_menu_revision=1` → HTTP 409 with attribution, nothing persisted (no `MenuMutationLog` row, revision unchanged).
4. **C1 gate** — all 102 verified-but-unacked local rows are catalog stubs (allowed seed exception); every machine-derived row is `is_verified=0`; client/server verified-flag mismatch across 461 shared keys = 0.
5. **Itemcode gate (9.2)** — 101/102 mappings `route_eligible=1`; the only ineligible one is the known `Orange Ice cream (wi` conflict (`status=conflict`, `route_eligible=0`) — manual UI resolve pending.
6. **Convergence** — 461 shared assignment keys; 6 mapping mismatches, all pre-existing legacy-key flavor families (unbacked name-keys, tracked open item), not Part B regressions.

**Findings fixed during validation:**

- **`apply_id_maps` blanking bug** — bootstrap mirror-push ingest blanked `MenuCatalogItem.item_type` and forced `is_verified=True` on every pushed id (live push blanked 203 rows). Fixed in `catalog_state.py` (preserve-when-absent) + regression test `test_bootstrap_ingest_preserves_item_type_and_verified_flag`; prod data repaired from client truth (154 rows), ~50 historical orphans keep `''` (no client truth; inert).
- **Rekey orphans** — retype/merge leaves the old `menu_item_id` in `MenuCatalogItem` and merge-forward id_maps republishes it. No client zombie resurrection: `seed_catalog` skips id_maps entries with no `cluster_state` key (live: `skipped_unmapped=139`). Server-side orphan pruning tracked in `pending_task.md` §2g.
- **Zero-backed item retype edge** — retyping an item with 0 mappings/0 orders GC'd the target row locally so the revert 404'd; restored manually. Tracked in `pending_task.md` §2h.

---

## 17. Global menu groups (revision 1.6 client — implemented, dormant)

Restaurants that share one business menu belong to a server-managed
`menu_group_id`. The **menu group**, not a restaurant, owns canonical item and
variant identity, canonical labels/types, verified mapping rules and aliases,
merge redirects and undo history, and the menu mutation revision. Orders,
customers, forecasts, prices, availability, POS locators and the per-restaurant
assignment materialization stay restaurant-specific. `X-Restaurant-ID` remains
mandatory: it authorizes the request, names the origin restaurant, and selects
restaurant-scoped rows; the server derives the group, and clients never send or
select one. The wire shape is frozen in
[central_server_analytics_app_api_contract.md](../contracts/central_server_analytics_app_api_contract.md)
§25 (revision 1.6).

As of 2026-08-10, the desktop contains an additive projection and an adapter for
the frozen revision 1.6 global-menu shapes. Client and central implementation are
complete; the client mutation/resolution workflows and the central P2.7 hardening
gate are done. This section records a complete dormant client, **not** production
activation — the rollout remains open (§17.3). The production registry advertises
no menu-group capability, so the resolver selects `legacy_restaurant_v1` and
existing menu behavior remains unchanged. `SYSTEM_CONTEXT.md` and
`SESSION_AND_ALL_STORES.md` Part B deliberately still describe legacy
restaurant-scoped behavior as what runs for that reason.

Activation is fail-closed and has one owner:
`src/core/global_menu_schema.py::resolve_global_menu_capability`. It requires an
authorized and selected physical restaurant, an exact registered database path,
a non-blank server-managed `menu_group_id`, local schema version 1, and the base
`global_menu_v1` capability. Removing capability during an allowed-restaurants
refresh clears cached enablement immediately. The four advertised capabilities
are kept distinct:

- `global_menu_v1`: pull/cache the projection and report coverage; legacy reads,
  ingest resolution, and writes remain live.
- `global_menu_resolution_v1`: allow only canonical create, locator-map, and
  matching undo writes needed to repair coverage during shadow/aggregation.
- `global_menu_aggregation_v1`: additionally allow global-ID All Stores
  aggregation after every participating profile passes its local coverage and
  quarantine gate.
- `global_menu_mutations_v1`: additionally use global resolution and author
  global mutations after the selected profile passes the same gate. All Stores
  remains read-only.

The dormant revision-1.6 client sequence is:

1. Pull the unpaged status document, then page each authoritative snapshot
   section (`items`, `variants`, `redirects`, `rules`) independently. Tail
   semantic events by `event_seq`; compact event pages are retained for history
   and followed by an authoritative resnapshot. Cross-group/cyclic state is
   quarantined without advancing the prior good cursor.
2. In every advertised rung, pull/cache global state before POS order ingest.
   Shadow and aggregation advertise narrow resolution writes while unrestricted
   global edits stay disabled. Resolution uses linked assignments, restaurant POS
   rules, approved group itemcodes, approved aliases, then the existing unverified
   suggestion path. The Resolution tab also surfaces verified assignments missing
   global item/variant identity; it previews and commits canonical creates and all
   trusted restaurant POS locators, using a separately confirmed group alias only
   when no POS locator exists. Fuzzy matches never create global authority.
3. After order ingest, pull the existing restaurant-filtered assignment snapshot
   with its additive global IDs. Preserve restaurant price and eligibility,
   respect `last_seq` and `last_verification_seq`, and reuse the current stat,
   forecast-cache, and husk-sweep epilogues.
4. For a global edit in the provisional adapter, require
   `global_menu_mutations_v1`, complete local
   coverage, one physical origin restaurant, and the separate configured editor
   credential sent as `X-Global-Menu-Key`. Preview and commit use the frozen
   `mutation_type`/`payload` envelope, stable global IDs, group OCC revision, and
   `preview_digest`. A POST timeout is reconciled through mutation status and is
   never blindly repeated.
5. Enable global-ID All Stores grouping only with
   `global_menu_aggregation_v1` and complete identity coverage. Unlinked rows
   remain explicitly store-qualified; without that rung the existing
   name/type/variant reducer remains active.

The frozen revision 1.6 payloads are in
`contracts/fixtures/1/global_menu_fixtures.json` and are byte-identical to the
central-repository copy.

### 17.1 Invariants that govern any change here

1. **Stable identity** — a global item/variant ID is server-issued and never
   derived from a mutable display name. Rename changes metadata, not identity.
2. **One authority** — global catalog, redirects, mapping rules and the group
   revision are central-server truth; SQLite is a cache/projection.
3. **No cross-domain scope leak** — sharing a menu never shares customers,
   orders, forecasts, restaurant prices or availability.
4. **Restaurant-qualified POS keys by default** — a POS item/addon ID is unique
   only within a restaurant unless a reviewed rule makes an itemcode or alias
   explicitly group-wide.
5. **No fuzzy auto-merge** — normalization/fuzzy matching may propose a link but
   can never create global authority without a human or a trusted server rule.
6. **Atomic global mutation** — commit, redirect/rule change, audit row and
   revision advance land in one central transaction; mutation IDs are unique per
   group and commits serialize under an expected group revision (OCC).
7. **Profile isolation remains** — one restaurant and one
   `restaurant_profile_identity` per local database.
8. **Undo is a new authoritative mutation**, never history editing or a client
   backup restore. The server refuses out-of-order undo
   (`undo_not_latest_mutation`) and refuses when a later mutation overwrote the
   provenance marks it would need (`undo_effects_superseded`).
9. **No partial All Stores lie** — rows without global identity stay visibly
   store-qualified and are never silently name-grouped.
10. **Rebuild only derived state** — order facts and local projections are
    regenerable; global IDs, reviewed equivalences, rules, redirects and audit
    attribution are central authority and cannot be reconstructed from raw order
    rows.

### 17.2 Lifecycle rungs

The group climbs `provisioning` → `shadow` → `aggregating` → `active`, one rung
at a time (rollback is unrestricted), each rung mapping onto the capability
ladder above so "enable All Stores global reads" and "enable global mutations"
stay separate, reversible operator decisions.

### 17.3 Activation stop gate (open)

Nothing is deployed or enabled. Until activation starts,
`GET /analytics/restaurants/` reports `menu_group_id: null` and
`menu_capabilities: []`, and every `/global-menu/` route answers
`400 global_menu_not_enabled`. The operator procedure is owned by the central repository (`db.dachnona`) —
that runbook is the authoritative gate; do not activate from this doc.
Summary of what it requires, in order: maintenance window and recoverable
snapshot → deploy central code/schema with capability off → reconciliation
dry-run with every blocking ambiguity resolved and the digest archived →
atomic bootstrap apply → wipe and re-bootstrap the sole client's projections →
**shadow mode** as a correctness smoke gate → 100% global identity coverage on
referenced verified assignments in every member, zero unresolved hard conflicts,
matching client/server digests → enable global-ID All Stores reads → enable
global mutations for a restricted role → live-test merge/rename/new-locator/undo
→ widen permissions. Before the first accepted global mutation a failed
bootstrap may be discarded and rerun from the preserved seed; **after** it,
global state is authority and rollback only disables capability.

Two production characteristics that shaped the bootstrap and must not be
re-litigated casually: the backfill replays each member's stored
`menu_merge.applied` history as canonical redirects by `menu_item_id` (never by
name), because linking only live catalog rows would republish already-merged-away
products as canonical in every member; and the deployment-only authority cutover
(`--authority-restaurant` with `--reset-replay-restaurant`) derives canonical
state from one member and replays the other's assignments from its own stored
mutation logs through the live ingest apply functions.

### 17.4 Release-blocking verification scenarios

1. Different POS IDs, one product — two restaurants' distinct POS IDs map to one
   global item and one All Stores row.
2. Same POS ID, different products — restaurant qualification prevents the
   collision unless a reviewed group rule says otherwise.
3. Global rename — canonical label changes everywhere without changing the global
   ID or splitting historical analytics.
4. Global merge — every known locator and assignment follows the redirect.
5. Future item — a newly seen POS ID with an approved group itemcode/alias
   resolves to the merge target automatically.
6. Unknown item — no trusted rule means unverified resolution, never fuzzy
   auto-assignment.
7. Variant conflict — incompatible units/values block commit until explicitly
   reconciled.
8. Concurrent editors — stale preview/expected revision returns a non-destructive
   conflict.
9. POST timeout — status lookup returns the accepted result without a duplicate
   mutation.
10. Offline/stale client — cannot author a global mutation; converges by snapshot
    plus tail on reconnect.
11. Undo — restored rules/redirect state and assignments converge everywhere.
12. Scope isolation — global menu sharing never merges customers, orders, prices,
    availability or forecasts.

### 17.5 Rejected shortcuts

Repeating a restaurant-scoped merge per profile from the client; mapping several
restaurants onto the generic `scope_key`; treating name-derived local IDs as
global identity; name-grouping All Stores rows once global mode starts; assuming
Petpooja item IDs are chain-global; deleting a merge source without a persistent
redirect; letting fuzzy matching create group-wide rules; enabling global
controls before central capability and coverage are valid; editing one repository's
copy of the shared contract or fixtures.

**Known open design question:** `GlobalVariant` has no owning item — membership is
inferred from assignments, which misses never-sold variants and weakens merge-time
conflict detection. A plain foreign key would be wrong if a variant like "Large" is
reusable across items; an explicit association table is the likely shape. Undecided.
