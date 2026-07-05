# Menu cluster baseline cutover — runbook

**Audience:** Operator performing the one-time convergence of all installs onto a single golden cluster state.
**Related:** [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) (the design), [CLOUD_SYNC_DIVERGENCE.md](./CLOUD_SYNC_DIVERGENCE.md).
**Status:** Tooling built (2026-07-05). Not yet executed.

---

## What this does

Makes every install's menu clustering identical by publishing **one anointed machine's full
per-order-item state** as the server's authoritative ground truth, then converging the rest to it.

The incremental sync (Phases S1–C5) only materializes ground truth for *merged / resolved* order
items, because that is all the event log contains. A first-time convergence needs the **complete**
state of one machine — every `menu_item_variants` row, merged or not. These three tools provide it:

| Piece | Where | What |
|-------|-------|------|
| `scripts/export_menu_assignments.py` | client (golden machine) | Dumps full `menu_item_variants` → `data/menu_assignments_baseline.json` |
| `import_menu_assignment_baseline` mgmt command | server (`db.dachnona`) | Ingests that file as explicit-assignment menu-merge events → materializes `OrderItemAssignment` at a fresh, highest watermark |
| `scripts/force_reseed_menu_assignments.py` | client (other machines) | Hard-resets an install to the server snapshot (escape hatch; normal pull also converges) |

**Why it converges deterministically:** the baseline is ingested as real events, so each gets a
fresh, strictly-increasing `MenuMergeEvent.id`. Ingested last, they outrank every prior event
(including other machines' divergent decisions) under the per-row last-write-wins guard. The
baseline therefore wins everywhere it is applied — on the server and on every client — and
`rebuild_order_item_assignments` reconstructs the same result because nothing is written out of band.

## Preconditions

- **Whole fleet on the Phase C1–C5 client build.** An older install cannot consume the snapshot or
  apply assignments; it stays diverged until upgraded.
- **`MENU_SYNC_ASSIGNMENT_APPLY` is enabled** (default). The baseline uses `kind:
  menu_assignment_baseline_v1`, which only the assignment applier understands; with the flag off,
  clients quarantine the baseline events instead of applying them.
- Server Phases S1 + S2 deployed, migration `0012` applied, `rebuild_order_item_assignments` run once.
- You have identified the single golden machine and confirmed its clustering is the state you want.

## Runbook

Rehearse the whole sequence on **staging** first (a copy of the server DB + a throwaway second
install) and confirm convergence before touching production.

1. **Back up** the server DB and the golden machine's local DB. Non-negotiable — this overwrites
   cluster state fleet-wide.
2. **Freeze menu edits** on every install (no merges / resolutions) until step 6. This guarantees no
   divergent event lands with a higher id than the baseline.
3. **Export** on the golden machine:
   ```
   python scripts/export_menu_assignments.py
   # → data/menu_assignments_baseline.json  (N assignments)
   ```
4. **Import** on the server (dry-run first to check counts):
   ```
   python backend/manage.py import_menu_assignment_baseline --file menu_assignments_baseline.json --scope <scope_key> --dry-run
   python backend/manage.py import_menu_assignment_baseline --file menu_assignments_baseline.json --scope <scope_key>
   ```
   Confirm the reported `order_item_assignments` row count matches the export's assignment count and
   note the new `watermark_seq`.
5. **Converge the other installs.** Either let each pull normally (the baseline events flow through
   the delta and overwrite local rows — divergent decisions raise supersede notices in
   `/api/menu/sync-conflicts`), or hard-reset each immediately:
   ```
   python scripts/force_reseed_menu_assignments.py
   ```
   The golden machine needs no action — it is already the baseline.
6. **Verify, then unfreeze.** Spot-check a few previously-divergent order items across installs; run
   the convergence digest if C6/S3 is available. Resume normal editing.

## Rollback

- Server: the import only *adds* events and assignment rows. To revert to pre-baseline truth,
  restore the server DB backup (or, if only assignments are wrong, re-run
  `rebuild_order_item_assignments` after removing the baseline events).
- A client that looks wrong: restore its local DB backup, or re-run `force_reseed_menu_assignments`
  once the server truth is correct.

## Repeatability

Re-importing the **same** export file is idempotent (deterministic `remote_event_id`s dedupe). A
**new** export supersedes the previous baseline (new content → new ids → higher watermark). So this
is also the standing "reset the fleet to a known-good state" procedure, not just a one-time fix.
