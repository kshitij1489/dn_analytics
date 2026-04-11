# Cluster Review Cleanup Backlog

Primary review files:
- `tmp/cluster_review/current_clusters.md`
- `tmp/cluster_review/current_clusters.csv`
- `tmp/cluster_review/current_cluster_members.csv`
- `tmp/cluster_review/merge_history.md`
- `tmp/cluster_review/merge_history.csv`

Goal:
- List the still-open cleanup work.
- Keep supporting review/data-quality follow-up separate from guardrails so resolved items do not get reopened by mistake.

## Supporting review/data-quality follow-up

### Recover raw-name context for generated source IDs
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

## Recommended next steps

1. Recover missing raw-name context for generated IDs as supporting review/data-quality follow-up.
2. Re-run `python3 scripts/export_cluster_review.py` after each meaningful cleanup pass.
3. Compare the new export against this backlog and close or add items based on fresh evidence.

## Open checklist

- [ ] Recover raw-name context for generated IDs as supporting review/data-quality follow-up
- [ ] Re-export and verify the remaining clusters
