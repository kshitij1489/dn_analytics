# Menu merge conflict resolution & multi-install convergence — implementation plan

**Audience:** Engineers working on desktop analytics (this repo) and Dachnona cloud (`db.dachnona`).
**Status:** Phases S1, C1, C2 implemented (2026-07-05, branch `sync-conflict-phase-1` in both repos). Phases C3, S2, C4, C5 implemented (2026-07-05, branch `sync-conflict-phase-2` in both repos; server migration 0012 created but not applied to any real DB). C6/S3 (convergence digest) not started.
**Related:** [CLOUD_SYNC_DIVERGENCE.md](./CLOUD_SYNC_DIVERGENCE.md) (symptom analysis), [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md) (verification-event groundwork, partially implemented), [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md).

---

## 1. Problem statement

Multiple installs edit menu clustering concurrently (merges, variant merges, per-order-item resolutions). Today the cloud is a **passive append-only log plus a last-push-wins snapshot slot** — it records decisions but never *decides*. The result is that conflicting decisions produce **permanent, silent split-brain** rather than a deterministic winner.

### 1.1 Issue inventory (all must be addressed)

| # | Issue | Where | Effect |
|---|-------|-------|--------|
| I1 | Conflicting `resolution_variant_v1` replay fails on the peer ("Source variant was not found"), is logged, counted in `events_failed`, and the cursor advances past it | `src/core/menu_merge_sync.py` (`pull_and_apply_menu_merge_events`, exception path) | Each install keeps its own answer forever; nobody is told |
| I2 | Conflicting `basic_merge_v1` replay "succeeds" as a no-op: `_ensure_menu_item_exists` resurrects the deleted source from the event snapshot, but order items stay wherever the local user put them | `src/core/menu_merge_sync.py` (`_apply_remote_merge_event`) | Silent divergence with a green status |
| I3 | Server replay stream is ordered by client-supplied `occurred_at` first | `db.dachnona backend/desktop_analytics_app_sync/services/merge_events.py` (`_get_merge_delta`, `_encode_cursor`) | An install that was offline ships events with old `occurred_at`; they sort *before* positions other clients' cursors already passed → **those events are never delivered to peers** |
| I4 | Fresh install replays the full log; on a conflict the first event (by client wall clock) applies and the second fails | Same as I1/I3 | A third divergent outcome: machine 1 has A, machine 2 has B, fresh installs get "whichever `occurred_at` is earlier" |
| I5 | Mapping-verification apply only writes the `is_verified` flag, never the `menu_item_id`/`variant_id` carried in the event | `src/core/menu_mapping_verification_sync.py` (`_apply_verification_by_order_item_id`) | The stream cannot correct a divergent mapping; both installs "verify" different mappings |
| I6 | Menu bootstrap snapshot is a full local dump (`export_to_backups` after every merge), pushed every 5 min, server keeps "latest" (last pusher wins), and every Sync DB **relinks `order_items`** from it — but not `menu_item_variants` | `src/core/menu_bootstrap_shipper.py`, `scripts/seed_from_backups.py` (`export_to_backups`), `src/core/menu_bootstrap_sync.py` (`_relink_order_items_from_snapshot`), server `menu_bootstrap_ingest` | Oscillating clobber between divergent installs + **internal inconsistency** (`order_items` says A, `menu_item_variants` says B) |
| I7 | Push poison pill: one invalid event in a batch → server 400 → the whole batch is retried forever, freezing the push stream | `src/core/menu_merge_shipper.py` (`upload_pending`), server `MergeIngestSerializer` (all-or-nothing) | Install stops publishing merges permanently |
| I8 | Verification-pull poison pill: any per-event exception aborts the pull **without advancing the cursor** | `src/core/menu_mapping_verification_sync.py` (`pull_and_apply_...`, exception path) | Stream stalls forever on one bad event (opposite failure mode of the merge pull — neither is right) |
| I9 | No retry / dead-letter / user surfacing for failed events anywhere | Both pull loops | Conflicts and bugs disappear into `logger.warning` |
| I10 | Remote undo needs the original remote merge to have applied locally; in a conflict it didn't, so the undo also fails silently | `src/core/menu_merge_sync.py` (`_apply_remote_undo_event`) | Undo never converges across installs in exactly the cases where it matters |
| I11 | No background pull — only push runs on the 5-minute scheduler; pull happens only on the Sync DB button | `src/core/services/cloud_sync_scheduler.py`, `src/api/routers/operations.py` | Long conflict windows; installs drift for days |
| I12 | `occurred_at` = SQLite `CURRENT_TIMESTAMP` (naive UTC string); server `make_aware(..., get_default_timezone())` | client `merge_history.merged_at` → server `_parse_contract_datetime` | If server default TZ ≠ UTC, timestamps skew by hours (compounds I3) |
| I13 | Concurrent edits during the push/pull window have no deterministic winner and no loser notification | Whole pipeline | The race the design must make *benign*, not prevent |

### 1.2 Root cause

Merge/resolution replay is **cluster-shaped** ("move source cluster into target") while the durable truth is **row-shaped** (which `menu_item_id`/`variant_id` each `order_item_id` belongs to). Cluster-shaped replay is non-commutative and fails when the peer's state differs. Row-shaped writes with a total order are commutative-resolvable: last write per `order_item_id` wins, identically on every machine.

---

## 2. Target design

### 2.1 Core decisions

1. **Unit of truth = per-order-item assignment**: `order_item_id → (menu_item_id, variant_id, is_verified)`. Cluster membership is the aggregation of these rows. (This is the "menu item variant cluster in sync" the product needs.)
2. **Authority = server ingestion order.** The server assigns a monotonic sequence (`server_seq` = the event row's auto-increment `id`) at ingest. All installs apply events in `(ingested_at, id)` order and resolve conflicts per `order_item_id` by highest `server_seq`. Client clocks (`occurred_at`) become display-only metadata.
3. **The central server materializes ground truth.** A new `order_item_assignments` table on Dachnona is updated as events are ingested, using the same per-key LWW rule. This is the queryable ground truth the central team owns, and the fast bootstrap path for fresh installs.
4. **Conflicts are resolved, not prevented.** Pushing "immediately" only shrinks the window (we already push ≤5 min); offline installs always reopen it. Determinism must not depend on timing.
5. **Nothing is silently dropped.** Events that cannot apply go to a quarantine table with UI surfacing; superseded local decisions produce a user-visible notice.
6. **The bootstrap snapshot is demoted** to first-install seeding / explicit restore. It stops being a routine sync channel, and its server copy is rebuilt from materialized assignments rather than trusted from whichever client pushed last.

### 2.2 Event payload: first-class assignments

Every menu merge event (all kinds) additionally carries an explicit assignment list, so any peer can apply it without needing the source cluster to still exist:

```json
"merge_payload": {
  "kind": "resolution_variant_v1",
  "...": "existing fields unchanged",
  "assignments": [
    {"order_item_id": "…", "menu_item_id": "<target>", "variant_id": "<target-or-null-sentinel>", "is_verified": 1}
  ]
}
```

- `basic_merge_v1`: one entry per `affected_order_item_ids` row — `menu_item_id = target_id`, `variant_id` unchanged (emit the current value so the applier is stateless).
- `variant_merge_v1` / `resolution_variant_v1`: derived from `mapping_rows` (already recorded in `history_payload`).
- `menu_merge.undone`: carries the **prior** assignments (the `old_*` columns already stored in `history_payload.mapping_rows` / undo metadata). An undo is just another LWW write at its own `server_seq` — this fixes I10 with no special casing.
- Bump `SCHEMA_VERSION` to 2. Legacy v1 events remain convertible: the applier derives assignments from `history_payload` (`mapping_rows` for variant/resolution kinds, `affected_order_item_ids` + `target_id` for basic merges), so old events in the server log replay fine.
- **Key-omission convention** (as implemented; pinned by `contracts/menu_merge_event_fixtures.json` in both repos): a normalized assignment always carries `order_item_id` and `menu_item_id`; the `variant_id` / `is_verified` keys are **omitted** when the event does not specify them (v1 basic-merge derivation), and the applier then leaves the current row value untouched. `variant_id: null` means the SQL NULL variant (`__NULL_VARIANT__` normalizes to null). v2 emit always includes both keys.

### 2.3 Client apply algorithm (replaces cluster replay)

New columns on `menu_item_variants`: `assignment_seq INTEGER` (highest `server_seq` applied to this row) and `pending_local INTEGER DEFAULT 0` (this row was changed locally and not yet acknowledged by the server echo).

For each pulled event, in stream order:

1. Skip if `remote_event_id` already in `menu_merge_remote_events` (existing dedupe). **If the event matches an existing local merge** (via `_find_matching_local_merge`, which ignores `origin='remote'` history rows), apply its assignments through the same seq-guarded writer (stamping `server_seq`, clearing `pending_local`) without inserting a new history row. The matched merge counts as *ours* only when it has an outbox event (`menu_merge_sync_events` row) — remote-applied merges never do. For our own echo, any row skipped as stale (it already carries a *higher* `assignment_seq`) means a peer outranked us between our edit and our ack: the winning value is already on the row, so just emit a "your resolution of X was superseded by {attribution}" notice, looking the winner up via `menu_merge_remote_events.server_seq` (fixes I13's loser notification).
2. Otherwise, for each assignment in the event:
   - `_ensure_menu_item_exists` / `_ensure_variant_exists` from the event snapshot (existing helpers).
   - If the local row's `assignment_seq` ≥ event's `server_seq` → skip (stale event; guarantees idempotence and order-independence across snapshot + tail).
   - If the local row is `pending_local` → apply the remote assignment anyway, **silently** (server order will judge; if our own event lands later on the server, its echo re-asserts our value everywhere, including here — a notice now would wrongly tell the eventual winner they lost). The loser notification fires in exactly two places: step 1's ack-time stale check, or here when the overwritten row is **not** pending and its current `assignment_seq` maps (via the outbox↔`menu_merge_remote_events` `server_seq` join) to an event we authored — i.e. a peer overwrote an already-acknowledged local decision.
   - Else UPDATE `menu_item_variants`, `order_items`, `order_item_addons` for that `order_item_id` to the event's target, set `is_verified`, set `assignment_seq = server_seq`, `pending_local = 0`. Rows whose `order_item_id` has no local `order_items` row are skipped (this install never saw that order); a missing mapping row for an existing order item is inserted.
3. After each batch: garbage-collect menu items left with zero mapping rows (matches `merge_menu_items` step 6), `_recalculate_menu_item_stats` + `_sync_menu_item_resolution_state` for every touched item, clear forecast caches for touched items (reuse `_clear_item_and_volume_forecast_cache` / `_clear_impacted_models`), one `export_to_backups`.
4. Record a `merge_history` row per applied event with `origin='remote'` so Resolution History and undo keep working (replaces the current "replay through `merge_menu_items`" mechanism).

Local edits keep using the existing code paths (`merge_menu_items`, `resolve_menu_item_variant`, …) unchanged, except they set `pending_local = 1`, `assignment_seq = NULL` on rows they touch and their emitted events include `assignments`.

**Convergence argument:** all installs see the same totally-ordered stream (fix I3), apply the same per-key LWW rule with a seq guard, and local provisional edits are reconciled at ack time against that same order. Any two installs that have pulled to the same cursor and have no pending local edits have identical assignments. Fresh installs start from the materialized snapshot at watermark S and tail events with `seq > S` — same fixed point (fixes I4).

### 2.4 What stays out of scope

- Customer merge conflict semantics (benefits from the server ordering fix automatically; applier redesign is a separate effort).
- Full SQLite replication; orders remain POS-sourced per install.
- `is_verified` conflicts between the verification stream and the merge stream are benign (flag-only) once both go through the seq guard; no cross-stream ordering is attempted.

---

## 3. Server changes (`db.dachnona`)

### Phase S1 — ordering, seq exposure, per-event ingest results *(prerequisite for everything; backward compatible)*

> **Status: implemented** (db.dachnona branch `sync-conflict-phase-1`). Delta replay is ordered by `(ingested_at, id)`; cursors are v2 (`{"v":2, ingested_at, id}`) and v1 cursors get a 400 `"Unsupported cursor version."`; `server_seq`/`server_ingested_at` injected into every delta event; ingest returns `accepted`/`rejected` per event (plus the legacy `ingested_count`/`duplicate_count`); naive `occurred_at` parsed as UTC; `(scope_key, ingested_at, id)` indexes added via migration 0011 (not yet applied to any DB). Covered in `desktop_analytics_app_sync/tests.py`.

`backend/desktop_analytics_app_sync/services/merge_events.py`:

1. **Order replay by `(ingested_at, id)` only** in `_get_merge_delta`; drop `occurred_at` from ordering, cursor encode/decode, and the filter. Add `"v": 2` to the cursor payload; reject v1 cursors with a distinct error string (`"Invalid cursor."` → keep, clients treat any 400 as an error and hold position; the client-side cursor reset in Phase C1 handles migration). Applies to all three streams (menu merges, mapping verifications, customer merges) since they share `_get_merge_delta`. Fixes **I3**.
2. **Inject `server_seq`** into each returned event dict: `{**row.payload_json, "server_seq": row.id, "server_ingested_at": row.ingested_at.isoformat()}`. Additive — old clients ignore unknown keys.
3. **Per-event ingest results** in `_ingest_merge_events`: validate and persist each event independently; collect `{"accepted": [ids...], "rejected": [{"remote_event_id":…, "error":…}, ...]}` instead of failing the batch. Move per-event validation out of `MergeIngestSerializer` (keep envelope validation there; relax the per-event schema to "dict with `remote_event_id` and parseable `occurred_at`", everything else best-effort). A malformed event is rejected individually, never 400s the batch. Fixes server half of **I7**.
4. **UTC handling**: in `_parse_contract_datetime`, treat naive datetimes as **UTC** (`timezone.utc`), not `get_default_timezone()`. Fixes **I12**. (Also normalize SQLite's `"YYYY-MM-DD HH:MM:SS"` which `parse_datetime` already accepts.)

New indexes/migration: `models.py` — add `models.Index(fields=["scope_key", "ingested_at", "id"], name="menu_merge_scope_ing_idx")` (and equivalents for the other two event tables).

Tests (`tests.py`): cursor v2 round-trip; offline-device scenario (ingest event with old `occurred_at` after a peer's cursor has advanced → peer still receives it); per-event rejection leaves siblings ingested; naive `occurred_at` stored as UTC.

### Phase S2 — materialized ground truth + snapshot endpoint

> **Status: implemented** (db.dachnona branch `sync-conflict-phase-2`). `OrderItemAssignment` model + migration 0012 (**created, not applied to any real DB** — run `migrate` + `rebuild_order_item_assignments` at deploy); `services/assignment_state.py` applies menu merge events at ingest under the `id > last_seq` guard and verification events flag-only; snapshot endpoint at `GET /desktop-analytics-sync/menu-assignments/snapshot?after=&limit=` (paged by `order_item_id`; returns `watermark_seq` **and** a ready-to-use `watermark_cursor` for the tail); bootstrap demotion honors `snapshot_role: "seed_only"` and the `MENU_BOOTSTRAP_TRUST_CLIENT_CLUSTER_STATE` env toggle (id_maps still merge forward; first-ever push may still seed cluster_state). S2.4/S2.5 below describe the as-built contract (incl. the deliberate frozen-cluster-state deviation). Fixtures shared with the client live in `contracts/menu_merge_event_fixtures.json` (source of truth in the analytics repo). Tests in `desktop_analytics_app_sync/tests.py` (`OrderItemAssignmentTests`).

1. **Model** (`models.py` + migration):

```python
class OrderItemAssignment(models.Model):
    scope_key = models.CharField(max_length=64, db_index=True)
    order_item_id = models.CharField(max_length=128)
    menu_item_id = models.CharField(max_length=128)
    variant_id = models.CharField(max_length=128, null=True, blank=True)  # null = NULL_VARIANT_SENTINEL
    is_verified = models.BooleanField(default=True)
    last_seq = models.BigIntegerField()          # MenuMergeEvent.id that set this
    last_event_id = models.CharField(max_length=128)
    employee_id / employee_name / install_id ... # attribution, same fields as MergeEventBase
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        constraints = [UniqueConstraint(fields=["scope_key", "order_item_id"], ...)]
```

2. **`services/assignment_state.py`**: `apply_menu_merge_event_to_assignments(event_row)` — extract assignments (schema v2 field, or derive from v1 `history_payload` exactly as the client converter does), upsert each row **iff `event_row.id > last_seq`**. Called inside `_ingest_merge_events`' transaction for `MenuMergeEvent` (and for `MenuMappingVerificationEvent`, flag-only updates under the same guard — fixes the server-side view of **I5**).
3. **Backfill management command** `rebuild_order_item_assignments --scope <key>`: replay all existing `MenuMergeEvent` rows in `(ingested_at, id)` order through the same function. Run once at deploy; also the recovery tool if the table is ever suspect.
4. **Snapshot endpoint**: `GET /desktop-analytics-sync/menu-assignments/snapshot?after=&limit=` → `{"assignments": [...], "watermark_seq": <max MenuMergeEvent.id at snapshot time>, "watermark_cursor": <encoded v2 delta cursor at that event>, "next_page": <last order_item_id of the page, or null>}` (views/`menu_assignments.py`, wired in `urls.py`, bearer auth + scope like the others). Paged deterministically by `order_item_id`; `after` is the previous page's `next_page`. `watermark_cursor` lets the client start the event tail directly at the watermark without constructing a cursor itself — no gap, no overlap. Clients keep the **first** page's watermark; later pages may already contain newer rows, which is safe because tail replay is idempotent under the seq guard.
5. **Bootstrap demotion** (server-side, fixes the server half of **I6**): `menu_bootstrap_ingest` keeps accepting pushes (old clients) but stops overwriting `MenuBootstrapLatest.cluster_state` when the payload carries `"snapshot_role": "seed_only"` (Phase C4 clients) or the ops toggle `MENU_BOOTSTRAP_TRUST_CLIENT_CLUSTER_STATE=false` is set; `id_maps` (catalog metadata) still merges forward, and the first-ever push for a scope may still seed `cluster_state`. **Deliberate deviation from the original "derived cluster_state" idea:** the stored cluster state is *frozen*, not regenerated from `OrderItemAssignment` — the cluster_state format carries per-row detail (`type_id`, name tuples) that assignments don't have, and Phase C4 clients no longer consume it for relinking, so regeneration would add lossy complexity for no consumer. `OrderItemAssignment` (+ the snapshot endpoint) is the authoritative clustering source.

Tests: LWW upsert guard; v1-event derivation parity with client converter (shared fixture file of sample events checked into both repos — see §6); snapshot watermark consistency (no gap/overlap between snapshot and `seq > watermark` tail); backfill idempotence.

### Phase S3 — convergence audit (ground-truth monitoring for the central team)

- Client-reported digest (see C6) lands via existing learning ingest: `{"install_id":…, "cursor_seq":…, "assignments_sha256":…, "row_count":…}`.
- Server compares against the digest of `OrderItemAssignment` at the same `seq` (compute by replay-window or accept small tolerance: only compare when `cursor_seq == max seq`). Expose mismatches in Django admin (`admin.py` list for a new `SyncConvergenceReport` model) — the "is everyone actually in sync" dashboard.

---

## 4. Client changes (this repo)

### Phase C1 — pull resilience: quarantine, poison pills, cursor migration *(independent of server; ship first)*

> **Status: implemented** (branch `sync-conflict-phase-1`). Quarantine table + helpers in `src/core/menu_sync_quarantine.py`; both pull loops quarantine per-event failures and keep advancing the cursor, with a retry pass at the start of each pull; one-time cursor reset in `src/core/sync_cursor_migration.py` (wired into all three streams' ensure-tables paths); `GET /api/menu/sync-conflicts` + dismiss endpoint in `src/api/routers/menu.py`. UI badge not done (API only). Tests in `tests/test_menu_merge_sync.py` / `tests/test_menu_mapping_verification_sync.py`.

1. **Quarantine table** (created in `ensure_menu_merge_sync_tables`):

```sql
CREATE TABLE IF NOT EXISTS menu_sync_event_quarantine (
    remote_event_id TEXT PRIMARY KEY,
    stream TEXT NOT NULL,              -- 'menu_merge' | 'mapping_verification'
    payload TEXT NOT NULL,
    error TEXT,
    fail_count INTEGER DEFAULT 1,
    first_failed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_failed_at TEXT,
    resolved_at TEXT                   -- set when a retry succeeds or user dismisses
);
```

2. `pull_and_apply_menu_merge_events`: on per-event exception, **insert into quarantine** (upsert bumping `fail_count`) instead of only `logger.warning`; keep advancing the cursor. A `retry_quarantined_events(conn)` pass runs at the start of every pull (mirrors `flush_deferred_menu_mapping_verifications`); events that succeed on retry get `resolved_at`. Fixes **I1's silent half and I9** (full fix of I1 lands in C3 when conflicts stop failing at all).
3. `pull_and_apply_menu_mapping_verification_events`: adopt the same semantics — per-event try/except → quarantine → continue → cursor advances (today it aborts without advancing). Fixes **I8**.
4. **Cursor schema migration**: on startup (`_ensure_pull_tables`), if `system_config['sync_cursor_schema_version'] != '2'`: delete `menu_merge_pull_cursor`, `menu_mapping_verification_pull_cursor`, `customer_merge_pull_cursor`, set version = 2. Full re-pull is safe: every applied event is already deduped via `menu_merge_remote_events` / `menu_mapping_verification_remote_events`. This cleanly absorbs the server's ordering change without missed-event edge cases.
5. **API surfacing**: `GET /api/menu/sync-conflicts` (new handlers in `src/api/routers/menu.py`) returning unresolved quarantine rows + supersede notices (C3), and `POST /api/menu/sync-conflicts/{remote_event_id}/dismiss`. UI: badge on the Menu/Resolutions tab. (Minimal first version: count + list with item names from event snapshots.)

Tests (`tests/test_menu_merge_sync.py`, `tests/test_menu_mapping_verification_sync.py`): poison event quarantined and cursor advances (both streams); retry drains quarantine; cursor reset happens exactly once.

### Phase C2 — push resilience *(pairs with S1.3)*

> **Status: implemented** (branch `sync-conflict-phase-1`). All three shippers parse `accepted`/`rejected` via `src/core/sync_push_results.py`; rejected events are marked errored (uploaded_at + last_error) and quarantined under `stream='<stream>_push'`; responses without those keys fall back to the legacy all-or-nothing behavior (old server); locally-unparseable payload rows are marked errored instead of sitting unsent. Tests alongside the C1 tests plus `tests/test_customer_merge_sync.py`.

`src/core/menu_merge_shipper.py` (and `menu_mapping_verification_shipper.py`, `customer_merge_shipper.py` — same pattern):

1. Parse the ingest response's `accepted`/`rejected` lists. Mark accepted events uploaded; mark rejected events `uploaded_at = <now>` **and** `last_error = <server error>` plus copy them into the quarantine table (`stream='menu_merge_push'`) so they stop blocking the queue but remain visible. Fixes **I7**.
2. If the response has no `accepted`/`rejected` keys (old server), keep current all-or-nothing behavior — forward compatible either way.
3. Guard against the one local poison shape we already know: events whose payload fails `json.loads` are currently skipped in `_select_unsent_events` but never marked — mark them errored so they don't sit unsent forever.

Tests: mixed accepted/rejected batch; old-server response shape; malformed local payload marked.

### Phase C3 — assignment-based apply (the core fix)

> **Status: implemented** (branch `sync-conflict-phase-2`). Schema via conditional ALTERs in `src/core/menu_assignment_schema.py` (called from every ensure path) plus fresh-install DDL in `database/schema_sqlite.sql`; extraction + seq-guarded applier + supersede notices in `src/core/menu_assignment_apply.py`; `SCHEMA_VERSION = 2` with `assignments` on all kinds and undo (signatures unchanged); local edit paths stamp `pending_local = 1, assignment_seq = NULL`; `menu_merge_sync.py` routes both event types through the assignment applier behind `MENU_SYNC_ASSIGNMENT_APPLY` (default on, env off-switch), with echo/ack, batch epilogue and `merge_history.origin='remote'`; legacy cluster replay remains only as a loudly-logged fallback for events with no derivable assignments. §2.3 above has been updated to the as-built algorithm (notice timing, echo authorship via the outbox join). Supersede notices surface in `GET /api/menu/sync-conflicts` (+ acknowledge endpoint). Verification stream seq guard added (skip if event `server_seq` < row `assignment_seq`; `assignment_seq` is not stamped from that stream). Conflict-matrix tests in `tests/test_menu_assignment_apply.py`.

1. **Schema** (`ensure_menu_merge_sync_tables` or the central migration in `src/core/db`): `ALTER TABLE menu_item_variants ADD COLUMN assignment_seq INTEGER; ALTER TABLE menu_item_variants ADD COLUMN pending_local INTEGER DEFAULT 0;` plus index on `(order_item_id)` if not present. New table `menu_sync_supersede_notices (order_item_id, local_merge_id, superseded_by_event_id, attribution, created_at, acknowledged_at)`.
2. **Emit side** (`src/core/menu_merge_sync_events.py`): `_build_merge_payload` adds the `assignments` array for all three kinds (from `mapping_rows` / `affected_order_item_ids` — for basic merges, join current `variant_id` per order item at emit time). `record_menu_merge_undone_event` includes prior assignments. `SCHEMA_VERSION = 2`. Signature computation (`operation_signature`, `_event_signature`) stays on the existing fields so v1↔v2 dedupe keeps matching.
3. **Local edit paths** (`utils/menu_utils.py`: `merge_menu_items`, `merge_menu_items_with_variant_mappings`, `resolve_menu_item_variant`, `remap_order_item_cluster`, `undo_merge`): set `pending_local = 1, assignment_seq = NULL` on every `menu_item_variants` row they rewrite (one extra SET clause in the existing UPDATEs; no behavioral change otherwise).
4. **Apply side** (`src/core/menu_merge_sync.py`): implement §2.3.
   - New `_extract_assignments(event)` — schema v2 field, else derive from v1 `history_payload` (shared fixtures with server, §6).
   - New `_apply_assignments(conn, assignments, server_seq, event)` — per-row seq-guarded UPDATE of `menu_item_variants` + `order_items` + `order_item_addons`; `_ensure_menu_item_exists`/`_ensure_variant_exists` first; returns touched menu_item_ids.
   - `_apply_remote_merge_event` / `_apply_remote_undo_event` route through it. The legacy "replay through `menu_utils`" path is kept **only** as fallback for events with no derivable assignments (shouldn't exist; log loudly). Fixes **I1, I2, I10**.
   - Echo handling: when `_find_matching_local_merge` matches (our own event returning), stamp `assignment_seq = server_seq`, clear `pending_local` on the merge's rows; if any row's `assignment_seq > server_seq` (someone outranked us between our edit and our ack), re-apply the winning remote assignment from `menu_merge_remote_events` and insert a supersede notice. Fixes **I13**.
   - Batch epilogue: orphaned-item GC, stats recompute, resolution-state sync, cache clears, single `export_to_backups`, `merge_history` rows with `origin='remote'` (add nullable `origin` column to `merge_history`).
5. **Verification stream** (`menu_mapping_verification_sync.py`): `_apply_verification_by_order_item_id` gains the seq guard (skip if event `server_seq` < row `assignment_seq`); still flag-only by design — mapping corrections ride the merge stream (documents and bounds **I5**).

Tests: the full conflict matrix —
- two installs resolve same order item differently → both converge to higher `server_seq`, loser gets a notice (simulate with two in-memory SQLite DBs + fake server list);
- conflicting basic merges (A→B vs A→C);
- undo after conflicting merge;
- v1 legacy event derivation;
- echo/ack path with and without an outranking event;
- seq guard: replaying an old event after a newer one is a no-op;
- orphan GC + stats after batch.

### Phase C4 — bootstrap demotion + fresh-install fast path

> **Status: implemented** (branch `sync-conflict-phase-2`). Fresh-install seed in `src/core/menu_assignment_bootstrap.py` (runs from the orchestrator after the catalog bootstrap): pages the snapshot, applies rows via the same applier at `server_seq = watermark`, sets the menu-merge cursor to the server's `watermark_cursor`, marks `menu_assignments_bootstrapped`; installs that already have a pull cursor are marked without reseeding. `DEFAULT_MENU_BOOTSTRAP_APPLY_MODE` is now `seed_only`; relink only via `MENU_BOOTSTRAP_APPLY_MODE` env / `menu_bootstrap_apply_mode` config (the manual `/bootstrap/pull-from-cloud` route keeps its explicit parameter for restore flows). Shipper sends `snapshot_role: "seed_only"` and skips pushes while the `id_maps` sha256 (stored in `data/menu_bootstrap_last_push_hash.txt`) is unchanged. Tests in `tests/test_menu_assignment_bootstrap.py` (snapshot digest == replay digest, clean tail, restore flag).

1. `src/core/services/cloud_pull_orchestrator.py`:
   - New snapshot-seed step (runs **after** the catalog bootstrap so menu items exist, before the event tails; implemented in `src/core/menu_assignment_bootstrap.py`): if `system_config['menu_assignments_bootstrapped']` unset **and** the server snapshot endpoint is configured → page through `GET /menu-assignments/snapshot`, apply rows (same seq-guarded applier, `server_seq =` the first page's `watermark_seq`), set the menu-merge pull cursor to the server-provided `watermark_cursor`, mark bootstrapped. Installs that already have a menu-merge pull cursor are not fresh: they are marked bootstrapped without reseeding (their state came from replay). Fetch errors leave the flag unset so the next pull retries. Fresh installs no longer replay history and cannot hit I4.
   - Menu bootstrap pull: change `DEFAULT_MENU_BOOTSTRAP_APPLY_MODE` to `"seed_only"` (no `_relink_order_items_from_snapshot`) once assignments are live; keep `seed_and_relink_orders` behind an explicit config flag for support/restore flows. Fixes client half of **I6** (both the clobber and the `order_items`-only inconsistency).
2. `src/core/menu_bootstrap_shipper.py`: include `"snapshot_role": "seed_only"` in the payload (S2.5 uses it); reduce cadence — ship bootstrap only after catalog-shape changes (new items/variants), not after every merge. (Cheap heuristic: hash `id_maps`; skip push when unchanged.)

Tests: fresh-DB bootstrap → identical assignments to an install that replayed the log; relink disabled by default; restore flag still works.

### Phase C5 — background pull + shrink the window

> **Status: implemented** (branch `sync-conflict-phase-2`), except the optional item 2 (pull-before-edit on tab focus — UI work, not done). The 5-minute scheduler runs `run_best_effort_cloud_pulls` after its push phase under a process-wide `CLOUD_PULL_LOCK` (`blocking=False`, so it skips while the Sync DB job pulls); local merge/resolution/undo commits fire `src/core/menu_merge_push_nudge.py` (daemon thread, no-op without cloud config). Tests in `tests/test_cloud_pull_lock_and_nudge.py`.

1. `src/core/services/cloud_sync_scheduler.py`: after the push phase each cycle, run `run_best_effort_cloud_pulls(conn)` guarded by a process-wide lock shared with the Sync DB job (reuse/extend `JobManager` or a simple `threading.Lock` in the orchestrator module) so a button-triggered sync and the scheduler never interleave pulls on one install. Fixes **I11**.
2. Optional UX: trigger a lightweight merge-events pull when the Menu Matrix / Resolutions tab gains focus ("pull-before-edit"), via the existing `POST /api/menu/…/pull` route in `src/api/routers/menu.py`.
3. Push latency: after any local merge/resolution commit, nudge the shipper (call `upload_pending` for the menu-merge stream fire-and-forget in a thread) instead of waiting up to 5 minutes. This is the "push immediately" idea — worth doing, but note it is an optimization layered on the deterministic model, not the correctness mechanism (**I13**).

### Phase C6 — convergence digest *(pairs with S3)*

Nightly (piggyback on `client_learning_shipper.run_all`): compute `sha256` over ordered `(order_item_id, menu_item_id, variant_id, is_verified)` rows where `pending_local = 0`, report with current cursor seq. Server compares (S3). Gives the central team a standing answer to "are all installs in sync," instead of discovering divergence via bug reports.

---

## 5. Rollout order & compatibility

| Step | Deploy | Risk gate |
|------|--------|-----------|
| 1 | **S1** (ordering + seq + per-event ingest + UTC) | Old clients tolerate all of it: extra payload keys ignored; per-event results ignored (old shipper treats 200 as full success — acceptable during overlap since rejects were previously 400-blocking anyway); ordering change only affects clients after their C1 cursor reset |
| 2 | **C1 + C2** (resilience + cursor reset) | Pure client hardening; safe against old or new server |
| 3 | **C3** (assignment applier) | Requires S1 (`server_seq` present). Feature-flag `MENU_SYNC_ASSIGNMENT_APPLY=1` (config default on, env override off) for one release |
| 4 | **S2** (materialized state + snapshot) + backfill command | Read-only addition |
| 5 | **C4** (fast path + bootstrap demotion), then flip `MENU_BOOTSTRAP_TRUST_CLIENT_CLUSTER_STATE=false` server-side once fleet is on C4 | The last install still pushing authoritative bootstrap must be upgraded before the flip |
| 6 | **C5, C6, S3** | Additive |

Mixed-fleet window (some installs pre-C3): old installs still fail conflicting replays — but now the events they miss are quarantined (C1) rather than lost, and new installs + the server materialized state converge correctly. Divergence heals when the old install upgrades (cursor reset → re-pull → assignment apply).

## 6. Shared test fixtures

Create `contracts/menu_merge_event_fixtures.json` in **both** repos (source of truth here; copied into `db.dachnona/contracts/`): a set of real v1 and v2 event payloads (basic, variant, resolution, undo, malformed) plus the expected extracted assignments for each. Client tests (`_extract_assignments`) and server tests (`assignment_state`) both consume it, so the two derivations can never drift.

## 7. Acceptance criteria

1. Two installs resolving the same order item differently converge to the same assignment (higher `server_seq`) after each install completes one push + one pull cycle, and the superseded user sees a notice. *(I1, I13)*
2. An install offline for N days ships its backlog; every peer receives and applies those events. *(I3)*
3. A fresh install reaches the same assignment digest as long-lived installs at the same cursor. *(I4)*
4. A malformed event neither blocks push nor stalls/skips either pull stream; it is visible in `/api/menu/sync-conflicts`. *(I7, I8, I9)*
5. Undoing a merge on install 1 restores the prior assignments on install 2. *(I10)*
6. Sync DB never rewrites `order_items` from another install's bootstrap snapshot (default config). *(I6)*
7. Server `OrderItemAssignment` digest matches every up-to-date install's digest. *(ground truth, I5)*
