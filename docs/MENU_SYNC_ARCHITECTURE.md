# Menu clustering sync — architecture, database schema & operational runbook

**Audience:** Engineers on the desktop analytics client (this repo) and the Dachnona cloud (`db.dachnona`, Django app `desktop_analytics_app_sync`).
**Status:** As-built and signed off (2026-07-06). This is the durable reference for how menu clustering converges across installs. It replaces the two implementation plans that drove the work (`MENU_MERGE_CONFLICT_SYNC_PLAN.md`, `MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md`) — those were phase-by-phase build plans and have been removed now that the work shipped. Open follow-ups live in [pending_task.md](./pending_task.md).
**Related:** [INDEX.md](./INDEX.md) (doc hub), [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) (general architecture), [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) §17 (wire contract — authoritative for on-the-wire format).

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

- `SCHEMA_VERSION = 2`. Legacy v1 events remain convertible: the applier derives assignments from `history_payload` (`mapping_rows` for variant/resolution kinds; `affected_order_item_ids` + `target_id` for basic merges). Extraction is shared with the server and pinned by `contracts/menu_merge_event_fixtures.json` in **both** repos (source of truth in this repo).
- **Key-omission convention:** a normalized assignment always carries `order_item_id` and `menu_item_id`; `variant_id` / `is_verified` are **omitted** when the event doesn't specify them (v1 basic-merge derivation), and the applier then leaves the current row value untouched. `variant_id: null` (or `__NULL_VARIANT__`) means the SQL NULL variant. v2 emit always includes both keys.
- **`is_verified` is create-only on the merge stream:** written only when materializing a previously nonexistent row, never on UPDATE (the verification stream owns it — §2.2 rule 6).
- `menu_merge.undone` carries the **prior** assignments; an undo is just another LWW write at its own `server_seq` — no special casing.
- **Same-item events** (legacy `mapping_audit_v1` addon consolidations where source `menu_item_id` == target, carrying no derivable assignments) are recorded as **applied no-ops** rather than replayed — see `_is_self_merge_event` / `_record_self_merge_noop` in `menu_merge_sync.py`. Matches the server materializer, which skips underivable events.

### 2.4 Client apply algorithm

For each pulled event, in stream order (`menu_assignment_apply.apply_assignments`):

1. Skip if `remote_event_id` already recorded (dedupe). If it matches an existing local merge (`_find_matching_local_merge`, ignoring `origin='remote'` history rows), stamp `server_seq` / clear `pending_local` on its rows without inserting a new history row. If it's the echo of our own event and a row was meanwhile outranked, emit a "your resolution of X was superseded by {attribution}" notice.
2. Per assignment: `ensure_menu_item_exists` / `ensure_variant_exists` from the event snapshot; if the local row's `assignment_seq` ≥ event's `server_seq`, skip (stale). If the row is `pending_local`, apply the remote value **silently** (server order will judge). Otherwise UPDATE `menu_item_variants` + `order_items` + `order_item_addons` to the target, set `assignment_seq = server_seq`, `pending_local = 0`. Rows whose `order_item_id` has no local `order_items` row are skipped (this install never saw that order).
3. Batch epilogue: GC menu items left with zero mappings, recompute stats + resolution state for touched items, clear forecast caches, one `export_to_backups`.
4. Record a `merge_history` row per applied event with `origin='remote'` so Resolution History and undo keep working.

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
scope_key          # tenant/store scope (default: "default")
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

- `apply_menu_merge_event_to_assignments(event_row)` upserts each row iff `event_row.id > last_seq`; verification events update the flag only under `id > last_verification_seq`. Both run inside the ingest transaction.
- Migrations 0011 (indexes + v2 cursors + per-event ingest), 0012 (`OrderItemAssignment`), 0013 (`last_verification_seq`) are **applied to live prod** and the `rebuild_order_item_assignments --scope <key>` backfill has been run (it is also the recovery tool if the table is ever suspect).
- **Bootstrap demotion:** `menu_bootstrap_ingest` stops overwriting `MenuBootstrapLatest.cluster_state` when a payload carries `snapshot_role: "seed_only"` (current clients) or the ops toggle `MENU_BOOTSTRAP_TRUST_CLIENT_CLUSTER_STATE=false` is set. `id_maps` (catalog metadata) still merges forward. The stored cluster_state is deliberately *frozen*, not regenerated from `OrderItemAssignment` — assignments are the authoritative clustering source; the cluster_state format carries extra per-row detail no current client consumes.

---

## 4. Event streams & pull order

Pull order (in `src/core/services/cloud_pull_orchestrator.py` — keep code and this doc in sync):

1. **Menu bootstrap** — broad catalog / `id_maps` + `cluster_state`; seed-only by default.
2. **Menu assignments snapshot** — one-time fresh-install seed (§5), after the catalog exists, before event tails.
3. **Menu mapping verification events** — per-line `is_verified` on `menu_item_variants`.
4. **Menu merge events** — structural merges / resolution history.
5. **Customer merges.**

Pulls are **best-effort**: per-stream failures are logged (and, for menu streams, quarantined), never raised, so the Sync DB job finishes "successfully" while individual events may fail. Menu-stream cursors advance past quarantined events; the customer stream still uses older batch semantics (a top-level exception leaves its cursor unchanged).

**Resilience:** both menu pull loops quarantine per-event failures and keep advancing the cursor, with a retry pass at the start of each pull. Shippers parse the server's per-event `accepted`/`rejected` lists (`sync_push_results.py`); rejected events are marked errored + quarantined so one poison event can't freeze the push queue. A background pull runs after the scheduler's push phase each cycle, under a process-wide `CLOUD_PULL_LOCK` shared with the Sync DB job; local merge/resolution/undo commits fire a push nudge (`menu_merge_push_nudge.py`).

---

## 5. Fresh-install fast path

`src/core/menu_assignment_bootstrap.py`, run from the pull orchestrator after catalog bootstrap:

- If `menu_assignments_bootstrapped` is unset **and** no menu-merge pull cursor exists (truly fresh), page `GET /desktop-analytics-sync/menu-assignments/snapshot?after=&limit=`, apply rows through the same seq-guarded applier at `server_seq = watermark_seq`, set the menu-merge pull cursor to the server-provided `watermark_cursor` (and the verification cursor to `verification_watermark_cursor`), mark bootstrapped. No history replay → cannot hit the old fresh-install conflict bug.
- Installs that already have a pull cursor are marked bootstrapped **without** reseeding (their state came from replay).
- Fetch errors leave the flag unset so the next pull retries.
- **Escape hatch:** `force_reseed_menu_assignments()` (client) + the `import_menu_assignment_baseline` management command (server) overwrite an install's cluster state from the snapshot regardless of prior state — for a coordinated cutover (edits frozen) or to recover a single diverged install. It overwrites pending local edits, so never run it on a machine with unsynced local work.

Bootstrap shipper (`menu_bootstrap_shipper.py`) sends `snapshot_role: "seed_only"` and skips pushes while the `id_maps` sha256 (`data/menu_bootstrap_last_push_hash.txt`) is unchanged.

---

## 6. Key files

| Topic | Location |
|-------|----------|
| Assignment extraction + seq-guarded apply | `src/core/menu_assignment_apply.py` |
| Menu merge pull/apply (routing, echo/ack, no-op) | `src/core/menu_merge_sync.py` |
| Fresh-install assignment snapshot seed | `src/core/menu_assignment_bootstrap.py` |
| Local JSON backup seed/restore (catalog auto-seed always; assignments are contingency-only, via CLI or explicit `seed_and_relink_orders`) | `scripts/seed_from_backups.py` |
| Assignment schema (conditional ALTERs) | `src/core/menu_assignment_schema.py` |
| Mapping verification emit / push / pull | `src/core/menu_mapping_verification_sync*.py`, `..._shipper.py` |
| Quarantine helpers | `src/core/menu_sync_quarantine.py` |
| Push result parsing | `src/core/sync_push_results.py` |
| Cursor migration (one-time reset) | `src/core/sync_cursor_migration.py` |
| Sync conflicts API | `src/api/routers/menu.py` (`/api/menu/sync-conflicts`) |
| Best-effort pull orchestration | `src/core/services/cloud_pull_orchestrator.py` |
| Background pull scheduler + nudge | `src/core/services/cloud_sync_scheduler.py`, `src/core/menu_merge_push_nudge.py` |
| Local edit paths (stamp pending_local) | `utils/menu_utils.py` |
| Shared extraction fixtures | `contracts/menu_merge_event_fixtures.json` (both repos) |
| Server: materializer + backfill | `db.dachnona backend/desktop_analytics_app_sync/services/assignment_state.py`, `rebuild_order_item_assignments` |
| Server: snapshot endpoint | `.../views/menu_assignments.py` |
| Server: wire contract | [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) §17 |

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
- **Sync scope_key:** `default`.
- **Server (LIVE prod):** `ssh -i ~/.ssh/id_ed25519 root@72.61.242.220`; repo `/home/appuser/dachnonabackend`; host `https://webhooks.db1-prod-dachnona.store`. Docker compose prod bakes code into the image → deploy = `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`; **restart nginx after** or it 502s on the stale web-container IP. Django shell: `... exec -T web python manage.py shell`.

---

## 9. Customer-merge divergence (open)

The menu redesign deliberately excludes customer merge conflict semantics. Customer merges inherit the server ingest-ordering fix automatically, but have **no** assignment-based applier and **no** quarantine parity with the menu streams.

Replay resolves customers via `_resolve_customer_id` (`customer_merge_sync.py`) using `portable_locators` and normalized snapshots — not another machine's `local_refs.source_customer_id` (autoincrement `customer_id` is local-only). Weak locators (`anon:*` keys, null phone/address hashes, many customers sharing a normalized name) make the resolver return ambiguous and raise. Order reassignment drift compounds it. Remediation items are tracked in [pending_task.md](./pending_task.md).

---

## 10. Validation record (2026-07-06 sign-off)

- **Fleet convergence:** full server snapshot (600 rows, `watermark_seq=371`) vs the live golden DB after a Sync DB run: **573/573 shared assignments identical, 0 mismatches**, 0 unverified, 0 pending local, quarantine empty. Local-only rows are clustering-created rows no event touched — deterministically re-derivable, not divergence.
- **27 server-only rows — benign.** Authored by a decommissioned April install (`install-d36c7661…`) for `order_item_id`s absent from the current cloud order stream. No install can apply them (the applier skips assignments with no local order backing); `rebuild_order_item_assignments` would resurrect them from the log, so they stay as inert historical coverage.
- **Fresh-install E2E simulation** (pull-only against prod: fresh schema → seed → full import of 16 872 orders → cloud pulls): snapshot seed at watermark, **0 unverified** (Resolutions starts empty), 567/567 shared mapping keys identical, 32 023/32 074 order-line groups place identically. Residual: 51 line-groups across 3 flavor families (legacy-key placements) need a one-time human remap — see [pending_task.md](./pending_task.md). Fresh install correctly does not reproduce the golden DB's ~705 duplicate `order_items` rows.
- **Same-item legacy events** (4 April `mapping_audit_v1`) now apply as recorded no-ops; live quarantine drained 4/4.

---

## 11. Known caveats

- **Writable seed path.** `export_to_backups` uses `get_resource_path("data")`, which under PyInstaller points at `_internal/data` inside the `.app` and can **mutate the bundled seed** after install. A separate initiative should move writable exports to `get_data_path("data")` next to `analytics.db` so reset + seed always reads pristine packaged JSON. Ref: `src/core/utils/path_helper.py`. Tracked in [pending_task.md](./pending_task.md).
- **Clustering re-verifies via heuristic (C1).** `services/clustering_service.py` sets `is_verified=1` on insert when `(menu_item_id, variant_id)` is already verified elsewhere, so POS re-import of a known SKU doesn't reopen resolved work. New/unknown SKUs still insert `is_verified=0`.
