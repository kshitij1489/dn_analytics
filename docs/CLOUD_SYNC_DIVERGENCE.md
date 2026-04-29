# Cloud sync divergence — analysis and remediation

**Audience:** Engineers working on desktop analytics (SQLite), Dachnona cloud sync, and multi-install collaboration.  
**Status:** Operational guide + proposed engineering direction.  
**Related:** [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md), [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md), [FORECASTING_AND_SYNC.md](./FORECASTING_AND_SYNC.md).

---

## 1. What “divergence” means here

In this project, **sync is not full multi-master replication** of the local SQLite database. The pipeline roughly separates:

| Layer | What syncs | Role |
|-------|------------|------|
| **Orders** | Incremental order stream from the configured integration | Builds/updates `orders`, customers derived from POS, etc. |
| **Cloud collaboration** | Append-only **events** (customer merges, menu merges, mapping verifications, menu bootstrap snapshots) | Converges **human-driven** catalog and identity decisions across installs |

**Divergence** means: two installs both “successfully” complete **Sync DB** (orders OK, HTTP 200), but their SQLite still differs in ways that cause **cloud pull replay** to log errors such as:

- `Menu merge pull failed … Source or Target item not found`
- `Customer merge pull failed … Could not resolve source customer …`
- `Cloud pull menu_merges reported error: …`
- `Cloud pull customer_merges reported error: …`

Cloud pulls are **best-effort** by design: failures are **logged**, not raised, so the job can still finish “successfully” while individual events fail ([`src/core/services/cloud_pull_orchestrator.py`](../src/core/services/cloud_pull_orchestrator.py)).

---

## 2. Symptoms and how to confirm

### 2.1 Log signatures

During or after sync, look for:

1. **Per-event warnings** from [`menu_merge_sync.py`](../src/core/menu_merge_sync.py) / [`customer_merge_sync.py`](../src/core/customer_merge_sync.py) when applying one remote event throws.
2. **Summary warnings** from `run_best_effort_cloud_pulls`: `Cloud pull <stream> reported error: <message>`.
3. **Missing pull cursors** in `system_config` for keys such as `menu_merge_pull_cursor` and `customer_merge_pull_cursor` — if pulls abort mid-batch on error, cursors may never advance, so the **same failing events can retry** on every sync.

### 2.2 SQLite checks (diagnostic)

Use these patterns on `analytics.db` (adjust IDs from your logs):

**Menu merge — does the referenced source `menu_item_id` exist?**

```sql
SELECT menu_item_id, name, type
FROM menu_items
WHERE menu_item_id IN ('<source_uuid>', '<target_uuid>');
```

If the **source** row is missing but the cloud event still describes a merge from that UUID, replay will fail until catalog state contains that row or the event is superseded/skipped.

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

**Implication:** divergence is a **class** of issues, not only a bug in a single run. The [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md) explicitly treats **full bitwise parity (S2)** across two installs as **hard / best-effort** unless you approach full replication or stronger snapshots.

### 3.2 Menu catalog divergence

**Mechanism:** Menu merge replay calls into `utils/menu_utils` merge helpers. If the **source** `menu_item_id` does not exist in `menu_items` (and cannot be treated as present via existing merge history / mappings), operations return errors such as **Source or Target item not found**.

**Typical reasons:**

| Cause | Explanation |
|-------|-------------|
| **Different merge order across installs** | Install A merged UUID `X → Z` first; install B never had row `X` because B already represented the product under another UUID or merged along a different edge first. The cloud may still serve an event `X → Z` from the origin device. |
| **Bootstrap / catalog lag** | This install has not yet applied a menu bootstrap snapshot that includes `X`, or POS-derived clustering created different variant rows than the reference machine. |
| **Id remapping vs stable UUID** | If any path recreated catalog rows with new IDs without updating all references, UUID-level replay will disagree with local reality. |
| **Partial `_ensure_menu_item_exists` behavior** | Remote handler can insert a stub **target** from payload when name/type exist; **source** must still exist for structural merges that move sales/history off the source row. |

### 3.3 Customer identity divergence

**Mechanism:** Replay resolves customers via [`_resolve_customer_id`](../src/core/customer_merge_sync.py) using `portable_locators` and normalized snapshots — **not** using `local_refs.source_customer_id` from another machine as the primary key.

**Typical reasons:**

| Cause | Explanation |
|-------|-------------|
| **Autoincrement `customer_id` is local** | The merge event’s `local_refs` integers describe the **origin** SQLite only. Other installs must infer rows from locators. |
| **Weak locators** | `customer_identity_key` absent or stripped for `anon:*` keys; `phone_hash` / `name_address_hash` null; many customers share the same normalized name → resolver returns **ambiguous** and raises. |
| **Order reassignment drift** | The order cited in `moved_orders` may attach to a **different** local `customer_id` than the IDs on the originating machine if imports or prior merges differed. |

Portable locator construction is in [`customer_merge_sync_events.py`](../src/core/customer_merge_sync_events.py) (`_build_portable_locators`).

### 3.4 Operational: pull cursor not advancing

[`pull_and_apply_menu_merge_events`](../src/core/menu_merge_sync.py) / [`pull_and_apply_customer_merge_events`](../src/core/customer_merge_sync.py) stop the batch on first exception, set `stats["error"]`, and **may not** update the pull cursor for that stream if the implementation returns before `set_*_pull_cursor`. Repeated syncs then **re-fetch the same head of the queue**, amplifying noise.

---

## 4. Goals for “fixing” divergence

Define success per scope:

| Scope | Goal |
|-------|------|
| **G1 — User-visible** | Sync completes; orders correct; **warnings understandable** and optionally surfaced in UI with actionable detail (event id, reason class). |
| **G2 — Convergence** | New installs and long-running installs **eventually apply** the same merge decisions **when** catalog + identity prerequisites are satisfied. |
| **G3 — Strict parity** | Two installs with same cloud cursors and same POS stream produce **equivalent** critical rows (strongest; see S2 discussion in [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md)). |

Most practical roadmaps target **G1 + G2** first; **G3** requires the largest product/backend investment.

---

## 5. Proposed solutions (ordered)

### 5.1 Operational and support (no schema change)

1. **Document expected warnings** in release notes when portable locators are sparse (anonymous customers, duplicate names).
2. **Support playbook:** For a stuck event id, capture: cloud payload (redacted), local `menu_items` rows for UUIDs, count of name-colliding customers, and whether `menu_merge_pull_cursor` / `customer_merge_pull_cursor` exist.
3. **Manual convergence (dangerous, rare):** Only with backup — e.g. insert missing stub `menu_items` / reconcile duplicates under guidance. Prefer engineering fixes below.

### 5.2 Client-side robustness (desktop repo)

| Idea | Purpose | Notes |
|------|---------|------|
| **Advance cursor on poison-pill events** | Skip or dead-letter events that can never apply (after N attempts or deterministic failure class), store `remote_event_id` in a **skipped** table, advance cursor | Prevents infinite retry spam; requires clear policy so merges are not silently “lost” without audit |
| **Richer skip metadata** | Record `{remote_event_id, error_class, payload_hash, attempted_at}` locally | Support + future replay |
| **Resolve menu source stubs** | When cloud sends full `source_item` `{name, type, menu_item_id}`, optionally **upsert source** the same way target is ensured, then merge | Reduces “source missing” if the only issue was row absence |
| **Customer merge: tie-break strategy** | When name-only collision, use **order anchors** from `moved_orders.portable_refs` (e.g. match `petpooja_order_id` → `orders` row → `customer_id`) if unique | Uses data already in many payloads; must handle collisions carefully |
| **UI surfacing** | Show “Cloud replay: 2 events skipped (details)” from last sync summary | Makes best-effort behavior visible |

### 5.3 Stronger portable identity (client + policy)

1. **Promote stable keys:** Prefer assigning/persisting non-anonymous `customer_identity_key` where business rules allow; reduce reliance on name-only matching.
2. **Enrich portable locators on emit:** When phone/address exist on **any** linked address row, include hashes (already partially supported via address book paths in `_resolve_customer_id`).
3. **Avoid exporting merges** until mandatory locators exist — product decision (may block legitimate merges).

### 5.4 Catalog convergence (client + cloud)

Align with [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md):

1. **Richer or more frequent menu bootstrap** so every install has the **UUID set** needed before merge replay (Option B in that doc).
2. **Verification / resolution events** so “silent” local changes do not leave cloud-only knowledge incomplete (Option A).
3. **Server-side ordering:** Serve merge events **after** catalog snapshots the client has acknowledged — reduces races on fresh installs.

### 5.5 Backend / contract extensions

Discuss with Dachnona owners (see [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md)):

- **Dead-letter / compensate:** Mark events as failed store-wide with reason, optionally emit **compensating** events if catalog changes.
- **Compact redundant merges:** If cloud knows `X` and `Y` merged into `Z` via separate events, expose a **resolved graph** or merged **checkpoint** for clients that missed intermediate UUIDs.

---

## 6. Suggested prioritization

1. **Short term:** Cursor / poison-pill handling + surfacing last sync errors in UI + customer resolution enhancement using order anchors when present.  
2. **Medium term:** Menu source stub creation or mandatory bootstrap coverage for UUIDs referenced by pending merge events.  
3. **Long term:** Single-source-of-truth milestones (bootstrap + verification events) per [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md); revisit strict cross-install parity (S2) only if product requires it.

---

## 7. Non-goals

- Promising **bit-identical SQLite** across all installs without defining **S2**-level infrastructure.
- Treating every cloud pull warning as a **failed orders sync** — orders and collaboration replay are separate stages ([`src/api/routers/operations.py`](../src/api/routers/operations.py)).

---

## 8. References (implementation)

| Topic | Location |
|-------|----------|
| Best-effort cloud pull orchestration | [`src/core/services/cloud_pull_orchestrator.py`](../src/core/services/cloud_pull_orchestrator.py) |
| Menu merge pull/apply | [`src/core/menu_merge_sync.py`](../src/core/menu_merge_sync.py) |
| Customer merge pull/apply | [`src/core/customer_merge_sync.py`](../src/core/customer_merge_sync.py) |
| Portable locators for customer events | [`src/core/customer_merge_sync_events.py`](../src/core/customer_merge_sync_events.py) |
| Menu merge upload | [`src/core/menu_merge_shipper.py`](../src/core/menu_merge_shipper.py) |
| Customer merge upload | [`src/core/customer_merge_shipper.py`](../src/core/customer_merge_shipper.py) |

---

## Document history

| Date | Change |
|------|--------|
| 2026-04-30 | Initial version: divergence definition, root causes, solution tiers, cross-links. |
