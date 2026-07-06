# Pending Tasks

Open / incomplete work. Menu sync architecture is documented in [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md); items below marked *(sync)* reference it.

- [Task 1 — Dedupe & reconcile subclusters during menu-item merge](#task-1--dedupe-and-reconcile-subclusters-during-menu-item-merge)
- [Task 2 — Menu sync follow-ups (post sign-off)](#task-2--menu-sync-follow-ups-post-sign-off)

---

## Task 1 — Dedupe and Reconcile Subclusters During Menu Item Merge

### Problem

When a menu item merge is executed from the Menu Items page, the current merge path reassigns the source parent item to the target parent item, but it does not reconcile the source child variants/subclusters against the target child variants/subclusters.

Example:

- Parent `A` has child variants `C`, `D`
- Parent `B` has child variants `E`, `F`, `G`
- Merge `B -> A`

Current result:

- `A` absorbs `B`'s rows
- `E`, `F`, `G` remain as separate child variants under the merged parent unless they already share the exact same global `variant_id`

Expected result:

- After `B -> A`, the merged parent should contain the full child set
- Any source child variants that are semantically the same as existing target child variants should be deduped and merged
- Only truly distinct child variants should remain separate

### Goal

Make parent menu-item merge perform proper child-variant dedupe and reconciliation, not just parent reassignment.

### Todo

- [ ] Identify all merge entry points and document which ones use:
  - plain parent merge
  - explicit variant-mapping merge
- [ ] Define the canonical expected behavior for parent merge:
  - source child variants must be evaluated against target child variants
  - matching child variants must be merged into the correct target child variants
  - non-matching child variants must be preserved as distinct child variants
- [ ] Decide whether Menu Items page merge should:
  - require explicit child mapping before merge, or
  - support auto-suggestions with user confirmation, or
  - support a safe fully automatic merge only for exact matches
- [ ] Implement a reconciliation layer for child variants during merge:
  - inspect source child variants
  - inspect target child variants
  - build a reconciliation plan before applying DB updates
- [ ] Define matching rules for child-variant dedupe:
  - exact `variant_id` match
  - exact normalized `variant_name` match
  - optional alias/synonym match
  - optional fuzzy match with a conservative threshold
- [ ] Prevent incorrect automatic merges:
  - do not merge variants that are only loosely similar
  - require manual confirmation for ambiguous cases
  - log why a proposed child merge was chosen
- [ ] Update merge preview API to return reconciliation details:
  - source child variants
  - target child variants
  - exact matches
  - suggested matches
  - ambiguous unmatched variants
  - new child variants that will be created or retained
- [ ] Update Menu Items page UI to show the reconciliation plan before confirmation:
  - what will merge into existing child variants
  - what will remain separate
  - what requires manual selection
- [ ] Reuse the variant-mapping merge path where possible instead of maintaining two inconsistent merge behaviors
- [ ] Ensure all affected tables are updated consistently during reconciliation:
  - `menu_item_variants` — **also stamp `pending_local = 1, assignment_seq = NULL`** on every row the reconciliation rewrites (the sync echo/ack path relies on this; see `utils/menu_utils.py` merge paths and [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §2.4)
  - `order_items`
  - `order_item_addons`
  - `merge_history` — carries the new `origin` column (`'remote'` for sync-applied rows; local reconciliation leaves it NULL)
- [ ] Ensure merge history stores child-variant reconciliation details so undo remains correct
- [ ] **Integrate with the assignment-based sync layer** (landed after this doc was written — see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md)): any child-variant reassignment/dedupe is a per-`order_item_id` assignment change, so the emitted merge event **must** include those `order_item_id`s in its `assignments` array (schema v2), or peer installs will not converge the reconciled children
- [ ] **Preserve the single-owner `is_verified` rule** (I5): if reconciliation changes a child mapping's verified flag, emit a mapping-verification event (`record_menu_mapping_verification_events_chunked`) — the merge stream only seeds `is_verified` on INSERT and must not rewrite it on an existing row
- [ ] **Undo carries prior assignments**: reconciled-merge undo must emit the prior per-`order_item_id` assignments in the undo event (undo is just another LWW write), not rely on local `merge_history` replay alone
- [ ] Verify undo works for:
  - plain child reparenting
  - child dedupe into existing target variants
  - child creation / retention as distinct variants
- [ ] Add tests for the core scenarios:
  - `B -> A` where all child variants are distinct
  - `B -> A` where some source children match target children exactly
  - `B -> A` where names match but IDs differ
  - `B -> A` where matches are ambiguous
  - undo after reconciled merge
  - two installs: reconciled merge on install 1 → push/pull → install 2 converges the same child variants (assignment LWW)
- [ ] Add regression tests to confirm revenue, quantity, and variant assignments remain correct after merge and undo
- [ ] Add a rollout safeguard:
  - feature flag or guarded UI path if needed
  - fallback to manual mapping when confidence is low

### Acceptance Criteria

- Merging `B -> A` no longer only reassigns the parent item
- Source child variants are reconciled against target child variants before commit
- Exact matches are deduped reliably
- Ambiguous matches are not auto-merged silently
- Undo restores both parent and child assignments correctly
- Merge preview explains the child-variant outcome before confirmation
- The reconciled merge (and its undo) emits assignment + verification events so other installs converge to the same child-variant state

---

## Task 2 — Menu sync follow-ups (post sign-off)

The menu clustering sync work shipped and was signed off 2026-07-06 (architecture in [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md)). These items remain open. None block a second-install bring-up.

### 2a. One-time human remap of legacy-key flavor families *(sync)*

Three flavor families — **Strawberry Cream Cheese, Coffee Mascarpone, Alphonso Mango** (~51 of 32 074 order-line groups) — place on a different menu item on a fresh install than on the golden install. Their April-2026 clustering decisions are recorded under mapping keys the current ingest no longer derives, so no sync mechanism can transfer them. **Fix:** re-do those merges/remaps once through the current UI (which emits modern-keyed v2 events); both installs then converge, historical lines included on the install where the remap is done. See [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §10.

- [ ] Redo the 3 flavor-family merges/remaps in the UI on the golden install
- [ ] Confirm the emitted v2 events carry the affected `order_item_id`s in `assignments`
- [ ] Re-run the fresh-install convergence check; expect 0 divergent line-groups

### 2b. Golden-install duplicate `order_items` *(orders pipeline)*

The golden DB carries ~705 duplicate order-line rows (same order re-processed historically; `process_order` re-INSERTs items on re-delivery). A fresh install does not reproduce them — its counts are the correct ones. This is an orders-pipeline dedupe, separate from menu sync.

- [ ] Add a re-delivery guard / upsert key to `process_order` in `services/load_orders.py`
- [ ] One-time cleanup of the existing duplicates on the golden DB (with backup)

### 2c. Convergence digest (C6 / S3) — deferred *(sync)*

The standing "are all installs in sync?" audit: client computes `sha256` over ordered `(order_item_id, menu_item_id, variant_id, is_verified)` rows where `pending_local = 0`, reports with cursor seq; server compares against `OrderItemAssignment`. **Deferred** until the fleet has ≥2 installs (pointless with one). When built, the digest must compare on the **intersection** of keys — the server legitimately holds historical rows no install has orders for (§10 27-row case), and installs hold clustering-created rows no event touched.

- [ ] Client nightly digest (piggyback `client_learning_shipper.run_all`)
- [ ] Server compare + `SyncConvergenceReport` admin view

### 2d. Sync-conflicts UI badge *(sync)*

`GET /api/menu/sync-conflicts` (quarantine + supersede notices) is API-only today. Add the Menu/Resolutions tab badge + list with item names from event snapshots, and wire the dismiss / acknowledge endpoints.

### 2e. Customer-merge divergence *(sync — customer stream)*

Customer merges have no assignment applier and no quarantine parity with the menu streams (see [MENU_SYNC_ARCHITECTURE.md](./MENU_SYNC_ARCHITECTURE.md) §9). Open remediation:

- [ ] **Order-anchor tie-break** — use order anchors from `moved_orders.portable_refs` on a name-only collision
- [ ] **Enrich portable locators on emit** — include phone/address hashes when a linked address row has them
- [ ] **Promote stable keys** — prefer non-anonymous `customer_identity_key` where business rules allow (policy)
- [ ] **Customer-stream quarantine** — mirror the menu per-event dead-letter + cursor advance (needs Dachnona backend work)

### 2f. Writable seed path bug *(latent)*

`export_to_backups` uses `get_resource_path("data")` → under PyInstaller this is `_internal/data` inside the `.app` and can **mutate the bundled seed** after install. Move writable exports to `get_data_path("data")` next to `analytics.db` so reset + seed always reads pristine packaged JSON. Ref: `src/core/utils/path_helper.py`.

### 2g. Merge branch to main *(release)*

Client branch `sync-conflict-phase-2` (assignment applier, fresh-install fast path, background pull, I5 single-owner `is_verified`, same-item no-op fix) is committed but **not merged to `main`**; the server half is already on live prod. Merge when ready.
