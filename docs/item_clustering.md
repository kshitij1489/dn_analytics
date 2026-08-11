# Item Clustering Logic

This document explains how the system takes messy, free-text order items (e.g., "Eggless Choco Brwn") and turns them into clean, structured Menu Items (e.g., "Chocolate Brownie" | Type: "Dessert" | Variant: "Eggless").

## The Problem
Order data comes from webhooks with inconsistent names. A single product might appear as:
- "Dbl Choco Chip Cookie"
- "Double Chocolate Chip Cookie (1pc)"
- "Cookie - Double Choco"

We need to map all these to a single **Menu Item ID** to track total sales accurately.

## The Solution: "Cluster & Verify"

The system uses a 2-step process to handle incoming data.

### 1. The "Brain" (Persistent Knowledge)
The core of the system is the **Menu Item Registry** (stored in the database and backed up to JSON).
- **UUIDs**: Every unique Menu Item gets a deterministic UUID based on its Name + Type. 
- **Variants**: Sizes and flavors (e.g., "Small", "Eggless") are extracted and stored as `variants`.

### 2. Nested Clustering Hierarchy
The clustering is designed as a **Parent-Child relationship**:

*   **Parent Cluster (The Item)**: Represents the core products (e.g., "Cappuccino", "Brownie").
    *   This is the high-level bucket for analytics.
    *   Mapped via `menu_item_id`.

*   **Child Cluster (The Variant)**: Represents specific attributes nested under the parent.
    *   Examples: "Regular", "Large", "Eggless", "with Ice Cream".
    *   Mapped via `variant_id`.

**Example:**
> **Parent**: "Chocolate Brownie"
> *   *Child*: "Eggless"
> *   *Child*: "With Ice Cream"
> *   *Child*: "Box of 4"

This allows us to track that we sold **50 Brownies** total (Parent level), while identifying that **30 were Eggless** (Child level).

### 3. The Clustering Algorithm
When a new order arrives, the `CleaningService` performs the following steps:

1.  **Exact Match (itemid)**:
    - Does this PetPooja `itemid` already exist in our mappings?
    - If **YES** -> Use the existing Menu Item ID. An itemcode never overrides an existing itemid mapping (if they disagree, the itemcode is only marked conflicted in the projection).

2.  **Itemcode Routing (regular items only)**:
    - If the itemid is unseen but its POS `itemcode` has one **active _and_ route-eligible** parent in `itemcode_mappings`, the new variant joins that parent directly (`match_method = itemcode-hit`, confidence 100).
    - The name is parsed only to extract the **variant**; the parent comes from the projection. Verification inheritance behaves as for other paths.
    - Conflicted, unknown, blank, **or not-yet-route-eligible** itemcodes skip this step and fall through to the parser (which then teaches the projection). Addons never enter it (PetPooja provides no addon itemcodes). See §6 below.

3.  **String Normalization**:
    - Otherwise, the system "cleans" the string (removes generic terms like "1pc", "Box", special chars).
    - It generates a tentative `clean_name`.

4.  **Fuzzy Prediction**:
    - The system looks for existing verified items that look similar to this new `clean_name` (using `difflib` string similarity).
    - If a match is found (Confidence > 0.7), it marks the new item as a **"Suggestion"** — never an automatic parent assignment.

5.  **Auto-Creation (Unverified)**:
    - If no match is found, a NEW Menu Item is created.
    - **Status**: `is_verified = FALSE`
    - **Action Required**: These show up in the "Menu" dashboard under "Unclustered Items" for you to review.

Whenever steps 3–5 pick a parent for an item that *does* carry an itemcode, that observation is taught back into the projection so the next unseen variant of the same product can take the fast path.

### 4. Variant Parsing & the UNKNOWN Fallback

`utils/clean_order_item.extract_variant` splits a raw name into `(clean_name, variant)`. Known package/size branches (Family Tub, Perfect Plenty, Regular Tub, Mini Tub, …) map recognized size tokens to canonical variants. Two rules keep unrecognized sizes **honest** instead of silently corrupting analytics:

- **Unrecognized size inside a known branch** → `UNKNOWN_<N><UNIT>` (e.g. `UNKNOWN_300ML`) via `_unknown_measure_variant`, instead of inheriting that branch's hardcoded default weight. This surfaces the odd size for review rather than mis-weighting it.
- **Bare volume with no tub context** → an honest volume label (e.g. `200ML`), not `MINI_TUB_…`. The `200ml` match is **word-bounded** (`\b200\s*ml\b`) so `"1200ml"` is *not* mistaken for `200ml` — it falls through to the generic `UNKNOWN_<N><UNIT>` fallback.
- **Generic size fallback** → any leftover `<N>(ml|gm|gms|kg)` token becomes `UNKNOWN_<N><UNIT>` rather than collapsing to `1_PIECE` (which would undercount weight/volume forecasting).

Why it matters: volume forecasting sums `quantity × variant_value`. A size silently coerced to the wrong default (or to `1_PIECE`) corrupts that sum invisibly. A visible `UNKNOWN_*` variant is a review signal instead.

### 5. Addon id collapse

Addons frequently arrive with **no stable PetPooja id**. When `OrderItemCluster.add` must synthesize an id, it normalizes punctuation/spacing first (`re.sub(r'[^a-z0-9]+', ' ', name.lower())`) before hashing, so `"Choco Chips"`, `"choco-chips"`, `"Choco  Chips"` collapse to **one** mapping row instead of three. See [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md) §4.

### 6. The Itemcode Projection (`itemcode_mappings`)

The POS `itemcode` is a human-assigned Petpooja product code shared by all size/packaging variants of one product (e.g. `CHOCO70ICE` covers 29 tub/scoop variants), while each product+variant has its own `itemid`. `src/core/itemcode_mapping.py` maintains a per-restaurant projection of which analytics parent owns each code:

- **Derived, local-only.** Rebuilt from raw order rows joined to current assignments (`rebuild_itemcode_mappings`). Not exported to `data/*.json` Brain artifacts, not synced to cloud — peers derive identical projections from the same assignment snapshot + order history.
- **One code → at most one active parent** per restaurant; **many codes → one parent** is normal (business merges, e.g. Egg + Eggless products reported together).
- **Auto-route gate (`route_eligible`).** A code auto-routes an unseen itemid (step 2 above) only when its parent's ownership is **server-backed** — some `menu_item_variants` row for the parent carries `assignment_seq` or `verification_seq`. `rebuild_itemcode_mappings` sets `route_eligible=1` for such parents and `0` otherwise; `first_seen` observations always record `route_eligible=0`. So a brand-new code learned only from local ingest **records** the mapping but does not become a routing authority until a derived-assignment flush / human verify / pull stamps a server seq and the next rebuild promotes it. This stops one install crowning a fleet-wide parent for a new itemid before the server has seen it (plan Phase 9.2). A pure local-only install (no cloud sync) therefore never auto-routes by itemcode — it name-parses — which is intended.
- **Split code → `conflict`.** A code observed under two parents is never auto-resolved; it falls back to the parser until a human merges/remaps the parents in the desktop UI, after which the next rebuild returns it to `active`. Conflicts are visible in rebuild logs and `itemcode_mappings.conflict_menu_item_ids`.
- **Rebuild lifecycle.** The projection is rebuilt (best-effort, never fails the caller) at the end of Sync DB, after assignment bootstrap/force-reseed, after remote-assignment / accepted strict-mutation batches, and after local merge / variant merge / resolution / remap / undo in `utils/menu_utils.py`. Strict-mode dry-run/rollback leaks nothing — helpers never commit.
- **Ops:** `python3 scripts/rebuild_itemcode_mappings.py` (dry-run by default, `--apply` to write, `--db` for a copy) prints active/conflict counts and every conflict.

## User Workflow
1.  **Incoming Orders**: Automatically mapped or created as "Unverified".
2.  **Review**: You go to the **Menu Page**.
3.  **Merge/Verify**:
    - If the system guessed right (e.g., it suggested "Choco Brownie" for "Choco Brwn"), you click **Merge**.
    - If it's a brand new item, you click **Verify**.
4.  **Learning**: Once verified, that mapping is permanent. Future orders with that name will automatically map correctly.

## Silent-reuse guard (Suspect Mappings)

Exact match keys on the PetPooja `itemid` / `addonid`. That is correct only while an id keeps pointing at one product. PetPooja sometimes recycles an id onto a **different** product (observed: itemid `1283886195` served "Eggless Chocolate", then "Just Chocolate (Andra)"). Because the mapping was already verified, every later order silently booked as the old product and never re-entered the Resolutions queue.

A string-similarity threshold cannot catch this: "Just Chocolate" vs "Eggless Chocolate" (different product, high overlap) sits in the same, inverted, distance space as "Banoffee" vs "Eggless Banoffee" (same product, lower overlap). Only a human who knows the menu can decide.

So detection is an **identity change, not a threshold**:

- `utils/mapping_core.py` reduces a raw name to a normalized `"<type>|<flavor>"` core, stripping known-cosmetic tokens (eggless / "ice cream" / size+unit / html / navratri / parentheticals). Benign relabels collapse to the same core; a genuinely different product yields a new core.
- `src/core/mapping_anomalies.py` remembers the cores seen per id (`mapping_id_cores`) and, when a **new** core lands on an id that already carried a different one **and** the mapping is verified, records a row in `mapping_anomalies`. The mapping is never rewritten; only surfaced.
- The clustering hook (`services/clustering_service.OrderItemCluster.add`) seeds the baseline core on create and flags on hit. `scripts/backfill_mapping_anomalies.py` replays full history through the same function to surface pre-existing reuses (run once after deploy).
- Both tables are **local diagnostics** — not included in dev export payloads and not shipped to cloud sync.

Operator triages on the Menu → Resolutions tab under **Suspect Mappings**: *Dismiss* (benign relabel — the core is remembered so it will not re-fire) or *Remap* (real reuse — rides the existing remap sync path). Endpoints: `GET /menu/resolutions/suspect-mappings`, `POST /menu/resolutions/suspect-mappings/{id}/dismiss`.

## Testing and Evaluation

Use the **Menu** page in the app to review clustering: merge, verify, and inspect unverified mappings on the Resolutions tab. Merge history is available via the Menu API (`GET /api/menu/merge/history`).

For how these mappings are applied during order loading (atomic ingest, replay, stat recompute, addon eligibility), see [ORDER_INGEST_PIPELINE.md](./ORDER_INGEST_PIPELINE.md). For fleet convergence and sync verification, see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md).
