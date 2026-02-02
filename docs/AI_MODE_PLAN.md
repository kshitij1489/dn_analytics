# AI Mode — Plan & Task List

**Status: All phases complete.**

This document is the single plan for AI Mode. It merges the former **Error / Crash Report Logging** and **LLM Response Cache** plans; see sections below and **Future plan of actions** for improvements (e.g. error-log concurrency, summary/report cache deferral).

## Your Flow (Validated)

Your proposed flow is **correct** and aligns well with a single “brain” on the Python side. Here it is mapped to what you have and what to add:

| Step | Your description | Current state | Action |
|------|------------------|---------------|--------|
| 1 | Incoming query: Electron UI → Python FastAPI | ✅ Done | None |
| 2 | Python server = AI brain | ✅ Done | None |
| 3a | User question → API | ✅ Done (`/ai/chat`, `AIMode.tsx`) | None |
| 3b | Spelling correction (small LLM) | ✅ Done (`ai_mode/llm/spelling.py`) | None |
| 3c | Intent recognition (LLM) | ✅ Done (`classify_intent`) | None |
| 3d | Decide sequence of actions from intent | ✅ Done (`planner`, `actions`) | None |
| 3e | Execute sequence (question + context + db) | ✅ Done (handlers + context) | None |
| 3f | Return result to UI | ✅ Done (text / table / chart / multi) | None |

**All steps complete.**

---

## Architecture (Confirmed)

```
[Electron UI - AIMode.tsx]
         │
         │ POST /api/ai/chat  { prompt, history }
         ▼
[FastAPI - routers/ai.py]
         │
         ▼
[services/ai_service.py - process_chat]
         │
         ├─► (NEW) Spelling correction
         ├─► Intent recognition (classify_intent)
         ├─► (EXTEND) Action planner → sequence of actions
         ├─► Execute actions (SQL, chart, report, etc.)
         └─► Format response → AIResponse
         │
         ▼
[Electron UI] displays content (text / table / chart)
```

---

## Task List

### Spelling correction & pipeline

- [x] **1.1** Add a spelling/grammar correction step in `ai_service.py` (before `classify_intent`).
  - Input: raw user `prompt`; output: corrected string.
  - Use a small/fast LLM call (e.g. same model with a short system prompt, or a dedicated small model) so latency stays low.
- [x] **1.2** Add a prompt in `services/prompts.py` for correction (e.g. “Correct typos and fix obvious grammar; preserve meaning and return only the corrected question”).
- [x] **1.3** Use the corrected text for all downstream steps (intent, SQL, chart, etc.) and optionally log both raw and corrected in `ai_logs` for debugging.

### Intent & action vocabulary

- [x] **2.1** Define an explicit list of **actions** (e.g. `RUN_SQL`, `GENERATE_CHART`, `GENERATE_SUMMARY`, `GENERATE_REPORT`, `GENERAL_CHAT`, `ASK_CLARIFICATION`). Map current intents to these.
- [x] **2.2** Extend the router prompt in `prompts.py` so the LLM returns **intent + suggested sequence** (e.g. one or more actions in order), or a single action for now.
- [x] **2.3** Add a simple **action planner** in `ai_service.py`: given intent (and later, multiple intents), return an ordered list of action identifiers.

### Multi-step execution

- [x] **3.1** Refactor `process_chat` so it:
  - Takes corrected prompt + history.
  - Gets intent (and optional sequence) from classifier/planner.
  - Iterates over the action sequence; for each action, runs the right handler (SQL, chart, summary, report, etc.) with (prompt, context, db).
- [x] **3.2** Define **context** passed between steps: e.g. previous SQL result, previous chart config, or summary text, so “follow-up” actions can use prior results.
- [x] **3.3** Combine results from multiple actions into one `AIResponse` (e.g. one table + one chart + one summary), or define a **multi-part** response format (e.g. `content` as list of `{ type, content }`) and update the UI to render it.

### New result types (reports / summaries)

- [x] **4.1** Add **SUMMARY** (and optionally **REPORT**) as action types: e.g. “Summarize last week’s sales” → run SQL or use existing insights, then pass data + user question to LLM to produce short summary text.
- [x] **4.2** Return these as `type: "text"` with structured explanation, or introduce `type: "summary"` / `type: "report"` if you want different UI (e.g. PDF export later).
- [x] **4.3** Ensure “reports” can pull from existing SQL/charts/summaries (reuse Phase 3 context) so the brain can combine tables + charts + narrative.

### UI & observability

- [x] **5.1** If you expose “corrected question” in the API response, optionally show it in the UI (e.g. “You asked: …” with corrected version in subtle text).
- [x] **5.2** Support multi-part responses in `AIMode.tsx`: e.g. loop over `content[]` and render each part (text, table, chart) in order.
- [x] **5.3** Add minimal logging/tracing (e.g. intent, action list, which step failed) so you can debug the brain without touching the DB every time.

### Pipeline metadata storage (debug / evaluation / cache)

**Goal:** Persist pipeline metadata (not large result data) so you can debug, evaluate the flow, and later cache outputs of individual steps.

**Store (per request):**

| Field | Description | Store? |
|-------|-------------|--------|
| User question (raw) | Original prompt from the user | Yes |
| Spelling-corrected query | Output of spelling correction step | Yes |
| Intent | Classifier intent (e.g. SQL_QUERY, CHART_REQUEST) | Yes |
| Action sequence | Ordered list of actions executed (e.g. RUN_SQL, GENERATE_SUMMARY) | Yes |
| Output of generate_sql | The **SQL text** generated (not the query result) | Yes |
| Explanation | Natural-language explanation(s) from the pipeline | Yes |
| Output of SQL query | Result rows / result data | No (can be huge; exclude) |

**Do not store:** Full SQL result sets, large chart data payloads, or other bulky content—only the metadata and small text outputs above. This keeps the table small and suitable for debugging, evaluation, and future step-level caching (e.g. cache by corrected_query + action and reuse SQL text or explanation).

**Tasks:**

- [x] **6.1** Extend schema for AI pipeline metadata:
  - Done: Added columns to `ai_logs`: `raw_user_query`, `corrected_query`, `action_sequence` (JSON text), `explanation`. `response_payload` is limited to a small summary when large (e.g. row count, type). Schema is in `database/schema_sqlite.sql` (single source; apply for fresh DB).
- [x] **6.2** In the orchestrator (or logging module), after the pipeline runs, persist: raw prompt, corrected prompt, intent, action_sequence, SQL text from each step that generated SQL, and explanation(s). Do **not** persist full table/chart result payloads.
- [x] **6.3** Per-step metadata: action_sequence stored as JSON; explanation(s) from parts concatenated. Payload summary (type + row_count / parts count) when content is large.

### Follow-up & context rewriting (insufficient information from current message)

**Goal:** Detect when the current user message is a **follow-up** to a previous query (e.g. “and yesterday?” after “Total Orders for today”) and rewrite it into a **standalone query** using conversation history, so the system does not treat it as insufficient information alone.

**Behavior:**

- If the user says something like “and yesterday?” or “what about last week?”, the system should **not** only classify it as needing clarification.
- Instead, it should **look up the previous user question** from history (e.g. “Total Orders for today”) and **rewrite** the current message into a full, self-contained question (e.g. “Total Orders for yesterday”).
- The rewritten query then goes through the normal flow (spelling → intent → actions → execute).

**Tasks:**

- [x] **7.1** Add a **follow-up detector**: before or after spelling correction, determine if the current message is a continuation/follow-up (e.g. fragment, pronoun, “and X?”, “what about X?”). Use heuristics and/or a small LLM call with conversation history.
- [x] **7.2** Add a **context rewriter**: when a follow-up is detected, use the **previous user question** (and optionally previous AI answer) from history to produce a **rewritten standalone query**. E.g. “and yesterday?” + previous “Total Orders for today” → “Total Orders for yesterday”. Store or pass the rewritten query into the pipeline.
- [x] **7.3** Wire the rewriter into the pipeline: after spelling correction (or as part of it), if follow-up detected → rewrite using history → use rewritten query for intent classification and all downstream steps. Ensure the rewritten query is what gets logged (e.g. in Phase 6) as the “effective” user question for that turn.
- [x] **7.4** Define how much history to use (e.g. last N user + AI pairs) and handle edge cases (e.g. first message, or no clear previous question).

### Clarification lifecycle (incomplete / complete / ignored)

**Goal:** Handle **insufficient information** by marking each user query as **complete** or **incomplete**, and support a **clarification lifecycle**: ask for missing info → user replies or ignores → mark complete, incomplete, or ignored and move on.

**Concepts:**

| State | Meaning |
|-------|--------|
| **Complete** | The query had enough information and was fully answered; or the user provided the missing information after a follow-up question, and the query was then answered. |
| **Incomplete** | The system asked for more clarification (e.g. “Which time range?”) and is waiting for the user to provide the missing information. |
| **Ignored** | The system asked for clarification, but the user’s **next message** was not a direct reply to that question (e.g. user asked something else). Treat as: user decided to ignore the follow-up → keep the original query **incomplete** and **ignored**, and process the new message as a **new** query. |

**Flow:**

1. **No clarification needed:** Answer the query → mark the query **complete**.
2. **Clarification needed:** Respond with a follow-up question (e.g. “Which date range do you mean?”) → mark the query **incomplete**.
3. **User replies with missing info:** Treat the reply as supplying the missing info (optionally combined with Phase 7 rewriter if it’s a fragment), answer the full query → mark **complete**.
4. **User ignores follow-up:** Next user message is unrelated (e.g. a new question). Do **not** treat it as the answer to the clarification. Mark the previous query **incomplete** and **ignored**, then process the new message as a **new** query (new conversation turn).

**Tasks:**

- [x] **8.1** Add **query lifecycle state** to the backend (and optionally DB): for each “conversation turn” or logical query, store whether it is **complete**, **incomplete**, or **incomplete + ignored**. Persist at least: `query_id` (or turn id), `status` (complete | incomplete | ignored), optional `pending_clarification_question` (the follow-up the system asked).
- [x] **8.2** When the system decides **CLARIFICATION_NEEDED** (or similar): return the clarification message to the user, and **mark the current query as incomplete**; store the pending question and context so the next message can be interpreted as “answer to clarification” or “new query”.
- [x] **8.3** On the **next user message**, first determine if it is a **reply to the pending clarification** (e.g. “last week”, “yesterday”) or a **new query** (e.g. “Show me top items”). Use intent + history + optional Phase 7 rewriter. If it’s a reply → merge with the incomplete query context, run the pipeline, then mark the query **complete**. If it’s a new query → mark the previous (incomplete) query **ignored**, then process the new message as a new query.
- [x] **8.4** If for a given user query there is **no** follow-up (answer was given immediately): mark that query **complete**.
- [x] **8.5** UI/API: ensure the client can send conversation history (including the last AI clarification message) so the backend can distinguish “reply to clarification” vs “new question”. Optionally surface **ignored** state in the UI (e.g. “Previous question was left unanswered”) so the user understands why the bot moved on.

---

## Error / Crash Report Logging

**Goal:** Log errors and crash reports to a **local file** (`logs/errors.jsonl`) in JSON Lines format for debugging and future cloud upload. Keep "where we log" and "how we ship" separate so cloud integration is a drop-in later.

**Current codebase:**

- **Orchestrator:** On handler exception, sets `step_error` and passes it to `log_interaction(..., error=step_error)` → `ai_logs.error_message` and debug panel.
- **Handlers:** `run_run_sql` and `run_generate_chart` **catch** SQL/execution failures and return clarification parts; they do **not** raise. So "wrong SQL" and chart failures are logged **explicitly** to the error file via `log_error(..., context={action, user_query, generated_sql}, error_kind="sql_execution_failure")`.
- **Sink:** `src/core/error_log.py` — `get_error_logger()`, `log_error()`, `JsonlRotatingFileHandler` (5 MB rotation, 3 backups). All records: `ts`, `level`, `message`, `exception`, `traceback`, `context`.
- **Shipper:** `src/core/error_shipper.py` — `upload_pending()` is a no-op; implement later to read `logs/errors*.jsonl`, batch, POST to cloud, then truncate/archive.

**Principles:** Single sink locally (one file); stable JSONL format; pluggable shipper (app never talks to cloud; shipper reads file and uploads). Pipeline/LLM errors (e.g. RUN_SQL execution failure) are logged from handlers with rich context (`action`, `user_query`, `generated_sql`, `error_kind`) so they are easy to detect and fix.

---

## LLM Response Cache

**Goal:** Avoid calling the LLM when we have a cached response for the same logical input. Reduces latency, cost, and API dependency.

**Implementation:** Central cache layer in `ai_mode/cache/` — `get_or_call(call_id, key_parts, fn)` and `get_or_call_diversity` for general chat. SQLite-backed; LRU eviction; config in `cache_config.py`.

**Cached calls (implemented):**

| Call | Key | Notes |
|------|-----|--------|
| correct_query, classify_intent | model + normalized prompt | High value |
| generate_sql | model + schema_hash + date + normalized prompt | Schema hash from `get_schema_context()` |
| generate_chart_config | model + schema_hash + normalized prompt | Config cached; SQL re-run on hit for fresh data |
| is_follow_up, rewrite_with_context, resolve_reply_to_clarification | model + message(s) | Follow-up/clarification |
| run_general_chat | model + normalized prompt | **Diversity cache:** up to 5 distinct responses per prompt; when full, randomly pick one |
| _suggest_no_data_hint | model, prompt, sql_query, valid_values_str | Optional; temperature=0 |

**Deferred / low priority:** Summary and report (and streaming variants) — data-heavy, non-deterministic (temp 0.5), low hit rate; **skip or defer**. generate_explanation — same; low priority.

**Decisions:** Schema version = hash of `get_schema_context()` (same source as generators). Persistence = SQLite. Eviction = LRU, global max entries (config). Diversity cache scope = chitchat only; if factual/system queries are added, route them elsewhere or use single-response cache.

**Regression risks (document in code):** If `classify_intent` or `run_general_chat` later use history/context in the LLM call, cache key must include it. SQL prompt should encourage relative time (`date('now')`, etc.) so cached SQL stays valid. Schema hash must be computed from `get_schema_context()` only, not a separate file read.

---

## TODO

Track tasks and future improvements here. Not required for current functionality.

### ⚠️ Observations / Risks

- **Code duplication:** Maintaining two identical copies of the schema and rules (one in Python, one in TS) is risky. If they drift, the frontend "Debug/Playground" might behave differently than the actual Python backend.  
  **Suggestion:** In the future, consider having the Python backend serve the prompt text to the frontend via an API endpoint (e.g. `/api/ai/prompts`), or just treat the Python version as the single source of truth.
- **LLM complexity:** The date logic (business day 5:00 AM–4:59:59 IST) is quite verbose in the prompts. If the LLM consistently struggles with syntax errors on the `CASE WHEN` block, consider adding a computed column `business_date` to the `orders` table (or a view) to simplify SQL generation.

### Error logging

- [ ] **Concurrency / multi-process:** If multiple workers or processes write to the same `logs/errors.jsonl`, use one log file per process (or a single dedicated logger process others send to via queue/socket) to avoid interleaved lines and fragile read-and-truncate semantics.
- [ ] **Delivery guarantees:** When implementing the shipper, use idempotency keys per record (e.g. `record_id` in JSON) so the cloud can dedupe if the same batch is sent twice. Decide at-least-once vs exactly-once and document.
- [ ] **Retention and disk:** Enforce rotation (already in place) and add retention (e.g. delete or archive local files after successful upload) so disk does not grow unbounded.
- [ ] **Security and PII:** Treat the error file as sensitive (permissions, encryption at rest if required). Consider redacting or hashing `user_query` / `generated_sql` before upload; document consent/compliance if needed.
- [ ] **Observability of the shipper:** Add metrics (e.g. last successful upload, pending line count) and alerts if upload fails or lags.

### LLM cache

- [ ] **Summary/report:** Skip or defer; low hit rate and non-deterministic. Revisit only if summary/report usage grows and caching becomes worthwhile.
- [ ] **Optional:** LRU expression index `(COALESCE(last_used_at, created_at))` if eviction becomes slow at very large sizes; TTL for date-keyed entries; DEBUG-level hit/miss counters; more stable default cache path (e.g. app data dir).

### General

- [ ] **Context sensitivity:** If `classify_intent` or `run_general_chat` ever send history/context to the LLM, update cache keys to include it and document in code.
- [ ] **Diversity cache scope:** Keep run_general_chat for chitchat; if factual/system queries are added, exclude them from the diversity cache or use a separate path.

---

## Order of implementation (suggested)

1. **Spelling correction** — Small change, clear win.
2. **Intent & action vocabulary** — Action vocabulary + planner (still single-step under the hood).
3. **New result types** — Summary (and optionally report) as a new action type.
4. **Multi-step execution** — Multi-step execution + context; then reports and UI.
5. **Pipeline metadata storage** — Debug/eval (raw query, corrected query, intent, action_sequence, SQL text, explanation; no large result payloads).
6. **Follow-up & context rewriting** — Detect “and yesterday?”-style follow-ups, rewrite using previous question from history (e.g. “Total Orders for yesterday”).
7. **Clarification lifecycle** — Mark queries complete / incomplete / ignored; ask for clarification when needed; treat next message as reply-to-clarification or new query; mark ignored when user moves on.

This gets you “one search for everything in the DB (tables, charts, summary/reports)” with a clear path to multi-step and richer reports later.

---

## Summary

- Your flow **(1) UI → (2) Python API as brain → (3) spelling → intent → action sequence → execute → result to UI** is correct.
- You already have: UI → API, intent recognition, single-step SQL + chart + general chat, and response back to UI.
- Add: **spelling correction**, **explicit action sequence (and later multi-step execution)**, **summary/report** result types, **pipeline metadata storage** (for debug, evaluation, and future step caching), **follow-up/context rewriting** (rewrite “and yesterday?” using previous question), and **clarification lifecycle** (complete / incomplete / ignored; clarification flow; ignore when user moves on). The task list above breaks that into concrete steps you can tick off.

---

## Detailed flow diagram (UI → API → functions → saving → follow-up → missing info)

Use this to trace code paths when reviewing the implementation.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  UI (Electron) — ui_electron/src/pages/AIMode.tsx                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│  • User types message; sends: POST /api/ai/chat                                  │
│    Body: { prompt, history[], last_ai_was_clarification? }                        │
│  • On response: appends user message + AI message to local history                │
│  • If response.previous_query_ignored → show "Previous question was left         │
│    unanswered"                                                                   │
│  • If response.query_status === "incomplete" → next request sends                 │
│    last_ai_was_clarification: true                                                │
│  • Renders: type text | table | chart | multi (loop over content[])               │
│  • Shows "You asked: {corrected_prompt}" when corrected_prompt is set             │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  API — src/api/routers/ai.py                                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│  POST /chat → process_chat(prompt, conn, history, last_ai_was_clarification)      │
│  (from services.ai_service → ai_mode.orchestrator.process_chat)                    │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Orchestrator — ai_mode/orchestrator.py  process_chat()                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│  1. raw_prompt = prompt                                                           │
│  2. prompt = correct_query(conn, prompt)           ← ai_mode/llm/spelling.py       │
│  3. [Reply-to-clarification] If last_ai_was_clarification && history:             │
│       clarification_text = get_last_ai_message(history)     ← ai_mode/llm/followup.py │
│       previous_user_question = get_previous_user_question(history)                │
│       is_reply, prompt = resolve_reply_to_clarification(...)  ← one LLM            │
│       handled_reply_to_clarification = True; if not is_reply: previous_query_ignored │
│  4. [Follow-up rewrite] If NOT handled_reply_to_clarification:                    │
│       prompt = resolve_follow_up(conn, prompt, history)                           │
│       (if current message is follow-up e.g. "and yesterday?" → rewrite to         │
│        standalone using previous user question)                                   │
│  5. classification = classify_intent(conn, prompt, history)  ← ai_mode/llm/intent.py   │
│  6. action_sequence = plan_actions(classification)             ← planner.py     │
│  7. context = empty_context()                                  ← context.py      │
│  8. For each action in action_sequence:                                           │
│       handler = ACTION_HANDLERS[action]  (handlers.py)                             │
│       If action == ASK_CLARIFICATION:                                             │
│         query_status_this_turn = "incomplete", pending_clarification_question set  │
│       part, context = handler(prompt, context, conn)                               │
│       parts.append(part)                                                           │
│  9. Build AIResponse (single-part or type="multi", content=parts)                  │
│ 10. Set corrected_prompt, query_status, pending_clarification_question,            │
│     previous_query_ignored on response                                              │
│ 11. log_interaction(conn, ..., raw_user_query, corrected_query, action_sequence,   │
│     explanation)                                    ← logging.py                   │
│ 12. return ai_resp                                                                │
└─────────────────────────────────────────────────────────────────────────────────┘
          │
          ├─────────────────────────────┬─────────────────────────────┐
          ▼                             ▼                             ▼
┌──────────────────────┐   ┌─────────────────────┐   ┌─────────────────────────────┐
│ ai_mode/llm/         │   │ ai_mode/llm/        │   │ handlers.py                  │
│ followup.py          │   │ intent.py            │   │                              │
├──────────────────────┤   ├─────────────────────┤   ├─────────────────────────────┤
│ get_last_ai_message  │   │ classify_intent()    │   │ run_run_sql → sql_gen, DB    │
│ get_previous_user_   │   │   INTENT_            │   │ run_generate_chart → chart    │
│   question           │   │   CLASSIFICATION_   │   │ run_generate_summary         │
│ is_follow_up         │   │   PROMPT → intent,   │   │ run_generate_report          │
│ rewrite_with_context │   │   reason             │   │ run_ask_clarification         │
│ resolve_follow_up    │   │                     │   │ run_general_chat             │
│ resolve_reply_to_    │   │                     │   │ (each returns part+context)   │
│   clarification      │   │                     │   │ context: last_table_data,     │
│ (LLM prompts in      │   │                     │   │ last_sql, last_chart_config   │
│  ai_mode/prompts/)   │   │                     │   │                              │
└──────────────────────┘   └─────────────────────┘   └─────────────────────────────┘
          │                             │                             │
          └─────────────────────────────┼─────────────────────────────┘
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Planner — ai_mode/planner.py  plan_actions(classification)                      │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Uses classification["actions"] if present, else intent_to_actions[intent]      │
│  from ai_mode/actions.py (RUN_SQL, GENERATE_CHART, ASK_CLARIFICATION, etc.)     │
└─────────────────────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Logging — ai_mode/logging.py  log_interaction()                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│  Writes to ai_logs: raw_user_query, corrected_query, intent, action_sequence     │
│  (JSON), explanation; response_payload trimmed to summary if large (e.g.        │
│  {type, row_count}). No full table/chart payloads. Returns query_id.             │
│  Schema: database/schema_sqlite.sql (single source for AI logs/feedback)        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Flow summary (key files)

| Step | What happens | Where |
|------|----------------|-------|
| UI → API | User message + history + `last_ai_was_clarification` sent to backend | `AIMode.tsx` → `routers/ai.py` |
| Spelling | Raw prompt corrected (small LLM) | `orchestrator` → `ai_mode.llm.spelling.correct_query` |
| Reply vs new query | If last AI was clarification: one LLM call decides + rewrites (clarification + previous question + current msg); else → previous ignored | `orchestrator` + `ai_mode.llm.followup.resolve_reply_to_clarification` |
| Follow-up rewrite | If message is follow-up (e.g. "and yesterday?"), rewrite to full question using history | `ai_mode.llm.followup.resolve_follow_up` → `is_follow_up`, `rewrite_with_context` |
| Intent | Classify intent (+ optional actions[]) | `ai_mode.llm.intent.classify_intent` |
| Plan | Resolve action sequence from classification | `planner.plan_actions` |
| Execute | Run each action handler with shared context; ASK_CLARIFICATION sets incomplete | `orchestrator` loop → `handlers.*` |
| Response | Single or multi-part (text / table / chart); + corrected_prompt, query_status, etc. | `orchestrator` builds `AIResponse` |
| Saving | Pipeline metadata (no large payloads) written to `ai_logs` | `logging.log_interaction` |
| UI display | Show content, "You asked:", ignored notice, multi-part list | `AIMode.tsx` |
