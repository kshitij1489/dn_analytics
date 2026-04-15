# Review of Commit `0735885b3a574cb58072c5de4668f1a90d057c02`

## Scope

Goal reviewed: clean noisy menu-item clusters, preserve manual remaps, expose remap tooling in the election/UI flow, keep local DB + backup JSONs in sync, and make sure future incoming data either lands in the cleaned cluster or shows up in Resolutions for follow-up.

## What I validated

1. Read the commit diff for:
   - `utils/menu_utils.py`
   - `scripts/seed_from_backups.py`
   - `src/core/menu_bootstrap_sync.py`
   - `src/core/menu_merge_sync.py`
   - `src/api/routers/menu.py`
   - `ui_electron/src/pages/Menu.tsx`
   - `utils/clean_order_item.py`
   - `services/clustering_service.py`
2. Ran targeted tests:
   - `python3 -m unittest tests.test_menu_utils tests.test_seed_from_backups tests.test_menu_bootstrap_pull tests.test_menu_queries tests.test_clean_order_item tests.test_menu_merge_sync`
   - Result: `Ran 16 tests ... OK`
3. Ran local data integrity checks against `analytics.db` and `data/*.json`.
4. Ran two synthetic checks:
   - a future alias that does not match the new normalization rules
   - a remote bootstrap pull into a DB that still contains stale pre-cleanup menu items

## Findings

### Finding 1: bootstrap pull is not fully convergent for cleaned menu data

Severity: high

`apply_menu_bootstrap_snapshot()` writes the latest JSON snapshot and calls `perform_seeding()`, but that seeding path only upserts menu items, variants, and `menu_item_variants`; it does not delete local menu items or variants that disappeared after a cleanup. It also explicitly warns that addon remaps are not restored from the bootstrap snapshot.

Relevant code:
- `src/core/menu_bootstrap_sync.py:186-229`
- `scripts/seed_from_backups.py:37-99`

What I reproduced:
- Local stale source item existed before pull.
- Remote snapshot contained only the cleaned target item.
- After bootstrap apply:
  - `menu_item_variants` was relinked correctly.
  - `order_items` was relinked correctly.
  - the stale source `menu_items` row still remained locally.

Impact:
- Another user who pulls only the bootstrap snapshot can still retain old noise rows locally.
- Addon remaps are also not fixed by bootstrap alone.
- Full convergence currently depends on the separate menu-merge event stream also being uploaded and pulled.

### Finding 2: cloud propagation is eventual, not guaranteed at commit time

Severity: medium

The merge/resolution transaction records sync events and exports backup JSONs locally, but upload to the central server is deferred to either:
- manual `POST /sync/client-learning`, or
- the background scheduler loop.

Relevant code:
- `utils/menu_utils.py:697-704`
- `utils/menu_utils.py:808-817`
- `utils/menu_utils.py:1310-1317`
- `src/api/routers/operations.py:90-98`
- `src/core/services/cloud_sync_scheduler.py:75-137`

Local state at review time:
- `merge_history = 91`
- `menu_merge_sync_events = 91`
- `menu_merge_sync_events uploaded_at IS NULL = 4`

Impact:
- I cannot sign off that "other app users already have these remaps" from this workspace alone.
- The local queue shows 4 menu merge/resolution events still waiting to upload.
- Network access was not available here, so I could not verify the actual remote server state.

## Step-by-step analysis of the 7 requested points

### 1. Review the commit

The core local behavior is improved and mostly correct:
- merge and resolution flows now retarget `suggestion_id` references and preserve undo history
- order items, addon rows, and `menu_item_variants` are all updated in the local transaction
- local backup JSONs are exported immediately after merge/resolution
- the UI now supports more granular item + variant review and resolution

Relevant code:
- `utils/menu_utils.py:656-704`
- `utils/menu_utils.py:767-817`
- `utils/menu_utils.py:1183-1350`

### 2. Check whether the cleanup/remap changes preserve mappings without data loss

Local result: yes, for the current workspace state.

I verified:
- `menu_items` in DB: `102`
- `variant_id_to_str` in backup JSON: `20`
- `variants` in DB: `20`
- `menu_item_variants` in DB: `475`
- cluster assignments represented in `cluster_state_backup.json`: `475`

Exact consistency checks passed:
- DB menu-item IDs exactly match `id_maps_backup.json`
- DB variant IDs exactly match `id_maps_backup.json`
- DB mapping rows exactly match `cluster_state_backup.json`
- `variant_id_to_meta` matches DB `variants.unit/value` exactly

Integrity checks also passed:
- 0 orphan `menu_item_variants -> menu_items`
- 0 orphan `menu_item_variants -> variants`
- 0 orphan `order_items -> menu_items`
- 0 orphan `order_items -> variants`
- 0 orphan `order_item_addons -> menu_items`
- 0 orphan `order_item_addons -> variants`
- 0 unverified mapping rows in the current DB
- 0 dead `suggestion_id` references

Conclusion:
- I do not see local evidence of mapping loss in the current cleaned dataset.

### 3. Make sure DBs and JSONs are updated properly, and whether that reaches the central server

Local DB + JSON update path is working:
- merge/resolution updates local DB rows
- then `export_to_backups()` writes `data/id_maps_backup.json` and `data/cluster_state_backup.json`
- the current DB and the current JSON backups match exactly

Relevant code:
- `scripts/seed_from_backups.py:112-180`

Central server status is not fully guaranteed from this review:
- upload is deferred, not inline with the merge transaction
- 4 menu merge events are still pending local upload
- I could not verify remote receipt because network access was unavailable here

Conclusion:
- local persistence: yes
- guaranteed central propagation at review time: no

### 4. Verify that incoming new entries map to updated/remapped clusters

For the cases you explicitly normalized in `clean_order_item.py`, yes.

Verified by test:
- `Waffle Cone` now normalizes to `Butter Waffle Cones` + `1_PIECE`
- `Butter Waffle Cone [2 Pieces]` now normalizes to `Butter Waffle Cones` + `2_PIECES`
- `OrderItemCluster.add()` reuses the existing canonical cluster for both cases

Relevant code:
- `utils/clean_order_item.py:260-274`
- `utils/clean_order_item.py:375-414`
- `services/clustering_service.py:117-160`
- `tests/test_clean_order_item.py:48-129`

Conclusion:
- exact future entries that match the new normalization rules will land in the cleaned cluster.

### 5. Verify the fallback when incoming data does not map cleanly

Fallback result: yes, it shows up in Resolutions.

Why:
- `OrderItemCluster.add()` creates a new unverified `menu_item_variants` row when the cleaned name + type does not exactly match an existing item.
- If a similar verified item exists, it stores `suggestion_id`.
- `fetch_unverified_items()` reads all `menu_item_variants.is_verified = 0` pairs and shows them in the Resolutions workflow.

Relevant code:
- `services/clustering_service.py:132-160`
- `src/core/queries/menu_queries.py:347-402`

Synthetic check I ran:
- Input alias: `Butter Waffle Conez`
- Result:
  - it did not force-map into the canonical cluster
  - it created a new unresolved row
  - it carried a suggestion to `Butter Waffle Cones`
  - it showed up in the Resolutions query

Conclusion:
- if future data misses your exact normalization/remap rules, it should surface for manual review instead of silently disappearing.

### 6. If there is no guarantee, could it create a new cluster/subcluster, and will that show in Menu Item Resolutions?

Yes.

Current guarantee level:
- guaranteed for names that hit the new `clean_order_item()` normalization rules
- not guaranteed for unseen aliases, spelling drift, or new naming patterns

Current fallback behavior:
- new unverified cluster/subcluster is created
- it is visible in Menu Item Resolutions because Resolutions is driven by `menu_item_variants.is_verified = 0`

The only important caveat is sync/collaboration:
- another user pulling only the bootstrap snapshot may still keep stale cleaned-away menu items locally
- another user also needs the merge-event stream to converge fully

### 7. Suggestions to improve future incoming mapping

These are the highest-value next steps:

1. Make bootstrap pull authoritative, not additive.
   - Add a "replace/prune" mode in `perform_seeding()` / `apply_menu_bootstrap_snapshot()` so rows absent from the snapshot are deleted or archived.
   - Without this, cleanup noise can survive on other clients.

2. Treat addon remaps as first-class in bootstrap reconciliation.
   - Today the code explicitly warns that bootstrap snapshots do not restore `order_item_addons`.
   - Either include addon assignments in the snapshot or keep relying on merge events, but then the event upload path must be airtight.

3. Make cloud push part of the merge/resolution success path, or show sync debt in the UI.
   - Right now the cleanup can succeed locally while remote propagation is still pending.
   - At minimum, expose a visible "4 menu changes pending upload" signal.

4. Populate variant metadata at ingestion time for brand-new variants.
   - `OrderItemCluster.add()` inserts new variants without `unit/value`.
   - That does not break clustering, but it can weaken volume analytics and make later cleanup harder.
   - Relevant code: `services/clustering_service.py:147-152`

5. Add a regression test for bootstrap convergence.
   - Create a test that starts with stale local items, applies a cleaned snapshot, and asserts whether stale items are removed.
   - Right now that behavior is untested, and my synthetic reproduction shows it fails.

6. Add alias-learning or explicit remap tables.
   - Today future coverage depends mainly on `clean_order_item()` rules plus fuzzy suggestion.
   - A durable `raw_alias -> canonical menu_item_id + variant_id` table would let you preserve manual cleanup knowledge directly, instead of re-encoding it only as string rules.

## Overall conclusion

Local cleanup quality is good:
- mappings were preserved locally
- DB and JSON backups are in sync
- the new normalization rules correctly route the known cleaned examples
- unresolved future aliases do surface in Resolutions

But I cannot give a full end-to-end sign-off for "all other app users will definitely receive the same cleaned state" yet, for two reasons:
- bootstrap pull is additive and leaves stale rows behind
- 4 menu merge events are still queued locally, so remote convergence is not guaranteed at this moment
