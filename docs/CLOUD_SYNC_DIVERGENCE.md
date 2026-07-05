# Cloud sync divergence — analysis and remediation

**Audience:** Engineers working on desktop analytics (SQLite), Dachnona cloud sync, and multi-install collaboration.  
**Status:** Operational runbook (symptoms + diagnostics). **Menu** remediation is largely implemented — see [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) (phases C1–C5 client, S1–S2 server; C6/S3 convergence digest not started). **Customer** divergence remains best-effort; this doc still applies there.  
**Related:** [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md), [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md), [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md), [FORECASTING_AND_SYNC.md](./FORECASTING_AND_SYNC.md).

---

## 1. What “divergence” means here

In this project, **sync is not full multi-master replication** of the local SQLite database. The pipeline roughly separates:

| Layer | What syncs | Role |
|-------|------------|------|
| **Orders** | Incremental order stream from the configured integration | Builds/updates `orders`, customers derived from POS, etc. |
| **Cloud collaboration** | Append-only **events** (customer merges, menu merges, mapping verifications, menu bootstrap snapshots) | Converges **human-driven** catalog and identity decisions across installs |

**Divergence** means: two installs both “successfully” complete **Sync DB** (orders OK, HTTP 200), but their SQLite still differs in ways that cause **cloud pull replay** to log errors or land events in **quarantine**, such as:

- `Menu merge pull quarantined event …` (current) or legacy `Menu merge pull failed … Source or Target item not found`
- `Customer merge pull failed … Could not resolve source customer …`
- `Cloud pull menu_merges reported error: …`
- `Cloud pull customer_merges reported error: …`

Cloud pulls are **best-effort** by design: per-stream failures are **logged** (and for menu streams, **quarantined**), not raised, so the job can still finish “successfully” while individual events fail ([`src/core/services/cloud_pull_orchestrator.py`](../src/core/services/cloud_pull_orchestrator.py)).

---

## 2. Symptoms and how to confirm

### 2.1 Log signatures

During or after sync, look for:

1. **Per-event warnings** from [`menu_merge_sync.py`](../src/core/menu_merge_sync.py) / [`customer_merge_sync.py`](../src/core/customer_merge_sync.py) when applying one remote event throws (menu merge failures are quarantined; see §3.4).
2. **Summary warnings** from `run_best_effort_cloud_pulls`: `Cloud pull <stream> reported error: <message>`.
3. **Quarantine rows** in `menu_sync_event_quarantine` or via `GET /api/menu/sync-conflicts` — unresolved menu merge / mapping-verification / push-reject events with `fail_count`, `error`, and `remote_event_id`.
4. **Supersede notices** (menu only, post–assignment apply): a peer’s resolution outranked a locally acknowledged decision; listed in the same API response.

**Pull cursors (menu streams, post–C1):** cursors **advance past** failed events; failures do not stall the stream. Check `menu_merge_pull_cursor` / `menu_mapping_verification_pull_cursor` in `system_config` — they should move forward even when `events_quarantined > 0`.

**Pull cursors (customer stream):** still uses the older batch semantics; a top-level exception can leave the cursor unchanged and retry the same head of the queue.

### 2.2 SQLite checks (diagnostic)

Use these patterns on `analytics.db` (adjust IDs from your logs):

**Menu merge — does the referenced `menu_item_id` exist?**

```sql
SELECT menu_item_id, name, type
FROM menu_items
WHERE menu_item_id IN ('<source_uuid>', '<target_uuid>');
```

On current clients, assignment-based apply ([`menu_assignment_apply.py`](../src/core/menu_assignment_apply.py)) writes per-`order_item_id` targets and can `_ensure_menu_item_exists` from the event snapshot — missing catalog rows are less common than under legacy cluster replay. If both rows are absent and the event payload lacks snapshot fields, the event may quarantine until catalog bootstrap catches up.

**Menu merge — assignment state for a disputed order line**

```sql
SELECT miv.order_item_id, miv.menu_item_id, miv.variant_id, miv.is_verified,
       miv.assignment_seq, miv.pending_local
FROM menu_item_variants miv
WHERE miv.order_item_id = '<order_item_id_from_event>';
```

`assignment_seq` is the highest `server_seq` applied; `pending_local = 1` means a local edit not yet echoed from the server.

**Customer merge — ambiguous resolution**

```sql
SELECT customer_id, name, phone, address, total_orders, total_spent
FROM customers
WHERE lower(trim(name)) = lower(trim('<name_from_event>'));
```

Many rows with the same normalized name and **no** usable `customer_identity_key` / phone / address hashes in the remote payload → local resolver cannot pick a unique source/target ([`_resolve_customer_id`](../src/core/customer_merge_sync.py)).

**Orders vs merge intent**

Compare `orders.customer_id` for the portable order refs in the cloud payload (`petpooja_order_id`, `event_id`) with `local_refs.source_customer_id` / `target_customer_id` from the machine that originated the merge. **Integer customer IDs are not portable** across databases; mismatch here is expected when portable locators are weak.

---

## 3. Root cause analysis

### 3.1 Architectural: event sourcing without guaranteed shared snapshot

Remote installs replay **events** that reference **stable UUIDs** (menu items) and **portable customer descriptors** (hashes, identity keys, snapshots). That only yields identical outcomes if:

- Every install has the **same catalog rows** for every UUID mentioned by menu merge events, and  
- Every install can **uniquely map** each descriptor to at most one local `customers` row.

Neither is guaranteed by “orders sync alone.” The contract explicitly notes that solutions should **not** depend on **local SQLite IDs** from the originating desktop ([DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) §3 / §4 themes).

**Implication:** divergence is a **class** of issues, not only a bug in a single run. Full **bit-identical SQLite** across installs (G3) remains hard unless you approach full replication or stronger snapshots ([MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md)).

### 3.2 Menu catalog divergence

**Historical mechanism (pre–assignment apply):** Menu merge replay called into `utils/menu_utils` cluster-shaped merge helpers. If the **source** `menu_item_id` did not exist in `menu_items`, operations returned **Source or Target item not found**. Conflicting merges could also “succeed” as silent no-ops (source resurrected from snapshot while order lines stayed local) or fail permanently while peers kept divergent answers.

**Current mechanism (C3 + S2, fleet on phase-2):** Events carry explicit `assignments` (`order_item_id → menu_item_id, variant_id, is_verified`). Clients apply in **server ingest order** (`server_seq` = event row `id`) with per-key last-write-wins and an `assignment_seq` guard ([`menu_assignment_apply.py`](../src/core/menu_assignment_apply.py)). The server materializes the same rule in `OrderItemAssignment` and exposes a snapshot for fresh installs ([MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) §2–3). Conflicting resolutions on the same order line **converge** to the higher `server_seq`; the superseded user gets a notice.

Per-key LWW applies to `menu_item_id` / `variant_id`. `is_verified` is **not** merge-stream LWW: the merge apply only *seeds* it when creating a new assignment row, and the separate mapping-verification stream (its own `verification_seq`) is the sole owner of the flag on existing rows. Every flag-changing operation — resolve, remap, verify/reopen, bulk actions, and undo — emits a verification event, so the flag converges independently of merge/verification ordering.

Legacy cluster replay remains only as a fallback when no assignments can be derived (`MENU_SYNC_ASSIGNMENT_APPLY` off or malformed legacy payload).

**Residual causes** (mostly catalog/bootstrap gaps, not conflict semantics):

| Cause | Explanation |
|-------|-------------|
| **Bootstrap / catalog lag** | Install has not yet applied menu bootstrap / snapshot rows for UUIDs referenced in an event; `_ensure_menu_item_exists` cannot stub. |
| **Pre-upgrade fleet** | An install still on pre–C3 client code replays cluster-shaped events and can diverge until upgraded (cursor reset → re-pull → assignment apply heals state). |
| **Id remapping vs stable UUID** | If any path recreated catalog rows with new IDs without updating all references, UUID-level replay disagrees with local reality. |
| **Orders never seen locally** | Assignments for `order_item_id`s with no local `orders` row are skipped by design (this install did not import that order). |

### 3.3 Customer identity divergence

**Mechanism:** Replay resolves customers via [`_resolve_customer_id`](../src/core/customer_merge_sync.py) using `portable_locators` and normalized snapshots — **not** using `local_refs.source_customer_id` from another machine as the primary key.

**Typical reasons:**

| Cause | Explanation |
|-------|-------------|
| **Autoincrement `customer_id` is local** | The merge event’s `local_refs` integers describe the **origin** SQLite only. Other installs must infer rows from locators. |
| **Weak locators** | `customer_identity_key` absent or stripped for `anon:*` keys; `phone_hash` / `name_address_hash` null; many customers share the same normalized name → resolver returns **ambiguous** and raises. |
| **Order reassignment drift** | The order cited in `moved_orders` may attach to a **different** local `customer_id` than the IDs on the originating machine if imports or prior merges differed. |

Portable locator construction is in [`customer_merge_sync_events.py`](../src/core/customer_merge_sync_events.py) (`_build_portable_locators`).

Customer merges benefit from **server ingest ordering** (S1) but have **no** assignment-based applier or quarantine parity with menu streams yet.

### 3.4 Operational: quarantine vs cursor advancement

**Menu merge and mapping-verification pulls (C1):** [`pull_and_apply_menu_merge_events`](../src/core/menu_merge_sync.py) and [`pull_and_apply_menu_mapping_verification_events`](../src/core/menu_mapping_verification_sync.py) wrap each event in try/except. On failure they **insert or bump** [`menu_sync_event_quarantine`](../src/core/menu_sync_quarantine.py), **continue the batch**, and **advance the pull cursor**. A `retry_quarantined_*` pass runs at the start of each pull. Push rejects (C2) land in the same table under `stream='*_push'`.

**Customer merge pulls:** unchanged from the original risk model — a batch-level exception can still set `stats["error"]` and leave the cursor at the failing position.

**User surfacing:** `GET /api/menu/sync-conflicts` lists quarantined events and supersede notices; `POST …/dismiss` and `POST …/notices/{id}/acknowledge` for resolution. UI badge is not shipped yet (API only).

---

## 4. Goals for “fixing” divergence

Define success per scope:

| Scope | Goal | Menu status | Customer status |
|-------|------|-------------|-----------------|
| **G1 — User-visible** | Sync completes; orders correct; failures understandable and surfaced with actionable detail | API shipped; UI badge open | Warnings in logs only |
| **G2 — Convergence** | Installs eventually apply the same merge decisions when prerequisites are satisfied | **Implemented** (assignment LWW + snapshot seed + bootstrap demotion) | **Open** (locator / resolver work) |
| **G3 — Strict parity** | Equivalent critical rows at same cloud cursors + same POS stream | Monitoring via C6/S3 digest (not started) | Not targeted |

Most practical roadmaps target **G1 + G2** first; **G3** requires the largest product/backend investment.

---

## 5. Remediation (ordered)

### 5.1 Operational and support (no schema change)

1. **Document expected warnings** in release notes when portable locators are sparse (anonymous customers, duplicate names).
2. **Support playbook:** For a stuck event id, capture: cloud payload (redacted), local `menu_items` rows for UUIDs, `menu_sync_event_quarantine` row (menu) or pull error (customer), count of name-colliding customers, pull cursor values, and `GET /api/menu/sync-conflicts` output.
3. **Manual convergence (dangerous, rare):** Only with backup — e.g. insert missing stub `menu_items` / reconcile duplicates under guidance. Prefer engineering fixes below.

### 5.2 Client-side robustness (desktop repo)

| Idea | Purpose | Status |
|------|---------|--------|
| **Quarantine + advance cursor on poison-pill events** | Failed events recorded in `menu_sync_event_quarantine`; cursor advances; retry on next pull | **Done** (C1) — [`menu_sync_quarantine.py`](../src/core/menu_sync_quarantine.py) |
| **Richer skip metadata** | `{remote_event_id, stream, payload, error, fail_count, first/last_failed_at}` | **Done** (C1/C2) |
| **Assignment-based menu apply** | Per-`order_item_id` LWW on `server_seq`; no dependence on source cluster still existing | **Done** (C3) — [`menu_assignment_apply.py`](../src/core/menu_assignment_apply.py) |
| **Push per-event accept/reject** | Malformed events rejected individually, not 400-blocking the batch | **Done** (C2 client + S1 server) |
| **Background pull + push nudge** | Shrink conflict window between scheduler cycles | **Done** (C5) |
| **Resolve menu source stubs** | Upsert missing source from event snapshot before cluster merge | **Superseded** for normal path by assignment apply + `_ensure_menu_item_exists` |
| **Customer merge: tie-break strategy** | Use **order anchors** from `moved_orders.portable_refs` when name-only collision | **Open** |
| **UI surfacing** | Badge / list for quarantine + supersede notices | **Partial** — API only (`/api/menu/sync-conflicts`) |

Full design and acceptance criteria: [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md).

### 5.3 Stronger portable identity (client + policy) — customer focus

1. **Promote stable keys:** Prefer assigning/persisting non-anonymous `customer_identity_key` where business rules allow; reduce reliance on name-only matching.
2. **Enrich portable locators on emit:** When phone/address exist on **any** linked address row, include hashes (already partially supported via address book paths in `_resolve_customer_id`).
3. **Avoid exporting merges** until mandatory locators exist — product decision (may block legitimate merges).

### 5.4 Catalog convergence (client + cloud) — menu

Align with [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) and [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md):

| Item | Status |
|------|--------|
| **Server ingest order** (`(ingested_at, id)`, `server_seq` on delta) | **Done** (S1) |
| **Materialized `OrderItemAssignment` + snapshot endpoint** | **Done** (S2; migration 0012 must be applied at deploy) |
| **Fresh-install snapshot seed** instead of full history replay | **Done** (C4) |
| **Bootstrap demotion** (`seed_only`, no routine `order_items` relink) | **Done** (C4 + S2.5) |
| **Verification / resolution events** for silent local changes | Partially — verification stream is flag-only; mapping corrections ride merge assignments |
| **Convergence digest** (client C6 + server S3) | **Not started** |

### 5.5 Backend / contract extensions

Discuss with Dachnona owners (see [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md)):

- **Customer-stream quarantine** — same per-event dead-letter semantics as menu (today menu-only).
- **Dead-letter / compensate:** Mark events as failed store-wide with reason, optionally emit **compensating** events if catalog changes.
- **Compact redundant merges:** Largely addressed for menu by materialized assignments; may still help customer or legacy v1 tails.

---

## 6. Suggested prioritization

1. **Now (customer):** Portable-locator enrichment + order-anchor tie-break for ambiguous customer merges; optional customer-stream quarantine mirroring C1.  
2. **Now (ops):** Deploy S2 migration + `rebuild_order_item_assignments`; upgrade fleet to phase-2 client; UI for `/api/menu/sync-conflicts`.  
3. **Next (monitoring):** C6/S3 convergence digest — standing answer to “are all installs in sync?”  
4. **Long term:** Revisit strict cross-install parity (G3) only if product requires it.

---

## 7. Non-goals

- Promising **bit-identical SQLite** across all installs without G3-level infrastructure and monitoring.
- Treating every cloud pull warning as a **failed orders sync** — orders and collaboration replay are separate stages ([`src/api/routers/operations.py`](../src/api/routers/operations.py)).

---

## 8. References (implementation)

| Topic | Location |
|-------|----------|
| Menu merge conflict design (authoritative for menu) | [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) |
| Best-effort cloud pull orchestration | [`src/core/services/cloud_pull_orchestrator.py`](../src/core/services/cloud_pull_orchestrator.py) |
| Menu merge pull/apply | [`src/core/menu_merge_sync.py`](../src/core/menu_merge_sync.py) |
| Assignment extraction + seq-guarded apply | [`src/core/menu_assignment_apply.py`](../src/core/menu_assignment_apply.py) |
| Fresh-install assignment snapshot seed | [`src/core/menu_assignment_bootstrap.py`](../src/core/menu_assignment_bootstrap.py) |
| Quarantine helpers | [`src/core/menu_sync_quarantine.py`](../src/core/menu_sync_quarantine.py) |
| Sync conflicts API | [`src/api/routers/menu.py`](../src/api/routers/menu.py) (`/api/menu/sync-conflicts`) |
| Customer merge pull/apply | [`src/core/customer_merge_sync.py`](../src/core/customer_merge_sync.py) |
| Portable locators for customer events | [`src/core/customer_merge_sync_events.py`](../src/core/customer_merge_sync_events.py) |
| Menu merge upload | [`src/core/menu_merge_shipper.py`](../src/core/menu_merge_shipper.py) |
| Customer merge upload | [`src/core/customer_merge_shipper.py`](../src/core/customer_merge_shipper.py) |
| Background pull scheduler | [`src/core/services/cloud_sync_scheduler.py`](../src/core/services/cloud_sync_scheduler.py) |

---

## Document history

| Date | Change |
|------|--------|
| 2026-04-30 | Initial version: divergence definition, root causes, solution tiers, cross-links. |
| 2026-07-05 | Refresh after menu merge conflict work (C1–C5, S1–S2): assignment-based apply, quarantine/cursor behavior, implementation status tables, customer gaps unchanged. |
