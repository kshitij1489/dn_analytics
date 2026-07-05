# Completed review artifacts (`menu_cleansing_exercise/done`)

## Combo cluster review — **closed** (2026-04-15)

**Combo-related data cleansing is complete.** Menu merges were applied and checked in `analytics.db` (including merges into **Design Your Indulgence Duo Ice Creams** for the former **Classic Night & Day Duo** and **Curious Creations Duo** rows, with `menu_item_variants` verified on the canonical item). **There is no need to revisit** this combo track unless new catalog or order evidence appears, or you deliberately undo a merge in the app.

### What is in this folder

| Path | Note |
|------|------|
| `combo_cluster_review/` | Frozen export snapshot (CSV + Markdown). Historical reference only. |
| `analytics.before_cluster_fix_1_2.db` | SQLite snapshot before merges **61–62** (see section below). |
| `analytics.before_banoffee_sample_fix.db` | SQLite snapshot before merge **63** (Banoffee row). |
| `drinks_cluster_review/` | Drinks-only cluster export (`--type Drinks`). Moved from earlier `tmp/` layouts; see **Drinks** section below. |
| `service_cluster_review/` | Service-only cluster export (`--type Service`). Moved from earlier `tmp/` layouts; see **Service** section below. |
| `dessert_cluster_review/` | Dessert-type cluster export (`--type Dessert`; DB value is singular). See **Dessert** section below. |
| `extra_cluster_review/` | Extra-only cluster export (`--type Extra`). Moved from earlier `tmp/` layouts; see **Extra** section below. |

### If you need combo clusters again later

Run a **new** export from the **current** database (for example `python3 scripts/export_combo_cluster_review.py`). Do **not** treat these archived files as live truth—they predate or parallel final cleanup and are kept for audit trail only.

---

## Pre-fix SQLite snapshots (moved 2026-04-15)

These files are **read-only backups** taken before specific fixes. They were compared to root `analytics.db` using **`merge_history` only** (no scripts re-run, no writes).

| File | Last `merge_id` in backup | Evidence the live DB already contains the fix |
|------|---------------------------|---------------------------------------------------|
| `analytics.before_cluster_fix_1_2.db` | **60** (ends after `Butter Waffle Cone`) | Live DB has **`merge_id` 61–62** (`Eggless Coffee Mascarpone Ice Cream` → `Coffee Mascarpone Ice Cream`, `Alphonso Mango Ice Cream` self-merge row), same timestamps **2026-04-11 10:23:02**. |
| `analytics.before_banoffee_sample_fix.db` | **62** | Live DB has **`merge_id` 63** — `Banoffee Ice Cream` merge at **2026-04-11 10:28:38** — absent from this backup. |

**Conclusion:** Both snapshots are **superseded**; the corresponding work is already reflected in `analytics.db`. Keep these files only for **rollback forensics** if you ever need to diff schema or row-level history manually—**do not** swap them in as `analytics.db` unless you intend to rewind.

---

## Drinks cluster review — **archived** (historical snapshot)

The folder **`drinks_cluster_review/`** is a **frozen** export (Markdown + CSVs). It is **not** the live source of truth. Regenerate anytime:

`python3 scripts/export_combo_cluster_review.py --type Drinks`

(Default output path is `menu_cleansing_exercise/drinks_cluster_review/` unless you pass `--output-dir`.)

---

## Service cluster review — **archived** (historical snapshot)

The folder **`service_cluster_review/`** is a **frozen** export (Markdown + CSVs). **Typed export:** `python3 scripts/export_combo_cluster_review.py --type Service` (same layout as Combo/Drinks).

**Snapshot summary (export time):** **2** parent `menu_item_id` clusters — *Ice Cream Factory Visit & Trials* (variants **FAMILY** / **SINGLE**) and *School Kids Factory Visit* (**1_PIECE**). All three `menu_item_variants` mappings verified; **no** matching rows in merge history for these clusters at export time.

Regenerate after Service merges or mapping edits:

`python3 scripts/export_combo_cluster_review.py --type Service`

(Default output path is `menu_cleansing_exercise/service_cluster_review/` unless you pass `--output-dir`.) Log issues in `docs/CLUSTER_MAPPING_REVIEW_TRACKER.md` with **Area** = `Service` and refs like `1-a-1` from `service_clusters.md`.

---

## Extra cluster review — **archived** (historical snapshot)

The folder **`extra_cluster_review/`** is a **frozen** export (Markdown + CSVs). **Typed export:** `python3 scripts/export_combo_cluster_review.py --type Extra`. Snapshot generated **2026-04-15 11:15:15 UTC** (see `extra_clusters.md` for full counts, merge-history slice, and parent names).

Regenerate after Extra merges or mapping edits:

`python3 scripts/export_combo_cluster_review.py --type Extra`

(Default output path is `menu_cleansing_exercise/extra_cluster_review/` unless you pass `--output-dir`.) Log issues in `docs/CLUSTER_MAPPING_REVIEW_TRACKER.md` with **Area** = `Extra` and hierarchy refs from `extra_clusters.md`.

---

## Dessert cluster review — **baseline** (2026-04-15)

The active **`dessert_cluster_review/`** snapshot for this exercise lives **only** under **`menu_cleansing_exercise/done/`** (the former `menu_cleansing_exercise/dessert_cluster_review/` folder was moved here).

**Typed export:** `python3 scripts/export_combo_cluster_review.py --type Dessert` — note **`Dessert`** singular matches `menu_items.type` in this database (there is no `Desserts` type).

### Current DB snapshot (this folder)

| Path | Note |
|------|------|
| `dessert_cluster_review/` | Frozen export: **20** `menu_items` rows have `type = Dessert`, but **19** distinct `menu_item_id` values appear in the cluster summary (the dessert **Request** has **no** `menu_item_variants` rows, so it does not show up in this export). **24** parent×variant summary rows (several desserts expose **1_PIECE** and **2_PIECES**, plus addon-backed variants such as *Cakes & Cookies* **MINI_TUB_200ML**). **30** member mappings in `dessert_cluster_members.csv`; all **verified** in this snapshot. **Merge history** section: **no** events matched these dessert `menu_item_id` values at export time. |

Canonical parent names represented in the export included: *Affogato*, *Assorted Cookie Duo ( Dark Chocolate + Choco Chip )*, *Boston Cream Pie*, *Brownie Cheesecake*, *Cakes & Cookies*, *Choco Chunk Cookie*, *Classic Chocolate Lamington*, *Classic Tiramisu*, *Coffee Banana Cheesecake*, *Cream Cheese Fruit Medley Cake*, *Custom Diwali Cookie Set*, *Dark Double Chocolate Cookie*, *Deconstructed Coffee Tres Leches*, *Employee Dessert ( Any 1 )*, *Fudgy Chocolate Brownie*, *New York Baked Cheesecake Eggless*, *Nona’s Traditional Plum Cake Eggless Contains Alcohol*, *Orange & Chocolate Cheesecake*, *Tres Leches*. *(Catalog-only, not in export: **Request**.)*

**Re-run** after Dessert merges or mapping edits, for example with `--output-dir menu_cleansing_exercise/done/dessert_cluster_review`; treat this copy as **historical reference** only. Log issues in `docs/CLUSTER_MAPPING_REVIEW_TRACKER.md` with **Area** = `Dessert` and refs from `dessert_clusters.md`.
