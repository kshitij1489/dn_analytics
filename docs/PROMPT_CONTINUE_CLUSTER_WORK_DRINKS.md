# Prompt (paste in new chat): Drinks cluster review — same as Combo export

Use this in a **new chat** with the **analytics** repo open. Goal: do for **`Drinks`** exactly what we did for **`Combo`**.

---

## Goal

Add a **Drinks-only** cluster review export that mirrors `scripts/export_combo_cluster_review.py`:

1. Filter current cluster data to **`menu_items.type == "Drinks"`** (confirm the exact string in `analytics.db` with `SELECT DISTINCT type FROM menu_items` if needed).
2. Output a **Markdown** file with the same structure as the combo export:
   - Numbered **parents** `1) Name, Drinks`
   - Lettered **variants** `a) VARIANT_NAME` under each parent
   - Numbered **raw / mapping** bullets `1)` under each variant
   - For each mapping: **scoped `order_items` and `order_item_addons` row listing** (one numbered line per DB row: ids, qty, prices, `name_raw`, etc.) — not only counts.
3. **`hierarchy_ref`** column like `1-a-1` matching parent / variant / raw index.
4. **ID index** table at the bottom: `hierarchy_ref`, `menu_item_id`, `variant_id`, `source_item_id`, verification, row counts, **`order_item_ids` / `order_item_addon_ids`** (truncate long lists in the table with “see list above”, full list in the body).
5. **Merge history** section: rows relevant to the Drinks `menu_item_id`s in this export (same idea as combo script).
6. Optional **CSVs** parallel to combo: summary, members, merge slice — same column shapes as combo export where it makes sense.

## Implementation hints

- Reuse loaders and cluster logic from **`scripts/export_cluster_review.py`** (import as sibling module like the combo script does with `export_cluster_review`).
- Either **`--type Drinks`** on a generalized script or a dedicated **`scripts/export_drink_cluster_review.py`** — keep the diff small and consistent with `export_combo_cluster_review.py`.
- Default output dir e.g. **`menu_cleansing_exercise/drinks_cluster_review`** (see `MENU_STATE_EVALUATION_ROOT` in `scripts/export_cluster_review.py`).
- Do **not** change DB data in this task unless I ask separately; this pass is **export + review artifacts only**.

## Reference file

Use **`scripts/export_combo_cluster_review.py`** as the template to copy behavior and Markdown layout from (replace `COMBO_TYPE` / titles / default paths with Drinks).

---

_End of prompt — paste from “## Goal” through here, or paste the whole file._
