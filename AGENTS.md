# Agent Instructions

D&N Analytics — desktop + backend app for order ingestion, menu clustering, forecasting, and cloud sync with Dachnona.

**Doc hub:** [docs/INDEX.md](docs/INDEX.md) — read first for task routing and token budget.  
**Session guide:** [docs/AI_SESSION_GUIDE.md](docs/AI_SESSION_GUIDE.md) — cross-tool conventions.  
**Claude pointer:** [CLAUDE.md](CLAUDE.md) mirrors this file for Claude Code auto-discovery.

---

## Critical invariants

From [docs/SYSTEM_CONTEXT.md](docs/SYSTEM_CONTEXT.md):

- **Brain (persistent):** durable menu mapping artifacts in `data/` — especially `data/item_parsing_table.csv` when present plus JSON reseed/export files.
- **Muscle (transient):** local SQLite DB (`analytics.db`) can be wiped and rebuilt; cloud/server uses PostgreSQL.
- **Never delete** `data/item_parsing_table.csv` or reseed/export artifacts without explicit user request — this can lose manual merges and aliases.
- Menu item IDs are transient across rebuilds; names and durable mapping artifacts are the stable references.

---

## How to use docs

1. Read [docs/INDEX.md](docs/INDEX.md) and [docs/SYSTEM_CONTEXT.md](docs/SYSTEM_CONTEXT.md) at session start.
2. @-reference or attach **only** the task-specific doc from the routing table — not every plan.
3. For large plans (menu sync, API contract), read **sections** via @-mention — do not paste 400+ lines into chat.
4. For menu sync, **[docs/MENU_SYNC_ARCHITECTURE.md](docs/MENU_SYNC_ARCHITECTURE.md)** is the as-built reference for LWW/assignment behavior and the operational runbook; API contract §17 is authoritative for wire format.

---

## Key commands

From project `Makefile`:

```bash
make start       # Backend + frontend (Electron dev)
make backend     # Backend only
make frontend    # Frontend only (ui_electron)
make verify      # Verify SQLite connection and schema
make clean       # Remove analytics.db (RESET — destructive)
make sync        # Incremental order sync
make reload-all  # Full order reload
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
| `services/` | Data ingestion, clustering |
| `data/` durable menu artifacts | Brain — preserve always |
| `docs/` | Architecture and implementation plans |

---

## Do not

- Delete or overwrite durable menu artifacts in `data/` without explicit user request.
- Load all menu sync plans into context at once — use INDEX routing.
- Implement against DACHNONA_CLOUD_SYNC_API_CONTRACT.md §16 (superseded; use §17).
- Run `make clean` expecting data to survive — it removes the local DB.
- Force-push to `main` without user approval.

---

## When stuck

1. [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — macOS app / signing / backend logs.
2. [docs/INDEX.md](docs/INDEX.md) status snapshot — what's implemented vs open.
3. Grep `src/core/*_sync*.py` and `src/core/*_shipper*.py` for live sync behavior.
