# TEMPORARY — Menu cluster cleansing & cutover session runbook

> **THIS FILE IS TEMPORARY.** It exists only to drive the one-time menu-cluster
> cleansing / baseline cutover session. Per Step 9 below, this file — and the
> other session-specific files listed there — must be DELETED once the cleansed
> data is deployed to the remote server and verified. Do not treat this as
> permanent documentation; the permanent design lives in
> `docs/MENU_MERGE_CONFLICT_SYNC_PLAN.md`.

**Executor:** a Claude session (any model, including Sonnet). Follow the steps
IN ORDER. Do not skip a verification gate. When a step says **ASK THE USER**,
stop and ask — do not guess or proceed.

**Repos:**
- Client (desktop analytics): `/Users/kshitijsharma/Documents/projects/analytics` — branch `sync-conflict-phase-2` (STAY on this branch; do not create new branches)
- Central server (Django, LIVE production): `/Users/kshitijsharma/Documents/projects/db.dachnona` — branch `sync-conflict-phase-2` (same rule)

**Background (read these first, in this order):**
1. `docs/MENU_MERGE_CONFLICT_SYNC_PLAN.md` — the sync redesign (Phases S1–C5, implemented on these branches)
2. `docs/MENU_CLUSTER_BASELINE_CUTOVER.md` — the cutover design & rationale
3. This file — the concrete session script

**Goal:** the user's machine (THIS machine) is the **golden machine** — its
merges/resolutions are the ones to keep. Publish its full menu cluster state as
the central server's ground truth, converge every other install to it, then
clean up all session-specific files.

**Safety rules (absolute):**
- NEVER run anything against the production database from a local test command.
  Server tests need `SECRET_KEY`/`DATABASE_URL` env — only run them in CI or with
  an explicitly-local test configuration confirmed by the user.
- NEVER `git push` or deploy without the user's explicit go-ahead in that step.
- Back up before every overwrite (steps say when).
- If any verification gate fails, STOP and report — do not improvise forward.

---

## Step 0 — Preflight (local, read-only)

1. `git branch --show-current` in BOTH repos → must be `sync-conflict-phase-2`.
2. Confirm the cutover tooling exists:
   - `analytics/scripts/export_menu_assignments.py`
   - `analytics/scripts/force_reseed_menu_assignments.py`
   - `analytics/src/core/menu_assignment_bootstrap.py` contains `force_reseed_menu_assignments`
   - `db.dachnona/backend/desktop_analytics_app_sync/management/commands/import_menu_assignment_baseline.py`
   - `db.dachnona/backend/desktop_analytics_app_sync/management/commands/rebuild_order_item_assignments.py`
3. If any of these files are uncommitted (`git status`), commit them on the
   current branch of their repo with a clear message (e.g.
   `feat(sync): baseline cutover tooling (export / import / force-reseed)`).
   Commit ONLY these files — the working tree has unrelated changes; do not
   sweep them in.
4. Run the client sync tests:
   `.venv/bin/python -m pytest tests/test_menu_assignment_apply.py tests/test_menu_assignment_bootstrap.py tests/test_menu_merge_sync.py tests/test_menu_bootstrap_pull.py tests/test_customer_merge_sync.py tests/test_menu_mapping_verification_sync.py -q`
   → all must pass (36 passed as of 2026-07-05). If pytest is missing:
   `.venv/bin/pip install pytest -q`.
5. **ASK THE USER** to confirm: (a) this machine is the golden machine, (b) the
   fleet's other installs are on the Phase C1–C5 client build (older installs
   cannot converge), (c) `MENU_SYNC_ASSIGNMENT_APPLY` is NOT set to off anywhere.

## Step 1 — Cleanse: review the golden machine's data BEFORE anointing it

The baseline freezes this machine's state as truth, so first make sure it IS
clean. On this machine's `analytics.db` (find the path via
`src/core/db/connection.py` / ask the user if ambiguous):

1. Count unresolved mappings:
   `SELECT COUNT(*) FROM menu_item_variants WHERE is_verified = 0;`
2. List them (if count > 0) with item names, e.g.:
   `SELECT mv.order_item_id, mi.name, v.variant_name FROM menu_item_variants mv LEFT JOIN menu_items mi ON mi.menu_item_id = mv.menu_item_id LEFT JOIN variants v ON v.variant_id = mv.variant_id WHERE mv.is_verified = 0 LIMIT 50;`
3. Sanity checks for obvious garbage (report, don't auto-fix):
   - mappings pointing at nonexistent menu items:
     `SELECT COUNT(*) FROM menu_item_variants mv LEFT JOIN menu_items mi ON mi.menu_item_id = mv.menu_item_id WHERE mi.menu_item_id IS NULL;`
   - menu items with zero mappings:
     `SELECT COUNT(*) FROM menu_items mi LEFT JOIN menu_item_variants mv ON mv.menu_item_id = mi.menu_item_id WHERE mv.menu_item_id IS NULL;`
   - duplicate item names that maybe should be merged:
     `SELECT lower(trim(name)), type, COUNT(*) c, GROUP_CONCAT(menu_item_id) FROM menu_items GROUP BY 1,2 HAVING c > 1;`
4. Present findings to the user. **ASK THE USER** to finish any remaining
   merges/resolutions in the app UI (Menu Matrix / Resolutions tab) until they
   are satisfied. It is FINE for some rows to stay `is_verified = 0` if that is
   genuinely their state — the baseline carries the flag either way. The user
   decides when the state is "clean enough"; do not block on zero.

## Step 2 — Deploy the server changes (push + migrate + sanity)

The server branch has the S1/S2 commits plus the baseline import command; the
central server must run this code before the import.

1. **ASK THE USER** for the go-ahead, then push `sync-conflict-phase-2` in
   `db.dachnona` (or merge to `main` first — follow whatever the user says their
   deploy flow is; historically this repo versions contract revisions and
   records a deploy gate, see `git log` around `0.21.0`).
2. The user (or their deploy pipeline) deploys and runs, ON THE SERVER:
   - `python backend/manage.py migrate desktop_analytics_app_sync` (applies `0012_orderitemassignment`)
   - `python backend/manage.py rebuild_order_item_assignments --scope <scope_key>`
   (**ASK THE USER** for the correct `scope_key`; default is `default`.)
3. Sanity-test the deployed server (client-side, using the client's configured
   cloud URL + bearer token from its `system_config` `cloud_sync_url` /
   `cloud_sync_api_key`):
   - `GET <base>/desktop-analytics-sync/menu-merges?limit=1` → 200, events list, `next_cursor`
   - `GET <base>/desktop-analytics-sync/menu-assignments/snapshot?limit=1` → 200 with
     `assignments`, `watermark_seq`, `watermark_cursor` keys
   - Ingest idempotence is exercised automatically by the next scheduled client push; just
     confirm no 4xx/5xx in the response above.
   GATE: both endpoints healthy before continuing.

## Step 3 — Freeze edits

**ASK THE USER** to ensure NO ONE performs menu merges/resolutions on ANY
install from now until Step 7 says unfreeze. This guarantees no divergent event
lands with a higher sequence than the baseline.

## Step 4 — Back up

1. Golden machine: copy its `analytics.db` (and `data/*.json` backups) somewhere safe.
2. Server: **ASK THE USER** to take/confirm a DB backup or snapshot.
GATE: both backups confirmed before Step 5.

## Step 5 — Export the golden baseline and import it on the server

1. On this machine:
   `.venv/bin/python scripts/export_menu_assignments.py`
   → writes `data/menu_assignments_baseline.json`; note the printed assignment count (call it N).
2. Sanity-check the file: `kind == "menu_assignment_baseline_v1"`,
   `assignment_count == N > 0`, spot-check 2–3 entries against the local DB.
3. Get the file to the server (ask the user how — scp, etc.).
4. ON THE SERVER (user runs, or user tells you how):
   - Dry run first:
     `python backend/manage.py import_menu_assignment_baseline --file <path>/menu_assignments_baseline.json --scope <scope_key> --dry-run`
     → confirm it reports N assignments and a sensible event count (N / 500, rounded up).
   - Real import: same command without `--dry-run`.
   - GATE: output must report `order_item_assignments now <M> rows` with M ≥ N
     (M can exceed N if prior events covered order items this machine has never
     seen — that is expected), 0 rejected, and a `watermark_seq`. Record the
     watermark_seq. If events were rejected, STOP and investigate.

## Step 6 — Converge the other installs

For EACH non-golden install (the golden machine needs nothing — it already IS
the baseline; its next pull will just stale-skip):

- Preferred: press **Sync DB** in the app (or wait for the background pull
  cycle). The baseline events arrive through the normal delta and overwrite
  divergent rows; superseded local decisions appear in
  `GET /api/menu/sync-conflicts` — that is expected, not an error.
- If an install looks stuck/quarantined, hard-reset it:
  `python scripts/force_reseed_menu_assignments.py` (it prompts; `--yes` to skip).
  WARNING: this overwrites that install's unsynced local edits — the freeze in
  Step 3 makes that safe.

Verify per install: pick 2–3 order items that were known-divergent and confirm
`menu_item_variants` now matches the golden machine (same `menu_item_id`,
`variant_id`, `is_verified`).

## Step 7 — Final verification, then unfreeze

1. On the server: `SELECT COUNT(*) FROM order_item_assignments WHERE scope_key = '<scope_key>';` ≥ N.
2. Client spot-check passed on every install (Step 6).
3. Tell the user editing can resume everywhere. Normal incremental sync now
   keeps the fleet together (that is what Phases S1–C5 exist for).

## Step 8 — Push & finalize the client branch

**ASK THE USER**, then push the analytics `sync-conflict-phase-2` branch (stay
on this branch — no new branches, no merges unless the user says so).

## Step 9 — CLEANUP (mandatory — the whole point of this file being temporary)

Only after Steps 5–7 are fully done and verified:

1. DELETE these session-specific files from the analytics repo and commit the deletion:
   - `scripts/export_menu_assignments.py`
   - `scripts/force_reseed_menu_assignments.py`
   - `docs/MENU_CLUSTER_BASELINE_CUTOVER.md`
   - `data/menu_assignments_baseline.json` (and any copies lying around)
   - `docs/TEMP_MENU_CLEANSE_SESSION_RUNBOOK.md` ← **this file itself**
2. **ASK THE USER** about two optional deletions (recommend KEEPING both, since
   they are the standing "reset an install / reset the fleet" escape hatches,
   but it is the user's call):
   - the `force_reseed_menu_assignments()` function in
     `src/core/menu_assignment_bootstrap.py` (client)
   - `import_menu_assignment_baseline.py` management command (server)
3. If anything server-side is deleted, commit in `db.dachnona` too, and remind
   the user that a server change means another push + the same sanity test as
   Step 2.3.
4. Re-run the client test suite from Step 0.4 one last time → must still pass.

---
*Created 2026-07-05 by the session that built the cutover tooling. If you are
reading this long after that date, ask the user whether the cutover already
happened before doing anything.*
