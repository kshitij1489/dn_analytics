# Cluster Review Cleanup Backlog

Primary review files:
- `tmp/cluster_review/current_clusters.md`
- `tmp/cluster_review/current_clusters.csv`
- `tmp/cluster_review/current_cluster_members.csv`
- `tmp/cluster_review/merge_history.md`
- `tmp/cluster_review/merge_history.csv`

Additional verification:
- Direct DB spot checks for the exact `order_items.order_item_id` examples cited in the `Alphonso Mango` section. The review export does not include those live row IDs directly.

Goal:
- List the still-open cleanup work.
- Keep active cluster fixes separate from supporting review/data-quality follow-up so resolved items do not get reopened by mistake.

## Active cluster fixes

### 1. Split `Coffee Mascarpone` and `Eggless Coffee Mascarpone Ice Cream`
Status: Fixed

Why this needs a fix:
- This is a real eggless/non-eggless collision.
- It distorts parent-level flavor analytics and any reporting that depends on the dietary attribute.

Evidence:
- Merge `40`: `Coffee Mascarpone Ice Cream -> Eggless Coffee Mascarpone Ice Cream`
- Current cluster `Eggless Coffee Mascarpone Ice Cream :: JUNIOR_SCOOP_60GMS` contains:
  - `Coffee Mascarpone (60gm)`
  - `Coffee Mascarpone Ice Cream (Junior Scoop (60gm))`
  - `Eggless Coffee Mascarpone Ice Cream (Junior Scoop (60gm))`

What to fix:
- Recreate `Coffee Mascarpone Ice Cream :: JUNIOR_SCOOP_60GMS` with:
  - `menu_item_id = 1b1a56f0-7e0a-5f7c-93b7-9543004db816`
  - `variant_id = e4d57a7d-d262-5fd8-98cb-62ae69804b8d`
- Move these non-eggless source mappings out of `Eggless Coffee Mascarpone Ice Cream :: JUNIOR_SCOOP_60GMS` (`menu_item_id = c9b75532-8b1c-5a96-bf9d-1bf64d857f5a`, same variant `e4d57a7d-d262-5fd8-98cb-62ae69804b8d`) back into the non-eggless cluster:
  - `1285048012`: `Coffee Mascarpone Ice Cream (Junior Scoop (60gm))` / `Coffee Mascarpone Ice Cream (Junior Scoop)` with 11 `order_items` rows
  - `54290615`: `Coffee Mascarpone (60gm)` / `Coffee Mascarpone Small Scoop` with 13 `order_item_addons` rows
  - `03993ebd-77e2-5d35-80f4-26bf37737865`: mapping-only source ID from merges `34` and `40`
  - `2a373e6d-15af-593c-b34b-0a999a41b01d`: mapping-only source ID from merges `13`, `34`, and `40`
- Keep these source mappings on the eggless cluster:
  - `1285048022`: `Eggless Coffee Mascarpone Ice Cream (Junior Scoop (60gm))` / `Eggless Coffee Mascarpone Ice Cream (Junior Scoop)` with 10 `order_items` rows
  - `54290378`: `Eggless Coffee Mascarpone Ice Cream Small Scoop` with 18 `order_item_addons` rows
  - `a352c745-890c-57a5-b5ee-450f633ec0b6`: mapping-only source ID from merge `15` (`Eggless Coffee Mascarpone (60gm)`)
- `41cf69a4-7008-5928-84c1-4caf7a8647df` is currently in the eggless junior-scoop cluster but has no raw-name evidence in the DB export; verify it manually before moving it.
- Verified after fix:
  - `Coffee Mascarpone Ice Cream :: JUNIOR_SCOOP_60GMS` now contains only the non-eggless source mappings.
  - `Eggless Coffee Mascarpone Ice Cream :: JUNIOR_SCOOP_60GMS` now contains only `1285048022`, `54290378`, `a352c745-890c-57a5-b5ee-450f633ec0b6`, and the unresolved `41cf69a4-7008-5928-84c1-4caf7a8647df`.

### 2. Fix `Alphonso Mango Ice Cream :: REGULAR_SCOOP_120GMS`
Status: Fixed

Why this needs a fix:
- A tub/pack format is sitting inside a scoop cluster.
- This overstates scoop demand and understates pack/tub demand.

Evidence:
- Current cluster `Alphonso Mango Ice Cream :: REGULAR_SCOOP_120GMS` contains:
  - `Alphonso Mango Ice Cream (Perfect Plenty (200gms))`
  - `Alphonso Mango Ice Cream (Regular)`

What to fix:
- Move `Alphonso Mango Ice Cream (Perfect Plenty (200gms))` into:
  - `menu_item_id = 332c5870-4847-510b-b78f-9e880ddca033` (`Alphonso Mango Ice Cream`)
  - `variant_id = 0a41ed3b-37e4-540b-bc5c-407631835802` (`PERFECT_PLENTY_200GMS`)
- Keep `Alphonso Mango Ice Cream (Regular)` in:
  - `menu_item_id = 332c5870-4847-510b-b78f-9e880ddca033`
  - `variant_id = b747b32a-ee01-59b9-b443-75581bb57863` (`REGULAR_SCOOP_120GMS`)
- Exact live row split from the DB:
  - `order_items.order_item_id = 2033` with raw name `Alphonso Mango Ice Cream (Perfect Plenty (200gms))` should move to `PERFECT_PLENTY_200GMS`
  - `order_items.order_item_id = 948` with raw name `Alphonso Mango Ice Cream (Regular)` should stay in `REGULAR_SCOOP_120GMS`
- The current cluster has a single `source_item_id = 1282581589` (backed by `menu_item_variants.order_item_id = 1282581589`) shared by both raw names, so this does not merge into an existing non-eggless child cluster today.
- Instead, create the missing child cluster `Alphonso Mango Ice Cream :: PERFECT_PLENTY_200GMS` under the same parent and split the shared source mapping so the pack row and scoop row no longer point to the same variant.
- Re-run the export and confirm:
  - `Alphonso Mango Ice Cream :: REGULAR_SCOOP_120GMS` only contains scoop rows
  - `Alphonso Mango Ice Cream :: PERFECT_PLENTY_200GMS` contains the `Perfect Plenty (200gms)` row
- Verified after fix:
  - `Alphonso Mango Ice Cream :: REGULAR_SCOOP_120GMS` now contains only `Alphonso Mango Ice Cream (Regular)`.
  - `Alphonso Mango Ice Cream :: PERFECT_PLENTY_200GMS` now contains `Alphonso Mango Ice Cream (Perfect Plenty (200gms))`.

## Active cluster fixes continued

### 3. Fix `Banoffee Sample` sample mapping
Status: Fixed

Why this needs a fix:
- Samples should not be remapped into sellable scoop variants.
- This can inflate normal product demand.

Evidence:
- Historical merge `2`: `Banoffee Sample -> Eggless Banoffee Ice Cream`
- Current final target for that merge is `Banoffee Ice Cream`
- Current cluster `Banoffee Ice Cream :: JUNIOR_SCOOP_60GMS` still contains generated source ID `7449dbb6-75ef-5e31-a0ab-a84c75401451` from that merge
- Required variant assignment: `1_PIECE -> 1_PIECE`

What to fix:
- Move `source_item_id = 7449dbb6-75ef-5e31-a0ab-a84c75401451` off `Banoffee Ice Cream :: JUNIOR_SCOOP_60GMS` and onto `Banoffee Ice Cream :: 1_PIECE`.
- Ensure `Banoffee Sample` remains mapped as `1_PIECE -> 1_PIECE`.
- Keep it out of junior scoop analytics.
- Verified after fix:
  - `Banoffee Ice Cream :: 1_PIECE` now contains `2dada6d8-2592-57a0-8543-564b6bbe294e` and `7449dbb6-75ef-5e31-a0ab-a84c75401451`.
  - `Banoffee Ice Cream :: JUNIOR_SCOOP_60GMS` now contains only `1285047341`, `54290380`, and `f7fb56db-3163-5a15-9911-53d5732f081a`.

## Supporting review/data-quality follow-up

### 4. Recover raw-name context for generated source IDs
Status: Confirmed

Why this needs a fix:
- This is not a cluster split/merge by itself, but it blocks confident audit work on several clusters.
- Missing source identity makes merge audits unreliable.
- It increases the chance of both false fixes and missed issues.

Current known scope:
- 74 current mapping rows have `unknown` or blank source-kind/raw-name context.
- 28 merge-history rows have the same issue.

Examples:
- `Banoffee Ice Cream :: 1_PIECE`
- `Banoffee Ice Cream :: MINI_TUB_160GMS`
- `Bean-to-Bar 70% Dark Chocolate Ice Cream :: MINI_TUB_160GMS`
- `Coffee Mascarpone Ice Cream :: MINI_TUB_160GMS`

What to fix:
- Trace where generated IDs are being used instead of real `petpooja_itemid` / `petpooja_addonid`.
- Backfill raw-name context where possible.
- Prevent future exports from dropping original source identity.

## Guardrails

Use these rules while cleaning up so resolved items do not get reopened.

### Eggless naming rule
- Do not treat a missing `Eggless` token by itself as proof of a non-eggless family.
- These merges are considered correct and should not be reopened unless new evidence appears:
  - Merge `41`: `Dates & Chocolate -> Dates & Chocolate Eggless`
  - Merge `42`: `Dates With Fig & Orange -> Dates With Fig & Orange Eggless`
  - Merge `39`: `Coconut & Pineapple -> Eggless Coconut & Pineapple Ice Cream`
  - Merge `38`: `Chocolate Overload -> Eggless Chocolate Overload Ice Cream`
  - Merge `37`: `Cherry Chocolate Sample -> Eggless Cherry & Chocolate Ice Cream`
  - Merge `31`: `Strawberry Sample -> Eggless Strawberry Cream Cheese Ice Cream`

### Sample handling rule
- These sample merges are considered correct and should not be treated as discrepancies:
  - Merge `27`: `Chocolate Overload Sample -> Eggless Chocolate Overload Ice Cream`
  - Merge `31`: `Strawberry Sample -> Eggless Strawberry Cream Cheese Ice Cream`
  - Merge `37`: `Cherry Chocolate Sample -> Eggless Cherry & Chocolate Ice Cream`
  - Merge `28`: `Fig & Orange Sample -> Fig & Orange Ice Cream`

### Variant semantics rule
- For ice cream, names that include `(60gm)` are safe to map to `JUNIOR_SCOOP_60GMS`.
- Use `1_PIECE` for ice cream only when the raw name includes `Sample`.
- These historical merges are considered correct and should not be reopened on variant semantics alone:
  - Merge `35`: `Banoffee (60gm) -> Eggless Banoffee Ice Cream`
  - Merge `8`: `Strawberry Cream Cheese (60gm) -> Strawberry Cream Cheese Ice Cream`
  - Merge `5`: `Cakes & Cookies (60gm) -> Cakes & Cookies Ice Cream`
  - Merge `3`: `Bean-to-Bar Dark Chocolate (60gm) -> Bean-to-Bar Dark Chocolate Ice Cream`

### Historical merge interpretation rule
- Always use both `target_name_at_event` and `current_target_name` when auditing or reversing merges.
- Example:
  - Merge `33`
  - Event target: `Coffee Mascarpone Ice Cream`
  - Current target: `Eggless Coffee Mascarpone Ice Cream`

## Recommended cleanup order

1. Fix the confirmed `Coffee Mascarpone` eggless/non-eggless collision.
2. Fix the `Alphonso Mango` variant mismatch.
3. Fix the `Banoffee Sample` mapping.
4. Recover missing raw-name context for generated IDs as supporting review/data-quality follow-up.
5. Re-run `python3 scripts/export_cluster_review.py`.
6. Compare the new export against this backlog and close resolved items.

## Checklist

- [x] Split `Coffee Mascarpone` and `Eggless Coffee Mascarpone Ice Cream`
- [x] Correct the `Alphonso Mango` `Perfect Plenty (200gms)` variant mapping
- [x] Fix `Banoffee Sample` to stay `1_PIECE -> 1_PIECE`
- [ ] Recover raw-name context for generated IDs as supporting review/data-quality follow-up
- [ ] Re-export and verify the remaining clusters
