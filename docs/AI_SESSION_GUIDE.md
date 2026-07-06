# AI Session Guide

Conventions for Cursor, Claude Code, Codex, Antigravity, and similar agents working in this repo.

**Hub:** [INDEX.md](./INDEX.md) · **Invariants:** [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) · **Root pointers:** [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md)

---

## Session startup checklist

1. **Route** — Read [INDEX.md](./INDEX.md); pick the task row that matches the user's goal.
2. **Load core** — @ [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) (always). Do not re-attach [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md), or Cursor rules if the tool auto-discovers them.
3. **Load targeted** — One plan or section from the routing table; skip unrelated 300+ line docs.
4. **Confirm status** — Check INDEX status snapshot before assuming a phase is done or open.
5. **Code** — Grep/read `src/` for as-built behavior; plans may lag code.

---

## @-mention / attachment conventions

| Tool | Convention |
|------|------------|
| **Cursor** | The project rule [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc) routes to the hub. Use `@docs/INDEX.md`, `@docs/SYSTEM_CONTEXT.md`, then one task doc/section. |
| **Claude Code** | `CLAUDE.md` points to `AGENTS.md` and this hub. Attach specific files only; avoid `/add-dir docs` unless doing a docs-wide audit. |
| **Codex / OpenAI** | `AGENTS.md` is the root instruction file. If context is manual, instruct: "Read docs/INDEX.md then docs/SYSTEM_CONTEXT.md only." |
| **Antigravity** | Point to `AGENTS.md` and `docs/INDEX.md` in project instructions; @-reference deep docs per task. |

**Rules:**
- @-mention **files**, not folder dumps (`docs/` entire tree wastes tokens).
- For long plans, specify section: e.g. "MENU_MERGE_CONFLICT_SYNC_PLAN §2.1 only."
- Re-@ a section when context was dropped — don't re-paste the full doc.

---

## Recommended context bundles

Estimated weight = approximate token cost if loaded whole.

### Menu sync / merge conflict

| Order | File | Weight |
|-------|------|--------|
| 1 | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | small |
| 2 | [INDEX.md](./INDEX.md) | small |
| 3 | [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) §1–2 or §8 (runbook) | medium |
| 4 | [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) §17 only | medium |
| 5 | Relevant `src/core/menu_*_sync*.py` | varies |

**Avoid in same turn:** full MENU_SINGLE_SOURCE_OF_TRUTH_PLAN + full MENU_MERGE_CONFLICT_SYNC_PLAN.

### New feature (non-sync)

| Order | File | Weight |
|-------|------|--------|
| 1 | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | small |
| 2 | [INDEX.md](./INDEX.md) | small |
| 3 | Feature-specific doc if any (e.g. [AI_MODE_PLAN.md](./AI_MODE_PLAN.md)) | medium |
| 4 | Target module under `src/` or `services/` | varies |

### Bug fix

| Order | File | Weight |
|-------|------|--------|
| 1 | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | small |
| 2 | [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) if deploy/runtime | small |
| 3 | Failing module + tests | varies |
| 4 | Related plan section only if bug is in sync/menu domain | medium |

### Item clustering / parsing

| Order | File | Weight |
|-------|------|--------|
| 1 | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | small |
| 2 | [item_clustering.md](./item_clustering.md) | small |
| 3 | `services/clustering_service.py`, `data_cleaning/` | varies |

---

## When to update docs vs code

| Update **code** | Update **docs** |
|----------------|-----------------|
| Fixing behavior, tests, APIs | Phase status changed (update plan header + INDEX snapshot) |
| Refactor with no contract change | New invariant agents must know → SYSTEM_CONTEXT or AGENTS.md |
| | New major plan or superseded section → INDEX routing + status table |
| | Operational runbook steps discovered during incident → relevant plan § or TROUBLESHOOTING |

**Do not** rewrite long plans for small fixes. **Do** add a one-line status note to the plan header and INDEX snapshot when a phase ships or is abandoned.

**Filename note:** [pending task.md](./pending%20task.md) has a space in the name — use the exact path when linking.
