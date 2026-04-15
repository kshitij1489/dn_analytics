# Menu cleansing: cluster review and catalog fixes

This folder holds **exported snapshots** (Markdown + CSV) used to review `menu_item_variants` mappings and related order lines. Use this document to **repeat the evaluation loop** and apply fixes safely.

Run all commands from the **repository root** unless noted otherwise.

---

## 1. What the exports are (and are not)

- **Source of truth** is **`analytics.db`** (`menu_items`, `variants`, `menu_item_variants`, `order_items`, `order_item_addons`, `merge_history`).
- **Typed exports** (`scripts/export_combo_cluster_review.py`) read the live DB and filter by **`menu_items.type`** on the canonical parent (shown as `parent_cluster_type` in CSVs). They do **not** guess category from product names.
- Markdown in each review describes **parent → variant → raw mapping → scoped order rows**, plus an **ID index** and a **merge history** section filtered to clusters in that export.

If a parent appears under the wrong type in an export, the first question is almost always: **what is `menu_items.type` for that `menu_item_id`?**

---

## 2. Confirm the exact `type` string in SQLite

Types are **case-sensitive** and must match the DB exactly (e.g. `Dessert` not `Desserts`, `Ice Cream` with a space).

```bash
sqlite3 analytics.db "SELECT DISTINCT type FROM menu_items ORDER BY 1;"
```

For one item:

```bash
sqlite3 analytics.db "SELECT menu_item_id, name, type FROM menu_items WHERE name LIKE '%Your Name%';"
```

---

## 3. Regenerate typed cluster reviews

Script: **`scripts/export_combo_cluster_review.py`** (reuses logic from `scripts/export_cluster_review.py`).

```bash
python3 scripts/export_combo_cluster_review.py --type "<ExactType>" --output-dir menu_cleansing_exercise/<slug>_cluster_review
```

Examples (quote types that contain spaces):

| Category   | Example `--type` | Default output folder (if omitted)   |
|-----------|-------------------|--------------------------------------|
| Ice cream | `"Ice Cream"`     | `tmp/ice_cream_cluster_review/`      |
| Dessert   | `Dessert`         | `tmp/dessert_cluster_review/`        |
| Combo     | `Combo`           | `tmp/combo_cluster_review/`          |
| Drinks    | `Drinks`          | `tmp/drinks_cluster_review/`         |
| Service   | `Service`         | `tmp/service_cluster_review/`        |
| Extra     | `Extra`           | `tmp/extra_cluster_review/`          |

**Artifacts** (stem = type lowercased, spaces → `_`, e.g. `ice_cream_*`):

- `{stem}_clusters.md` — main human review
- `{stem}_cluster_summary.csv`, `{stem}_cluster_members.csv`, `{stem}_merge_history.csv`

**Full-database** cluster export (all types in one place): `scripts/export_cluster_review.py`.

---

## 4. How to read the Markdown

- **Hierarchy:** `1)` parent name + type, `a)` variant, numbered raw rows; cross-ref **`1-a-1`** in the ID index table.
- **Per mapping:** `order_items` / `order_item_addons` lists respect the same scoping as the export (PetPooja id + `menu_item_id` + `variant_id`).
- **Merge history:** events touching any exported parent `menu_item_id` (useful after merges).

---

## 5. Diagnosing “wrong category” vs “wrong mapping”

### 5.1 Export shows Dessert but product is ice cream

- **`parent_cluster_type`** comes straight from **`menu_items.type`** (`export_cluster_review._load_current_cluster_rows` + `_load_menu_lookup`).
- Fix is usually **catalog**, not the export script: **`UPDATE menu_items SET type = 'Ice Cream', … WHERE menu_item_id = '…'`** (or a **menu merge** into the canonical ice cream row if two parents should be one).

### 5.2 Why a row ever had Dessert after seeding

`scripts/seed_from_backups.py` sets `menu_items.type` from **`data/cluster_state_backup.json`** keys shaped as **`{menu_item_id}:{type_id}`**, resolved via **`data/id_maps_backup.json`** → `type_id_to_str`. If you change **`type`** in the DB only, a later **seed from backup** can **revert** type unless **`cluster_state_backup.json`** uses the **Ice Cream** `type_id` in that key for that `menu_item_id`.

### 5.3 Incoming orders and duplicate rows

`services/clustering_service.py` + **`utils/clean_order_item.py`**:

- **`extract_variant`** runs **before** **`determine_type`** on the cleaned name.
- **`determine_type`** can still label strings containing **`Cookie`** (etc.) as **`Dessert`** even if you renamed one catalog row to Ice Cream.
- New lines match **`menu_items`** on **`(name, type)`**. A type-only update can cause **new** `(name, Dessert)` rows to appear unless **`clean_order_item.py`** or **merges** align behavior.

---

## 6. Fix patterns (choose what matches the case)

| Goal | Approach |
|------|-----------|
| Same variants, one canonical parent | `utils.menu_utils.merge_menu_items(conn, source_menu_item_id, target_menu_item_id)` |
| Different variants / subclusters | `merge_menu_items_with_variant_mappings(conn, source_id, target_id, variant_mappings)` with every source variant mapped |
| Wrong `type` only, same `menu_item_id` | `UPDATE menu_items SET type = …` then **align `cluster_state_backup.json`** key `menu_item_id:type_id` if you rely on seed backups |

After structural DB changes: re-run exports, spot-check **`menu_item_variants`** and order counts on the target.

---

## 7. JSON backups (stay consistent with the DB)

- **`data/cluster_state_backup.json`**: cluster keys **`menu_item_id` + `type_id`**. When you permanently move an item to Ice Cream in the catalog, update the key to use the **Ice Cream** `type_id` (see `id_maps_backup.json` → `type_id_to_str`) so **`seed_from_backups`** does not restore Dessert.
- **`data/id_maps_backup.json`**: usually **unchanged** for a type move (global `type_id_to_str` entries stay; you only change which `type_id` appears in the cluster key for that menu item).

Validate after edits:

```bash
python3 -c "import json; json.load(open('data/cluster_state_backup.json')); json.load(open('data/id_maps_backup.json')); print('OK')"
```

---

## 8. Menu UI: Resolutions tab

- Data: **`GET /menu/resolutions/unverified`** → rows from **`menu_item_variants` WHERE `is_verified = 0`**, grouped by parent + variant.
- **New** auto-clustered mappings are inserted with **`is_verified = 0`**, so they typically **appear here** for review.
- Lines with **no** `menu_item_variants` row **do not** appear there (not built from orphan order lines).

---

## 9. Issue log (optional)

Use **`docs/CLUSTER_MAPPING_REVIEW_TRACKER.md`** to log problems with **Area** matching the export (`Dessert`, `Ice Cream`, `Extra`, …) and hierarchy refs from the relevant `*_clusters.md`.

---

## 10. Archiving snapshots

- **`tmp/done/`** can hold frozen copies for audit; see **`tmp/done/README.md`**.
- This **`menu_cleansing_exercise/`** tree is a convenient place to point **`--output-dir`** so review artifacts live next to this guide.
- **Dessert** snapshots for this exercise are archived under **`menu_cleansing_exercise/done/dessert_cluster_review/`** (regenerate with `--output-dir` pointing there if you want to refresh the same location).

---

## 11. Quick checklist after a fix

1. `sqlite3 analytics.db` — confirm `menu_items` / counts as expected.
2. If backups matter — **`cluster_state_backup.json`** key + JSON parse check.
3. `python3 scripts/export_combo_cluster_review.py --type "…" --output-dir menu_cleansing_exercise/…` — refresh local review files.
4. Spot-check **`merge_history`** section and order line lists in the new Markdown.

---

## Reference scripts

| Script | Role |
|--------|------|
| `scripts/export_combo_cluster_review.py` | Typed cluster review (Combo, Drinks, `"Ice Cream"`, Dessert, …) |
| `scripts/export_cluster_review.py` | Full cluster + merge export |
| `scripts/seed_from_backups.py` | Restore menu from `data/*_backup.json` (respects cluster key → type) |

Connection helper: **`src/core/db/connection.py`** (`get_db_connection()`).
