# Pending Task: Dedupe and Reconcile Subclusters During Menu Item Merge

## Problem

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

## Goal

Make parent menu-item merge perform proper child-variant dedupe and reconciliation, not just parent reassignment.

## Todo

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
  - `menu_item_variants`
  - `order_items`
  - `order_item_addons`
  - `merge_history`
- [ ] Ensure merge history stores child-variant reconciliation details so undo remains correct
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
- [ ] Add regression tests to confirm revenue, quantity, and variant assignments remain correct after merge and undo
- [ ] Add a rollout safeguard:
  - feature flag or guarded UI path if needed
  - fallback to manual mapping when confidence is low

## Acceptance Criteria

- Merging `B -> A` no longer only reassigns the parent item
- Source child variants are reconciled against target child variants before commit
- Exact matches are deduped reliably
- Ambiguous matches are not auto-merged silently
- Undo restores both parent and child assignments correctly
- Merge preview explains the child-variant outcome before confirmation
