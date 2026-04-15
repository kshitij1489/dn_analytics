> **Status — archived under `menu_cleansing_exercise/done/`**  
> This Service export was moved here from an earlier working tree (`tmp/service_cluster_review/`). Snapshot only; for an up-to-date view, re-run `python3 scripts/export_combo_cluster_review.py --type Service`.

---

# Service cluster review

Generated at: 2026-04-15 09:29:29 UTC

Hierarchy legend (refs use the same numbers/letters):

- **1)** Parent cluster name, type
  - **a)** Variant (child cluster) name
    - **1)** Raw name(s) on order rows for that `menu_item_variants` mapping

Cross-reference key: **`1-a-1`** = parent **1)**, variant **a)**, raw row **1)**.

_Category filter: `menu_items.type` / `parent_cluster_type` = **Service**._

---

## 1) Ice Cream Factory Visit & Trials, Service

### a) FAMILY

- Stats: 1 mappings (1 verified); 10 item rows, 0 addon rows.

- **1)** Ice Cream Factory Visit & Trials(family)
  - Source kind: item; 10 item rows, 0 addon rows.
  - **order_items rows** (10):
    1. `order_item_id`=825 · `order_id`=474 · qty=1 · total_price=847.5 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    2. `order_item_id`=4157 · `order_id`=2484 · qty=1 · total_price=847.5 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    3. `order_item_id`=4639 · `order_id`=2760 · qty=1 · total_price=847.5 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    4. `order_item_id`=4767 · `order_id`=2826 · qty=1 · total_price=1000 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    5. `order_item_id`=7784 · `order_id`=4479 · qty=1 · total_price=1000 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    6. `order_item_id`=10926 · `order_id`=6248 · qty=1 · total_price=847.5 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    7. `order_item_id`=12547 · `order_id`=7113 · qty=1 · total_price=1000 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    8. `order_item_id`=13877 · `order_id`=7816 · qty=1 · total_price=1000 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    9. `order_item_id`=15073 · `order_id`=8430 · qty=1 · total_price=1000 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
    10. `order_item_id`=19484 · `order_id`=10592 · qty=2 · total_price=2000 · petpooja_itemid=1282786652 · name_raw=Ice Cream Factory Visit & Trials(family)
  - **order_item_addons rows** (0):
    - _(none)_


### b) SINGLE

- Stats: 1 mappings (1 verified); 2 item rows, 0 addon rows.

- **1)** Ice Cream Factory Visit & Trials (single)
  - Source kind: item; 2 item rows, 0 addon rows.
  - **order_items rows** (2):
    1. `order_item_id`=10056 · `order_id`=5768 · qty=1 · total_price=423.7 · petpooja_itemid=1282786651 · name_raw=Ice Cream Factory Visit & Trials (single)
    2. `order_item_id`=11538 · `order_id`=6574 · qty=1 · total_price=423.7 · petpooja_itemid=1282786651 · name_raw=Ice Cream Factory Visit & Trials (single)
  - **order_item_addons rows** (0):
    - _(none)_


## 2) School Kids Factory Visit, Service

### a) 1_PIECE

- Stats: 1 mappings (1 verified); 4 item rows, 0 addon rows.

- **1)** School Kids Factor Visit
  - Source kind: item; 4 item rows, 0 addon rows.
  - **order_items rows** (4):
    1. `order_item_id`=3029 · `order_id`=1816 · qty=20 · total_price=5714.2 · petpooja_itemid=1291692931 · name_raw=School Kids Factor Visit
    2. `order_item_id`=3075 · `order_id`=1849 · qty=20 · total_price=5714.2 · petpooja_itemid=1291692931 · name_raw=School Kids Factor Visit
    3. `order_item_id`=3185 · `order_id`=1911 · qty=20 · total_price=5714.2 · petpooja_itemid=1291692931 · name_raw=School Kids Factor Visit
    4. `order_item_id`=3229 · `order_id`=1941 · qty=20 · total_price=5714.2 · petpooja_itemid=1291692931 · name_raw=School Kids Factor Visit
  - **order_item_addons rows** (0):
    - _(none)_


---

## ID index (trace hierarchy → database)

Use **hierarchy_ref** to match sections above (`1-a-1` = parent **1)**, variant **a)**, raw row **1)**).

When there are many lines, **order_item_ids** in this table may be truncated; the numbered **order_items rows** list under each mapping is complete.

| hierarchy_ref | menu_item_id | variant_id | child_cluster_name | source_item_id | source_item_kind | raw_names | mapping_is_verified | item_row_count | addon_row_count | order_item_ids | order_item_addon_ids |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1-a-1 | 9dfd092f-1a87-5eec-9536-b4f0acb02f6c | 190eb2e5-5671-55be-ac33-57907b87a2ce | FAMILY | 1282786652 | item | Ice Cream Factory Visit & Trials(family) | ✅ | 10 | 0 | 825,4157,4639,4767,7784,10926,12547,13877,15073,19484 | (none) |
| 1-b-1 | 9dfd092f-1a87-5eec-9536-b4f0acb02f6c | f2a1ea5a-2b0b-562d-8c50-808760640024 | SINGLE | 1282786651 | item | Ice Cream Factory Visit & Trials (single) | ✅ | 2 | 0 | 10056,11538 | (none) |
| 2-a-1 | 27568a25-6849-5e85-be25-0db39ea3a641 | f8b92f1e-8f3b-5a1c-8615-215dd0b3a4cc | 1_PIECE | 1291692931 | item | School Kids Factor Visit | ✅ | 4 | 0 | 3029,3075,3185,3229 | (none) |

---

## Merge history (Service-related)

_No merge events matched these service clusters._
