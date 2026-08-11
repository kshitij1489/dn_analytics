# Order Ingest Pipeline

> **For AI agents:** Start at [INDEX.md](./INDEX.md) for routing. This doc is the as-built reference for how raw POS orders become rows in the local DB — the `services/load_orders.py` path and its interaction with clustering, stats, addons, and customer identity. Read this before touching order loading, replay, or stat-count bugs.
>
> **Last updated:** 2026-07-10

Menu **sync/convergence** (cross-install LWW) is a different concern — see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md). This doc is about turning one PetPooja order event into local rows correctly and idempotently.

---

## 1. Where orders come from

The orders stream is **append-only** and sourced from PetPooja (POS) via the cloud pull path:

```
cloud pull orchestrator ─▶ order events (JSON payloads, each with stream_id/event_id)
        │
        ▼
services/load_orders.process_order(conn, payload, item_cluster)
        │
        ├─ resolve restaurant / customer identity
        ├─ upsert order header
        ├─ (re-ingest) wipe + rebuild child rows
        ├─ cluster each item / addon  ─▶ services/clustering_service.OrderItemCluster.add()
        ├─ write order_items / order_item_addons / order_taxes / order_discounts
        ├─ update or recompute menu_item + customer aggregates
        └─ single commit
```

Entry points:
- **Incremental**: `make sync` → `src/core/services/sync_service.sync_database()` → per-order `process_order`.
- **Full reload**: `make reload-all`.
- The incremental watermark is `MAX(stream_id)` over the `orders` table (see §3).

---

## 2. Atomic ingest — one order, one transaction

**Invariant:** an order's header, items, addons, taxes, and discounts land as **one atomic commit**, or not at all.

Why it matters: the incremental sync uses `MAX(stream_id)` as its resume cursor. If the header were committed before the items and item loading then failed, the watermark would advance past an order that is only **partially** loaded — the next sync would skip it and the order would be permanently missing rows. So:

- `process_order` upserts the header but **does not commit it**.
- All children are written on the same connection/transaction.
- There is exactly **one `conn.commit()` at the end**. `stats['orders'] = 1` is set **only after** that commit succeeds.
- Anything that needs its own transaction (deferred verification flush, see §7) runs **after** the order is durably committed and is wrapped in a guarded `try/except` so it can never fail the already-saved order.

**Do not** add a mid-order `conn.commit()`. Several helpers used during ingest were specifically written to avoid committing (e.g. `_ensure_customer_no_stats`, §5) precisely to preserve this guarantee.

---

## 3. Event replay — the same orderID comes back

PetPooja **re-sends the same `orderID`** whenever an order is edited, cancelled, or its status changes. Each re-send is a **new `stream_id`** on the same aggregate. We treat the **latest event as full truth**.

`process_order` detects replay by looking up the existing order:

```sql
SELECT order_id, customer_id FROM orders WHERE petpooja_order_id = ?
```

If the order already exists (`exists` truthy), it is a **re-ingest**:

1. **Header** is refreshed via `INSERT … ON CONFLICT(petpooja_order_id) DO UPDATE SET …` — every mutable header column is overwritten from the new event (status, totals, customer_id, timestamps, etc.), not just `stream_id`.
2. **Children are wiped and rebuilt**, in FK-safe order:
   - capture the set of `menu_item_id`s this order previously touched (items **and** addons) — needed because an edit may **drop** an item, and that item's aggregates must still be recomputed;
   - `DELETE FROM order_item_addons` (via its parent `order_items`), then `order_items`, `order_taxes`, `order_discounts`;
   - re-insert all children from the new payload.

This wipe-and-rebuild is why replays don't accumulate duplicate `order_items`/`order_item_addons` rows.

### Stats: inline increment vs. recompute

Menu-item counters (`total_sold`, `sold_as_item`, `total_revenue`, `sold_as_addon`, …) live denormalized on `menu_items`. There are **two code paths**:

| Case | How counters update |
|------|--------------------|
| **New order** (`not exists`) | Inline `UPDATE menu_items SET total_sold = total_sold + …` per item/addon. Fast path. Only for `order_status = 'Success'`. |
| **Re-ingest** (`exists`) | **Skip** inline increments. After children are rebuilt, recompute each touched `menu_item_id` from source rows via `utils/menu_utils._recalculate_menu_item_stats` (status-aware, idempotent). |

The recompute set is the **union** of items touched before the edit (captured pre-delete) and items present after — so an item removed by an edit is corrected downward, and a newly added item is corrected upward.

> **Gotcha:** if you add a new denormalized counter on `menu_items`, you must update it in **both** the inline increment block **and** `_recalculate_menu_item_stats`, or replays will silently drift from first-ingest values.

---

## 4. Item & addon clustering during ingest

Each item and addon name is resolved through `OrderItemCluster.add(name, id, is_addon=..., itemcode=..., restaurant_id=...)`, which returns a 6-field `ClusterMatch`:

```
ClusterMatch(menu_item_id, order_item_id, variant_id, item_type, match_method, match_confidence)
```

- `match_method` is the **provenance** of the resolution: `existing-id-hit` (PetPooja id already mapped), `itemcode-hit` (unseen PetPooja itemid routed to the parent that owns its POS itemcode), `name-hit` (cleaned name+type matched), `fuzzy-suggested` (difflib suggested a verified item), `new` (brand-new item), `unmatched` (empty/blank name). It and `match_confidence` (0–100; fuzzy = difflib ratio × 100, exact and itemcode = 100) are written to `order_items.match_method` / `match_confidence` so you can audit how any row was mapped.
- Clustering hierarchy, itemcode routing, silent-reuse guard, and variant parsing are documented in [item_clustering.md](./item_clustering.md).

### Itemcode routing (regular items only)

`itemcode` / `restaurant_id` are **keyword-only** arguments; addon call sites don't pass them (PetPooja provides no addon itemcodes), so addons are byte-for-byte unchanged. For regular items the priority is:

1. **Existing itemid mapping wins** — an already-mapped itemid is never remapped by its itemcode. If the itemcode disagrees with the existing assignment, the projection marks that code `conflict`; the assignment is untouched.
2. **Active itemcode hit** — an *unseen* itemid whose itemcode has one active **and route-eligible** parent joins that parent (`itemcode-hit`, 100.0). The name is parsed only for its **variant**; the parent comes from the projection. C1 verification inheritance applies unchanged. Route-eligibility (`itemcode_mappings.route_eligible`) requires the parent's ownership to be **server-backed** (a `menu_item_variants` row carrying `assignment_seq`/`verification_seq`); a `first_seen` mapping learned only from local ingest is recorded but not yet routable, so it does not crown a fleet-wide parent before sync (plan Phase 9.2).
3. **Fallback** — conflicted/unknown/blank **or not-yet-route-eligible** itemcodes go through the existing parser/fuzzy path, and the parent the fallback chose is *taught* back to the projection.

All itemcode observations ride the order's transaction — a failed order rolls back mapping, projection, and children together (no helper commits; §2 holds). The projection table (`itemcode_mappings`) is a derived local artifact: rebuilt from order rows + assignments at sync/bootstrap/merge lifecycle points, never exported to Brain JSON, never synced to cloud. See `src/core/itemcode_mapping.py` module docstring for semantics.

### Synthetic addon ids collapse by normalized name

Exact-match keys on the PetPooja id (`itemid` / `addonid`). Addons often arrive **without a stable id**. When `order_item_id` is missing, `OrderItemCluster.add` synthesizes one by hashing the name — but it **normalizes punctuation/spacing first**:

```python
normalized_name = re.sub(r'[^a-z0-9]+', ' ', name.lower()).strip()
order_item_id = generate_deterministic_id(f"generated_{normalized_name}")
```

So `"Choco Chips"`, `"choco-chips"`, and `"Choco  Chips"` collapse to **one** synthetic id (one mapping row) instead of splitting into three. `generate_deterministic_id` already lowercases but does **not** collapse punctuation — hence the extra normalization here.

---

## 5. Customer identity & double-count avoidance

Orders attach to a `customers` row via `customer_identity_key` (phone → gstin → anonymous; see `compute_customer_identity_key`). New orders land on the **surviving** customer after any merges via `resolve_active_customer_target` (follows `customer_merge_history`).

Customer aggregates (`total_orders`, `total_spent`, first/last order date) are **denormalized**, so the same first-ingest-vs-replay split as menu items applies:

- **New order** → `get_or_create_customer` seeds/increments aggregates inline.
- **Re-ingest** → `_ensure_customer_no_stats` resolves/creates the customer **without touching aggregates and without committing**. After the order commits its children, aggregates are rebuilt from the `orders` table via `recompute_customer_aggregates` for **both** the previous owner and the current owner.

Why both owners: an edit can **move an order to a different customer** (the header upsert re-points `orders.customer_id`). The previous owner may have lost the order; the new owner gained it. Recomputing from `SUM(orders.total)` after the re-point corrects both.

> **Gotcha:** `_ensure_customer_no_stats` exists *specifically* so replay does not (a) double-count spend or (b) commit mid-order. Do not "simplify" it back into `get_or_create_customer`.

---

## 6. Addons — storage, view, and sync-time eligibility

- **Storage:** `order_item_addons` (one row per addon per order item; `menu_item_id`/`variant_id` map the addon to the catalog just like items).
- **Menu-item counter:** `menu_items.sold_as_addon` accumulates addon sales; the flag is **accumulate-only** (never decremented on the fast path).
- **`menu_item_variants.addon_eligible`:** marks a `(menu_item_id, variant_id)` pair as sellable-as-addon. It is **derived from observed usage**, not configured: after each sync, `utils/menu_item_variant_enforcement.mark_addon_eligible_from_usage` runs one batched `UPDATE` flipping `addon_eligible` `0 → 1` for every pair that appears in `order_item_addons`. The flag is **sticky** (only ever 0→1), mirroring `sold_as_addon`, so re-running every sync is idempotent and cheap. It does **not** commit — the caller (`sync_database`) owns the transaction.
- **Resolutions tab** surfaces addon usage stats (`addon_rows`, `addon_qty`) alongside item stats via `fetch_unverified_items` so an operator resolving a mapping sees how often it was sold as an addon.

---

## 7. Deferred verification flush

Mapping-verification retries used to commit **mid-order** inside `OrderItemCluster.add`, which violated §2. They were moved out: `process_order` calls `flush_deferred_menu_mapping_verifications(conn)` **after** the order's own commit, on its own transaction, inside a guarded `try/except`. A failure there is logged but must never fail the committed order.

---

## 8. Complexity checklist (before editing this pipeline)

1. **Never commit mid-order.** One commit at the end. New helpers that run during ingest must not commit.
2. **Watermark safety.** The header upsert advances `stream_id`; make sure nothing observable to the next sync's `MAX(stream_id)` is durable before the children are.
3. **Two stat paths.** Any denormalized counter change must touch both the inline-increment block and the recompute function (`_recalculate_menu_item_stats` for menu items, `recompute_customer_aggregates` for customers).
4. **Replay is wipe-and-rebuild.** Adding a new child table? It must be deleted-and-rebuilt on re-ingest, or replays will duplicate/strand rows.
5. **Capture pre-delete state.** If a stat depends on rows an edit may remove, capture the affected keys **before** the delete.
6. **Status-aware.** Inline increments only fire for `order_status = 'Success'`; the recompute functions are status-aware. Keep both consistent.
7. **Addon eligibility is derived, sticky, and uncommitted-by-callee.** Don't turn it into a per-row lookup or a 1→0 toggle.

---

## 9. Key files

| File | Role |
|------|------|
| `services/load_orders.py` | Order ingest core: `process_order`, header upsert, replay wipe/rebuild, customer resolution, stat dispatch. |
| `services/clustering_service.py` | `OrderItemCluster.add` — item/addon → `(menu_item_id, variant_id, …)` with provenance, incl. itemcode routing. |
| `src/core/itemcode_mapping.py` | Itemcode → parent projection: observe/lookup during ingest, full rebuild at lifecycle points. Local-only, derived. |
| `utils/clean_order_item.py` | Legacy regex name/variant parsing (fallback for unseen names). See [item_clustering.md](./item_clustering.md). |
| `utils/menu_utils.py` | `_recalculate_menu_item_stats` — idempotent menu-item aggregate recompute. |
| `utils/menu_item_variant_enforcement.py` | `mark_addon_eligible_from_usage`, variant-mapping backfills. |
| `src/core/queries/customer_merge_helpers.py` | `recompute_customer_aggregates` — idempotent customer aggregate recompute. |
| `src/core/services/sync_service.py` | `sync_database` — drives per-order `process_order`, runs addon-eligibility pass, and orchestrates cloud pulls. |
| `database/schema_sqlite.sql` | `orders`, `order_items`, `order_item_addons`, `order_taxes`, `order_discounts` DDL. |

See [FILE_INVENTORY.md](./FILE_INVENTORY.md) for the full map.
