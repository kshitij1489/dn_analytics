# Documentation Index

Single entry point for humans and AI agents. Read this first to route to the right doc without loading every plan.

**For session conventions:** [AI_SESSION_GUIDE.md](./AI_SESSION_GUIDE.md)  
**For tool auto-discovery:** [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md), [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc)

---

## Start here

| Priority | Doc | Why |
|----------|-----|-----|
| 1 | This file | Task routing and token budget |
| 2 | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Canonical architecture: Brain vs Muscle, menu logic, schema highlights, development caveats |
| 3 | Task-specific doc from the table below | Only when the task matches |

New session rule: load **this index** + **SYSTEM_CONTEXT** before opening any 300+ line implementation plan. If your tool already auto-loaded `AGENTS.md`, `CLAUDE.md`, or `.cursor/rules`, do not attach those files again.

---

## Task routing

Read files **in order** for the task at hand. Stop when you have enough context; do not read the full chain unless needed.

| If you're working on… | Read (in order) |
|----------------------|-----------------|
| **Menu sync / merge conflicts / multi-install convergence** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) (§1–2 for design; §8+ for runbook) → [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) §17 |
| **Menu single source of truth / verification events / fresh-install convergence** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md) → [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) (overlap — conflict plan is authoritative for LWW/assignments) |
| **Cloud sync API (server-side / contract)** | [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) → [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) §2 (target design) |
| **Forecasting or ingest pipelines** | [FORECASTING_AND_SYNC.md](./FORECASTING_AND_SYNC.md) Part 1 (forecasting) or Part 2 (cloud ingest) |
| **Build / release / .dmg packaging** | [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md) |
| **macOS app won't open / signing / quarantine** | [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) |
| **AI Mode (chat, intent, LLM)** | [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) → relevant `src/ai_mode/` source |
| **Item clustering / parsing / merge UX** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → [item_clustering.md](./item_clustering.md) |
| **Merge child-variant dedupe (open work)** | [pending task.md](./pending%20task.md) → merge code in `src/core/` / `services/` |
| **General bug fix / unfamiliar area** | [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) → grep codebase → [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) if runtime/deploy |

---

## Token budget guidance

| Tier | Docs | How to use |
|------|------|------------|
| **Always load** (small) | This index, [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Attach or @-mention at session start |
| **Auto-discovered when available** (tiny) | [../AGENTS.md](../AGENTS.md), [../CLAUDE.md](../CLAUDE.md), [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc) | Do not re-attach if the tool already loaded it |
| **Load on demand** (medium–large) | Task row from table above | @-reference specific sections; read headings first |
| **Never paste whole doc into chat** | [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) (~400 lines), [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md) (~360 lines), [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) (~1100 lines) | Use @file + section anchors; summarize what you need in your own words |
| **Reference only** | [FORECASTING_AND_SYNC.md](./FORECASTING_AND_SYNC.md), [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) | Load the relevant Part/section, not the full file |

**Progressive disclosure:** index → one plan section → source file. Avoid loading two full menu plans in the same turn.

---

## Status snapshot

Pulled from doc headers (2026-07-06). See each doc for detail — do not treat this table as the source of truth. Refresh this table when a phase ships or a feature branch merges.

| Doc | Status (summary) |
|-----|------------------|
| [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) | **Signed off 2026-07-06** (§11): fleet convergence + fresh-install E2E validated; 27 server-only rows reconciled benign; self-merge no-op fix landed. Client on `sync-conflict-phase-2` (not merged to main). Open: one-time human remap of 3 legacy-key flavor families; C6/S3 digest **deferred** (fleet of 1); UI badge. |
| [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md) | **Signed off 2026-07-06**: Phases 0, 1, 3 implemented; Phase 2 superseded by design; Phase 4 not built by design. **Cross-device convergence validated** via fresh-install simulation (S1 satisfied). |
| [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) | Baseline contract (§5–15) **implemented**. Assignment/verification sync in **§17 implemented**; §16 **superseded**. |
| [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) | **All phases complete.** |
| [FORECASTING_AND_SYNC.md](./FORECASTING_AND_SYNC.md) | Combined forecasting + cloud ingest reference; last updated Feb 2026. |
| [pending task.md](./pending%20task.md) | **Open:** child-variant dedupe/reconcile during parent menu-item merge. |
| [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md) | Operational — release build via `./scripts/build_release.sh`. |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | Operational — macOS Gatekeeper, signing, backend startup. |
| [item_clustering.md](./item_clustering.md) | Conceptual reference for clustering hierarchy (no implementation status). |
| [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Canonical architecture reference (stable). |

---

## Full doc list

| File | Role |
|------|------|
| [SYSTEM_CONTEXT.md](./SYSTEM_CONTEXT.md) | Architecture & invariants |
| [INDEX.md](./INDEX.md) | This hub |
| [AI_SESSION_GUIDE.md](./AI_SESSION_GUIDE.md) | Cross-tool AI conventions |
| [../AGENTS.md](../AGENTS.md) | Root AI-agent instructions |
| [../CLAUDE.md](../CLAUDE.md) | Claude Code pointer to AGENTS + INDEX |
| [../.cursor/rules/doc-routing.mdc](../.cursor/rules/doc-routing.mdc) | Cursor rule that routes agents through this hub |
| [MENU_MERGE_CONFLICT_SYNC_PLAN.md](./MENU_MERGE_CONFLICT_SYNC_PLAN.md) | Menu conflict LWW plan + operational runbook |
| [MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md](./MENU_SINGLE_SOURCE_OF_TRUTH_PLAN.md) | Verification events & convergence plan |
| [DACHNONA_CLOUD_SYNC_API_CONTRACT.md](./DACHNONA_CLOUD_SYNC_API_CONTRACT.md) | Server wire contract |
| [FORECASTING_AND_SYNC.md](./FORECASTING_AND_SYNC.md) | Forecasting algorithms + cloud ingest schemas |
| [AI_MODE_PLAN.md](./AI_MODE_PLAN.md) | AI chat mode architecture & task list |
| [BUILD_INSTRUCTIONS.md](./BUILD_INSTRUCTIONS.md) | Build and share .dmg |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | macOS / app runtime issues |
| [item_clustering.md](./item_clustering.md) | Clustering logic explained |
| [pending task.md](./pending%20task.md) | Open merge dedupe task |
