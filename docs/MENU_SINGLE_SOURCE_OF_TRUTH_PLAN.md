# Menu single source of truth — implementation plan

**Audience:** Product + engineers (desktop analytics repo + Dachnona cloud)  
**Status:** **SIGNED OFF 2026-07-06.** Phases 0, 1, and 3 (C1) **implemented and shipping against prod**; Phase 2 (Option B) **superseded by design** (verification event stream + assignment snapshot/watermark cursor make an `is_verified` snapshot overlay unnecessary); Phase 4 (Option C) **not built by design** (the server assignment snapshot is the fresh-install fast path). **Cross-device convergence validated 2026-07-06** via a fresh-install end-to-end simulation against prod: S1 satisfied (0 unverified → empty Resolutions), S4/S5 satisfied, S2 best-effort as scoped (567/567 shared mapping keys identical; residual legacy-key placements documented in [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) §11.3). See **§0. As-built status** below.  
**Goal:** Move toward **one authoritative representation of “clean menu + mapping + verification state”** so a **fresh install** (or **reset orders + seed**) followed by **sync/pull** can **converge** to the same catalog and resolution state as a **reference machine** (e.g. dev), within explicit limits.

This doc complements:

- [INDEX.md](./INDEX.md) — doc hub and task routing  
- [AI_SESSION_GUIDE.md](./AI_SESSION_GUIDE.md) — cross-tool session conventions  
- [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) — merge + bootstrap ingest/pull contracts  
- [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) — the conflict/LWW plan under which Option A actually landed (phases C1–C5)  
- [FORECASTING_AND_SYNC.md](./FORECASTING_AND_SYNC.md) — broader sync overview  

---

## 0. As-built status (2026-07-06)

This section records what is **actually implemented**, verified against the live install
(`dn-analytics`, pointed at `webhooks.db1-prod-dachnona.store`) after the cloud API-contract
changes were deployed.

| Plan item | State | Notes |
|-----------|-------|-------|
| **Option A — verification events** (§4, Phase 1) | ✅ Implemented | `menu_mapping_verification_sync_events.py` (emit), `menu_mapping_verification_shipper.py` (push), `menu_mapping_verification_sync.py` (pull/apply, LWW by `verification_seq`) |
| Emit on verify **and** undo (§1.1 gap) | ✅ Closed | `utils/menu_utils.py` calls `record_menu_mapping_verification_events_chunked` on verify and reopen |
| Pull ordering (§8.2) | ✅ Wired | `cloud_pull_orchestrator.py`: bootstrap → mapping verifications → menu merges |
| Cursor + migration (§4.4/5) | ✅ Done | `menu_mapping_verification_pull_cursor`, `sync_cursor_migration.py` |
| Deferred apply (§4.5, order_item_id not yet imported) | ✅ Done | `menu_mapping_verification_deferred` table + retry pass |
| Tests (§4.4/6) | ✅ Green | 36 passing across the 4 verification/assignment/merge suites |
| **Phase 0** — instrumentation hygiene / grep gate | ✅ Clean | no `debug-56943a` / `#region agent log` / `.cursor/debug-` / local ingest URLs in `src`/`services`/`utils`/`scripts` |
| **Phase 3 (C1)** — clustering inherits verified status | ✅ Done | `services/clustering_service.py` sets `is_verified=1` on insert when `(menu_item_id, variant_id)` is verified elsewhere |
| **Option B** — versioned catalog snapshot (Phase 2) | ◑ Superseded | verification truth now carried by the event stream + bootstrap watermark cursor / catalog-only bootstrap, not an `is_verified` snapshot overlay |
| **Option C** — packaged DB (Phase 4) | ○ Not built | closest artifact is the baseline export / force-reseed cutover tooling |

**Single-install validation (confirmed via one SyncDB against prod):**

- Push: 70 verification events emitted, **70 uploaded, 0 pending, 0 errored**.
- Pull: 78 remote verification events applied; pull cursor advanced (Jul 5 14:27).
- Deferred applies: 0. Resolutions backlog: **574 verified / 0 unverified** → **S1 satisfied**.

**~~Known open issue~~ RESOLVED 2026-07-06:** the 4 `menu_merge` remote events stuck in
`menu_sync_event_quarantine` (`Cannot merge item into itself` — April `mapping_audit_v1`
addon-consolidation events with source == target and no derivable assignments) are now applied
as recorded no-ops by the merge apply path (`_is_self_merge_event` in
`src/core/menu_merge_sync.py`), matching the server materializer. The live quarantine was
drained (4/4 resolved). A fresh install never pulls them anyway — its merge cursor starts at
the snapshot watermark, past those events.

**Multi-install validation (2026-07-06, fresh-install simulation against prod):** fresh schema →
packaged seed → full order import (16 872 orders) → cloud pulls. Result: snapshot seed applied at
watermark, **0 unverified mappings** (S1: Resolutions starts empty), 567/567 shared mapping keys
identical to the golden install, per-line placement identical for 32 023 of 32 074 line-groups.
The 51 divergent groups are April-era decisions recorded under legacy mapping keys — see the
conflict plan §10.3/§11.3 for the one-time human remap that closes them.

---

## 1. Problem statement

### 1.1 What “all verified” means today (local only)

The **Resolutions** UI is driven by `GET /api/menu/resolutions/unverified`, which ultimately queries **`menu_item_variants` where `is_verified = 0`** (see `fetch_unverified_items` in `src/core/queries/menu_queries.py`).

Many workflows set `is_verified = 1` **only in SQLite** (and sometimes refresh `data/id_maps_backup.json` + `cluster_state_backup.json` via `export_to_backups`). Example: **`verify_item`** in `utils/menu_utils.py` updates local rows and calls `export_to_backups` but **does not** emit a **menu merge sync event** for the cloud queue.

### 1.2 What the cloud sees today

The client already ships:

- **Menu merge events** (applied / undone) when merges go through paths that call `record_menu_merge_applied_event` / `record_menu_merge_undone_event`  
- **Menu bootstrap** (`id_maps` + `cluster_state` JSON) via `menu_bootstrap_shipper`  

Those mechanisms **do not** encode every **per–`order_item_id` mapping verification** or every **silent verify** unless that state is **folded into** merges, bootstrap snapshots, or a **new** event stream.

### 1.3 What breaks “DMG == dev” expectations

After **reset + seed**, **Sync DB** re-ingests **POS orders**. Clustering creates **new** `menu_item_variants` rows for **live** `order_item_id` values, typically with **`is_verified = 0`**, because seeded JSON keys are **historical** `order_item_id`s from an export, not future POS ids.

So even with a **perfect** cloud merge log, **fresh order ingestion** can recreate **Resolutions work** unless:

- verification state is **replayed** onto those new rows, or  
- clustering **reuses** verified mappings without inserting `is_verified = 0` rows, or  
- catalog + mapping truth is **materialized** elsewhere and applied after every sync.

---

## 2. Success criteria (define “single source of truth”)

Pick measurable outcomes; not all are compatible with every option.

| ID | Criterion | Notes |
|----|-----------|--------|
| S1 | **Fresh DB + configured cloud + Sync DB** yields **empty Resolutions** when cloud says “clean” | Requires either no unverified mappings or automatic verification replay |
| S2 | **Two installs** with same cloud cursor + same POS stream produce **same** `menu_items` / `variants` / critical `menu_item_variants` rows for the same business keys | Hard if clustering is nondeterministic beyond IDs |
| S3 | **Reset orders** then seed restores **canonical catalog** from cloud or bundle, not from a **mutated** `_internal/data` copy | May require writable userData seed path (separate doc topic) |
| S4 | **Auditability**: every transition from “unverified” → “verified” is **recoverable** on another device via cloud pull | Needs events or versioned snapshots |
| S5 | **Idempotent** cloud ingest + client apply (retries safe) | Align with contract Section 4.3 style rules |

**Recommendation:** Target **S1 + S4** first (user-visible parity + audit). Treat **S2** as best-effort unless you adopt full DB replication.

---

## 3. Strategic options (summary)

| Option | Idea | Cloud change | Client change | Pros | Cons |
|--------|------|--------------|----------------|------|------|
| **A** | **Verification events** — every verify / resolution emits a durable event (like merges) | New ingest + pull event type (or extend merge payload kinds) | Emit on verify; apply on pull; reconcile with clustering | Fine-grained, auditable, incremental | Many events; must handle ordering + compaction |
| **B** | **Richer bootstrap snapshot** — periodic “full menu truth” including mapping flags / variant graph | Extend `menu-bootstrap/latest` payload or add `menu-catalog/v2` | Build snapshot from DB; apply replaces or merges local state | Fewer round trips than per-line events | Large payloads; conflict policy with live POS |
| **C** | **Shipped DB snapshot** — treat a **file** or **backup** as truth for greenfield installs | Optional hosting of signed snapshot; or none (file-only) | Import path in client | Strongest parity for **static** installs | Worst for ongoing POS divergence; ops + security |

**Pragmatic combo:** **A + B** — events for interactive edits; **versioned bootstrap** for “checkpoint” and disaster recovery. Use **C** only for factory / support bundles.

---

## 4. Option A — Verification and resolution events (detailed plan)

### 4.1 Intent

Any operation that currently flips **`menu_item_variants.is_verified`** (or equivalent “resolution complete” state) without a merge should produce a **cloud-durable event** another client can apply.

### 4.2 Event taxonomy (proposal)

Define a new stream (preferred) **or** extend menu merge with new `kind` values (riskier coupling).

**Preferred:** `menu_mapping_verification` events:

| `event_type` | Meaning |
|--------------|---------|
| `mapping.verified` | One `(menu_item_id, variant_id)` or `(order_item_id)` mapping is verified |
| `mapping.bulk_verified` | Bounded batch (e.g. max 500 keys) for compaction |
| `mapping.reopened` | Optional undo / admin |

Payload fields (minimum):

- `schema_version`  
- `tenant` / store resolution (server-side)  
- `menu_item_id`, `variant_id` **or** `order_item_id` (choose one canonical key; `order_item_id` is best for POS alignment)  
- `is_verified` target (usually `1`)  
- `actor` (`uploaded_by` / device attribution — reuse existing patterns)  
- `occurred_at`, `remote_event_id` (UUID) for idempotency  

### 4.3 Cloud backend work

1. **Persistence** — table similar to menu merge events: `remote_event_id`, payload JSON, cursor, store scope.  
2. **Ingest** — `POST .../menu-mapping-verifications/ingest` (name illustrative) with idempotent dedupe.  
3. **Pull** — `GET .../menu-mapping-verifications?cursor=&limit=` ascending order, same cursor semantics as merge pulls.  
4. **Retention / compaction** — server-side rollup job turns N single-row events into `mapping.bulk_verified` checkpoints (optional phase 2).  
5. **Auth** — same Bearer key as existing desktop sync.

### 4.4 Desktop client work

| Step | Location / area | Action |
|------|-----------------|--------|
| 1 | `verify_item`, `resolve_menu_item_variant`, any bulk verify | After successful commit, call **`record_menu_mapping_verification_event(conn, ...)`** (new module parallel to `menu_merge_sync_events.py`) |
| 2 | Shipper | Add `menu_mapping_verification_shipper.py` (pattern from `menu_merge_shipper.py`) posting pending rows |
| 3 | Pull + apply | Add `menu_mapping_verification_sync.py` applying events onto `menu_item_variants` / `variants` / `menu_items` as needed |
| 4 | Orchestrator | Extend `run_best_effort_cloud_pulls` in `src/core/services/cloud_pull_orchestrator.py` to call new pull after menu merges (define order: bootstrap → mapping verifications → menu merges → customers, or the reverse — document chosen order) |
| 5 | Cursor | New `system_config` key `menu_mapping_verification_pull_cursor` |
| 6 | Tests | Mirror `tests/test_menu_merge_sync.py` style: apply order, idempotency, partial batches |

### 4.5 Interaction with POS sync

**Ordering rule (must document in code):**

- If a **verification** event references `order_item_id` that does not exist yet, **queue** client-side until order import creates the row, or **server** stores and client applies after orders (simpler server, more client logic).

**Preferred client approach:** apply mapping verification events **after** `sync_database` order pass for that session, inside the same “sync job” pipeline before marking job done.

### 4.6 Risks

- **Volume** — high-traffic stores could generate huge event counts; mitigation = bulk events + compaction.  
- **Conflicts** — verify vs later merge undo; define precedence (merge wins over stale verify, etc.).

---

## 5. Option B — Versioned full bootstrap / catalog snapshot (detailed plan)

### 5.1 Intent

Periodically (or on demand) publish a **single JSON document** (or tarball) that fully describes:

- `menu_items` (ids, names, types, `is_verified`, stats if needed)  
- `variants`  
- `menu_item_variants` including **`is_verified`** and `order_item_id` where applicable  

So a new device can **replace** or **merge** local catalog state without replaying thousands of events.

### 5.2 Cloud API shape (proposal)

Either extend:

- `GET /desktop-analytics-sync/menu-bootstrap/latest` with optional `?include=verified_mappings` and a **`snapshot_version`**,  

or add:

- `GET /desktop-analytics-sync/menu-catalog-snapshot/latest` returning `{ version, generated_at, id_maps, cluster_state, mapping_verification_overlay? }`

The existing bootstrap contract already centers on `id_maps` + `cluster_state`; the gap is **`is_verified` per mapping row** and possibly **non–exportable** rows. Options:

1. **Extend `cluster_state` schema** (backward compatible): each mapping entry becomes `[order_item_id, variant_id, is_verified]` instead of `[order_item_id, variant_id]` only.  
2. **Sidecar key** in snapshot: `mapping_flags: { "order_item_id|menu_item_id|variant_id": { "is_verified": 1 } }`  

### 5.3 Generation triggers

| Trigger | Who | Notes |
|---------|-----|-------|
| Manual | “Publish catalog snapshot” button in Configuration | Calls new endpoint to bump cloud version after local `export_to_backups` + upload |
| Automatic | After N merge events or nightly job | Cloud aggregates from events (if A exists) OR client uploads full snapshot |

### 5.4 Apply semantics on client (must choose one)

| Mode | Behavior | Risk |
|------|----------|------|
| **Replace** menu tables from snapshot for non-order tables | Simple | Loses local-only items unless merged into snapshot |
| **Merge** | Upsert by id; delete only if tombstone list included | More code |
| **Apply overlay on top of seed** | Seed from bundle, then snapshot delta | Two sources of truth |

**Recommendation:** **Merge upsert** for `menu_items`/`variants`; for `menu_item_variants`, prefer **upsert by `order_item_id` PK** and set `is_verified` from snapshot when present.

### 5.5 Client work

| Step | Action |
|------|--------|
| 1 | Extend `export_to_backups` or add `export_catalog_snapshot()` including flags |
| 2 | Extend `menu_bootstrap_shipper` / new shipper to POST full snapshot with `Content-Type` + size limits |
| 3 | Extend `fetch_and_apply_menu_bootstrap_snapshot` (or sibling) to parse new fields and update `is_verified` |
| 4 | UI: show `snapshot_version` + last applied in Configuration |

### 5.6 Risks

- **Payload size** — may need gzip + S3-style storage with client fetching URL if JSON > 5–20 MB.  
- **Race with POS** — snapshot must be **consistent** with a declared `stream_id` / `orders_through` watermark or accept eventual consistency.

---

## 6. Option C — Shipped DB / backup snapshot (detailed plan)

### 6.1 Intent

For **support**, **factory reset**, or **air-gapped** rollout, ship **`analytics.db`** or a **menu-only SQLite** + instructions.

### 6.2 Work

| Step | Action |
|------|--------|
| 1 | Document secure distribution (code signing, checksum) |
| 2 | Optional: `scripts/package_reference_db.sh` producing encrypted blob |
| 3 | Client “Import database…” flow (large UX + safety) |

### 6.3 Limits

Does **not** stay current with cloud or POS unless repeated. Best as **bootstrap** only, then Options A/B take over.

---

## 7. Recommended phased roadmap

### Phase 0 — Instrumentation, metrics, and debug-session cleanup (1–3 days) — ✅ IMPLEMENTED (grep gate clean)

**Product metrics (forward work)**

- Add **configurable** logging or metrics: counts of `menu_item_variants` by `is_verified`, events pending upload, last bootstrap version applied.  
- Prefer **structured application logs** (level-based, env-gated) over ad-hoc file paths.

**Debug-mode / agent instrumentation cleanup (required hygiene)**

When using Cursor **debug mode** (NDJSON file, HTTP ingest, or `#region agent log` blocks), treat instrumentation as **temporary**:

| Step | Owner | Action |
|------|--------|--------|
| 1 | Implementer | Land instrumentation only on a **branch** or keep it **uncommitted** until hypotheses are proven. |
| 2 | Before merge to `main` | **Remove** all `#region agent log` / hard-coded paths under `.cursor/debug-*.log` / `fetch(.../ingest/...)` blocks. |
| 3 | Grep gate | Run `rg "debug-56943a|agent log|127\.0\.0\.1:7919/ingest"` (adjust session id / port) and expect **no hits** in `src/`, `services/`, `scripts/`. |
| 4 | Optional | Delete the session log file under `.cursor/` if it exists (safe; it is not source of truth). |

**Baseline cleanup (2026-04, Resolutions investigation)**

The following files **were** instrumented for session `56943a` and are **restored to production-only code** as part of this plan’s hygiene checklist (verify in git if reintroduced):

- `src/api/routers/menu.py` — `get_unverified` entry/exit NDJSON writes  
- `services/clustering_service.py` — `OrderItemCluster.add` mapping insert logs + module counters  
- `scripts/seed_from_backups.py` — post-seed `is_verified` count logs  

**Policy:** No merge may ship **absolute paths** to a developer machine (e.g. `/Users/.../.cursor/debug-....log`) in application code. Future Phase 0 metrics should use existing logging (`logging` + optional file in userData) if needed.

### Phase 1 — Make “verify” cloud-visible (Option A minimal) (1–2 weeks) — ✅ IMPLEMENTED

- Emit events for **`verify_item`** and **successful `resolve_menu_item_variant`** outcomes that change verification.  
- Cloud ingest + pull + client apply (small batches).  
- **Delivers:** S4 auditability; partial S1 if combined with ordering rules.

### Phase 2 — Snapshot checkpoints (Option B) (2–4 weeks) — ◑ PARTIALLY SUPERSEDED (event stream + bootstrap watermark)

- Versioned snapshot including **`is_verified`** in mapping representation.  
- Apply after seed on fresh installs + nightly “repair” job on client.  
- **Delivers:** faster convergence; reduced event replay cost.

### Phase 3 — Clustering alignment (parallel track) — ✅ IMPLEMENTED (C1 heuristic)

Even with A+B, clustering currently inserts **`is_verified = 0`** for new POS lines. Pick one:

- **C1:** When inserting mapping for a catalog hit, set **`is_verified = 1`** if `(menu_item_id, variant_id)` already has **any** verified mapping elsewhere (heuristic).  
- **C2:** After POS sync, run **“replay verifications”** pass: match by `name_raw` + deterministic variant key (fragile).  
- **C3:** Prefer **canonical** `(menu_item_id, variant_id)` only in `menu_item_variants` and store POS line linkage elsewhere (larger schema change).

Document the chosen approach in `item_clustering.md` follow-up.

### Phase 4 — Optional packaged DB (Option C) — ○ NOT BUILT

Support / enterprise only.

---

## 8. Cross-cutting concerns

### 8.1 Tenant and multi-store

All new endpoints must follow **Section 4.2** (tenant scoping) in the API contract.

### 8.2 Ordering with existing pulls

Document final order in `cloud_pull_orchestrator.py`, e.g.:

1. Menu bootstrap / catalog snapshot (broad structure)  
2. Mapping verification events (fine corrections)  
3. Menu merge events (structural merges)  
4. Customer merges  

(Exact order is a design decision — must be consistent on server replay too.)

### 8.3 Writable seed path (related bug class)

Today `export_to_backups` uses `get_resource_path("data")`, which in PyInstaller points at **`_internal/data`** inside the `.app` and can **mutate the bundled seed** after install. A separate initiative should move **writable exports** to `get_data_path("data")` next to `analytics.db` so **reset + seed** always reads **pristine** packaged JSON unless intentionally updated.

Reference: `src/core/utils/path_helper.py` (`get_resource_path` vs `get_data_path`).

### 8.4 Testing matrix (minimum)

| Scenario | Expected |
|----------|----------|
| Verify on A → Push → Pull on B | B shows no resolution for that mapping |
| Merge on A → Push → Reset+sync on B | B matches merge result |
| Snapshot v3 applied on empty DB | Row counts + flags match snapshot |
| POS sync after snapshot | Unverified only for genuinely new SKUs |

---

## 9. Open decisions (product)

1. Is **“empty Resolutions”** the definition of done, or is **“identical row counts”** required?  
2. Should **automatic** verify ever be allowed (C1–C3), or only human-driven events?  
3. Maximum acceptable **snapshot** size and gzip threshold?  
4. **Retention** for verification events on server (30 / 90 / 365 days)?

---

## 10. Summary

Today, **“all verified” on dev does not imply Dachnona holds a complete copy of that state**, because many verifications are **local SQLite + JSON export** only. To approach **single source of truth**, implement **deliberate cloud replication** for verification state (**Option A**), **optionally** add **versioned catalog snapshots** (**Option B**), and align **clustering** so POS re-import does not **reopen** resolved work unnecessarily. **Option C** remains a **bootstrap** tool, not the ongoing truth.

This document is the implementation plan. **Signed off 2026-07-06** — implemented phases validated against prod (see §0); remaining Section 9 open decisions are product-policy questions that do not block multi-install bring-up (current de-facto answers: S1 "empty Resolutions" is the definition of done; automatic verify via the C1 heuristic is allowed; snapshot size and event retention untouched defaults).

---

## 11. Appendix — PR checklist for any future debug instrumentation

Before opening a PR that touched sync, menu, or seeding:

- [x] No `#region agent log` / session-specific NDJSON writers remain. _(grep gate clean, 2026-07-06)_  
- [x] No hard-coded `.cursor/debug-` paths or local ingest URLs in `src/`, `services/`, `utils/`, or `scripts/`. _(grep gate clean, 2026-07-06)_  
- [x] If metrics are needed permanently, they use **`logging`** or a **configurable** sink, not a fixed workspace path.  
- [ ] Document new metrics in `TROUBLESHOOTING.md` or this file under Phase 0.
