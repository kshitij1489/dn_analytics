# Cluster mapping review tracker

**Purpose:** Record `menu_item_variants` (and related) mappings that look wrong, noisy, or inconsistent so we can fix or remove them in a controlled way and keep the dataset clean.

**Scope:** Any cluster mapping issue—combos, other menu types, variant splits, duplicate sources, bad raw-name groupings, etc.

**How to use:** Add one row per issue in the table below (or a new subsection if you prefer long notes). When a fix is implemented in the DB or code, update **Status** and **Resolution / PR**.

---

## Issues log

| ID | Added | Area | Problem (short) | Details | Suggested fix | Status |
|----|-------|------|-----------------|---------|---------------|--------|
| — | — | — | *(none yet)* | | | Open |

**Column hints**

- **ID:** Local tracker id (`CM-1`, `CM-2`, …) or leave blank until assigned.
- **Area:** e.g. `Combo`, `Drinks`, `Service`, `Extra`, `Dessert`, `Ice Cream`, merge history, PetPooja id `…`, etc.
- **Problem:** One line; use **Details** for menu_item_id, variant_id, source ids, screenshots, export refs (`1-a-1` from combo export, etc.).
- **Status:** `Open` | `In progress` | `Fixed` | `Won’t fix` | `Spurious`.

---

## Resolved / parked

_Move completed rows here or keep in the main table with Status = Fixed._

| ID | Resolved | Summary | Resolution / PR |
|----|----------|---------|-----------------|
| — | — | — | — |

---

## Notes

- Cross-check cluster exports: `scripts/export_cluster_review.py`, `scripts/export_combo_cluster_review.py` (defaults under `menu_cleansing_exercise/` for menu state evaluation).
- **Drinks-only export (same layout as Combo):** `python3 scripts/export_combo_cluster_review.py --type Drinks` → default directory `menu_cleansing_exercise/drinks_cluster_review/` (`drinks_clusters.md`, `drinks_cluster_summary.csv`, `drinks_cluster_members.csv`, `drinks_merge_history.csv`). Use **Area** = `Drinks` and hierarchy refs from that Markdown when logging issues.
- **Service-only export:** `python3 scripts/export_combo_cluster_review.py --type Service` → `menu_cleansing_exercise/service_cluster_review/` (`service_clusters.md`, `service_cluster_summary.csv`, `service_cluster_members.csv`, `service_merge_history.csv`). Use **Area** = `Service` and hierarchy refs (e.g. `1-a-1`, `2-a-1`) when logging issues.
- **Extra-only export:** `python3 scripts/export_combo_cluster_review.py --type Extra` → `menu_cleansing_exercise/extra_cluster_review/` (`extra_clusters.md`, `extra_cluster_summary.csv`, `extra_cluster_members.csv`, `extra_merge_history.csv`). Use **Area** = `Extra` and hierarchy refs from `extra_clusters.md` when logging issues (includes packaging, cones, cup add-ons, delivery charges, fudge SKUs; merge history may show prior waffle-cone merges).
- **Dessert catalog export:** In `analytics.db`, the type string is **`Dessert`** (singular), not `Desserts`. Run `python3 scripts/export_combo_cluster_review.py --type Dessert` → `menu_cleansing_exercise/dessert_cluster_review/` (`dessert_clusters.md`, `dessert_cluster_summary.csv`, `dessert_cluster_members.csv`, `dessert_merge_history.csv`). Use **Area** = `Dessert` (match DB) and hierarchy refs from `dessert_clusters.md` when logging issues. One dessert menu row (**Request**) currently has no `menu_item_variants`, so it will not appear in the export until variants/mappings exist.
- Other categories: same script with `--type <exact menu_items.type>` (case-sensitive; confirm with `SELECT DISTINCT type FROM menu_items ORDER BY 1` on `analytics.db`).
- After DB changes, re-run exports and note the run date in the issue row if useful.
