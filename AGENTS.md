# Agent Instructions

D&N Analytics — desktop + backend app for order ingestion, menu clustering, forecasting, and cloud sync with Dachnona.

**Doc hub:** [docs/INDEX.md](docs/INDEX.md) — read first for task routing and token budget.  
**Session guide:** [docs/SESSION_AND_ALL_STORES.md](docs/SESSION_AND_ALL_STORES.md) Part A — cross-tool conventions.  
**Claude pointer:** [CLAUDE.md](CLAUDE.md) mirrors this file for Claude Code auto-discovery.

---

## Critical invariants

From [docs/SYSTEM_CONTEXT.md](docs/SYSTEM_CONTEXT.md):

- **Brain (persistent):** central server state (`db.dachnona`, PostgreSQL) plus the local SQLite cache (`analytics.db`). `data/cluster_state_backup.json` / `data/id_maps_backup.json` are explicit dev/disaster-recovery CLI exports — never routine runtime stores or ground truth.
- **Muscle (transient):** local SQLite DB (`analytics.db`) can be wiped and rebuilt from server pulls + POS orders; cloud/server uses PostgreSQL.
- **Never delete** `data/cluster_state_backup.json`, `data/id_maps_backup.json`, or other reseed/export artifacts without explicit user request — this can lose manual merges and aliases.
- Menu item IDs are transient across rebuilds; names and durable mapping artifacts are the stable references.

---

## How to use docs

1. Read [docs/INDEX.md](docs/INDEX.md) and [docs/SYSTEM_CONTEXT.md](docs/SYSTEM_CONTEXT.md) at session start.
2. @-reference or attach **only** the task-specific doc from the routing table — not every plan.
3. For large plans (menu sync, API contract), read **sections** via @-mention — do not paste 400+ lines into chat.
4. For menu sync, **[docs/MENU_SYNC_ARCHITECTURE.md](docs/MENU_SYNC_ARCHITECTURE.md)** is the as-built reference for LWW/assignment behavior and the operational runbook; API contract §17 in [contracts/central_server_analytics_app_api_contract.md](contracts/central_server_analytics_app_api_contract.md) is authoritative for wire format. Use the fixtures in `contracts/fixtures/1/` to understand exactly what JSON payloads to send or expect. Any change under `contracts/` must also be copied to `db.dachnona/contracts/desktop_analytics_app/` (see [contracts/README.md](contracts/README.md) for the twin-sync rule and server vs desktop checklist).
5. For order loading/replay/addons/stat bugs, read **[docs/ORDER_INGEST_PIPELINE.md](docs/ORDER_INGEST_PIPELINE.md)** before editing `services/load_orders.py`.
6. To locate a module by concern, use **[docs/FILE_INVENTORY.md](docs/FILE_INVENTORY.md)** instead of grepping blind.

---

## Key commands

From project `Makefile`:

```bash
make start       # Backend + frontend (Electron dev)
make backend     # Backend only
make frontend    # Frontend only (ui_electron)
make verify RESTAURANT_ID=<id>  # Verify one bound profile's SQLite schema
make clean       # Remove analytics.db (RESET — destructive)
make sync RESTAURANT_ID=<id>        # Incremental order sync
make reload-all RESTAURANT_ID=<id>  # Full order reload
```

Release build (see [docs/BUILD_INSTRUCTIONS.md](docs/BUILD_INSTRUCTIONS.md)):

```bash
./scripts/build_release.sh
./scripts/install_to_applications.sh   # optional: install to /Applications
```

---

## Project layout (quick)

| Path | Purpose |
|------|---------|
| `src/` | FastAPI backend, sync shippers, menu merge logic |
| `ui_electron/` | Electron + React frontend |
| `services/` | Order ingest (`load_orders.py`), clustering (`clustering_service.py`) |
| `utils/` | Name parsing, id generation, mapping core, variant enforcement |
| `data/` JSON exports | Dev/disaster-recovery exports — preserve, but not runtime stores |
| `docs/` | Architecture and implementation plans |

Full annotated map: [docs/FILE_INVENTORY.md](docs/FILE_INVENTORY.md).

**Multi-restaurant:** one SQLite profile per restaurant; **All Stores** is a local read-only federation over the bound profiles — never a database, never an `X-Restaurant-ID` value. Mutations, drilldowns, SQL Console, and AI Mode require one physical restaurant. See [docs/SESSION_AND_ALL_STORES.md](docs/SESSION_AND_ALL_STORES.md) Part B.

---

## Do not

- Delete or overwrite durable menu artifacts in `data/` without explicit user request.
- Load all menu sync plans into context at once — use INDEX routing.
- Implement against the authoritative API contract in `contracts/central_server_analytics_app_api_contract.md` (read the specific section you need, e.g. §17). The exact request/response JSON shapes are pinned by fixtures in `contracts/fixtures/1/`.
- Run `make clean` expecting data to survive — it removes the local DB.
- Force-push to `main` without user approval.
- Add a mid-order `conn.commit()` in `load_orders.process_order`, or update a denormalized counter in only one of the two stat paths (inline-increment vs. replay-recompute) — see [docs/ORDER_INGEST_PIPELINE.md](docs/ORDER_INGEST_PIPELINE.md) §8.

---

## When stuck

1. [docs/INDEX.md](docs/INDEX.md) status snapshot — what's implemented vs open.
2. Grep `src/core/*_sync*.py` and `src/core/*_shipper*.py` for live sync behavior.
