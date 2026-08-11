"""
AI Mode prompts: intent routing, SQL generation, and chart generation.
"""

# Phase 1: spelling/grammar correction (small, fast pass before intent).
SPELLING_CORRECTION_PROMPT = """You are a spelling and grammar corrector. Your only job is to fix typos and obvious grammar in the user's question. Preserve the exact meaning and intent. Return ONLY the corrected question, nothing else. Do not add explanations, quotes, or preamble. If the text is already correct, return it unchanged.

Do NOT change casing or hyphenation for domain-specific terms: order types (e.g. Dine In, Takeaway), order sources (Swiggy, Zomato, POS, Home Website), or similar. Keep the user's exact wording for such terms (e.g. leave "Dine In" as-is, do not change to "dine-in")."""

# --- Intent classification (slim prompt: list + app role only) ---
INTENT_CLASSIFICATION_PROMPT = """Restaurant analytics chat: users ask about orders, revenue, menu items, and trends. Classify intent. We only answer questions related to our system.

INTENTS: SQL_QUERY (data/numbers/lists), CHART_REQUEST (graph/visual), SUMMARY_REQUEST (short summary), REPORT_REQUEST (longer report), GENERAL_CHAT (greetings/off-topic within our app), CLARIFICATION_NEEDED (too vague), OUT_OF_SCOPE (unrelated to our system — e.g. general knowledge like "capital of England", weather, other topics we don't have data for).

Return JSON only: {"intent": "<one of above>", "reason": "<brief>"}."""

# No-data hint: LLM suggests a short, smart hint using the cached valid values (for future: replace with cached list).
NO_DATA_HINT_PROMPT = """Restaurant analytics: the user's query returned no data. You are given their question, the SQL that ran, and the valid values for filter columns (from our system).

Your job: suggest ONE short, helpful hint or follow-up. Examples:
- If they used a wrong value (e.g. "Dine-in" vs "Dine In"): "Did you mean 'Dine In'? Try: Average order value for Dine In orders."
- If valid values are listed: briefly mention the relevant one and a sample rephrase.
- If no valid values given: suggest they ask e.g. "What order types do we have?"

Return ONLY the suggestion (one sentence). No preamble, no quotes around the whole thing."""

# --- Shared rule blocks -------------------------------------------------------
# Single source of truth for rules common to SQL_GENERATION_PROMPT and
# CHART_GENERATION_PROMPT. Placeholders ({schema}, {business_today}, {today_start},
# {today_end}, {yesterday_start}, {yesterday_end}) are filled at call time via
# .format() on the composed prompt — do NOT turn these into f-strings.

_SHARED_CONTEXT_BLOCK = """## Context
- The database stores restaurant orders from PetPooja (a POS system).
- It tracks Customers, Menu Items, Orders, and Order Items.
- We have a specific focus on "Menu Clustering" (normalizing raw item names to clean menu items).
- All monetary amounts are in INR.
- The restaurant operates in IST (Asia/Kolkata timezone).

## Database Schema
{schema}
"""

_SHARED_COLUMN_MAPPINGS_BLOCK = """## CRITICAL COLUMN MAPPINGS (use these EXACT names):
| Concept | Use This Column | DO NOT USE |
|---------|-----------------|------------|
| Order Revenue | `orders.total` | 'amount', 'revenue', 'value' |
| Order Date | `orders.created_on` | `occurred_at` (has invalid values!) |
| Order ID | `orders.order_id` | `id` (does not exist) |
| Item Revenue | `order_items.total_price` | |
| Item Name (raw) | `order_items.name_raw` | |
| Menu Item ID | `menu_items.menu_item_id` | `id` (does not exist) |
| Menu Item Name | `menu_items.name` | |
| Category/Type | `menu_items.type` | |
| Order Type | `orders.order_type` | Use EXACT casing: e.g. 'Dine In', 'Takeaway' (not 'Dine-in') |
| Order Source | `orders.order_from` | Values: 'Swiggy', 'Zomato', 'POS', 'Home Website' |
| Order Item ID | `order_items.order_item_id` | |
| Addon → Order Item | `order_item_addons.order_item_id` | ❌ `order_item_addons.order_id` DOES NOT EXIST. Join addons via `oi.order_item_id = oia.order_item_id`. |
"""

_SHARED_DATE_RULES_BLOCK = """## DATE & BUSINESS-DAY RULES:
- Dates are stored as TEXT 'YYYY-MM-DD HH:MM:SS' in IST. Use `date(orders.created_on)` to extract date, `strftime('%H', orders.created_on)` for hour.
- NEVER use `occurred_at` - it contains invalid data.
- NEVER use `created_at` - this is the system insertion time (technical metadata). ALWAYS use `created_on` - the actual Timestamp of Order Placement (Business Date).
- Business day (IST): A day runs from 5:00 AM to 4:59:59 AM next day IST. Use the precomputed boundaries below (do not use SQLite 'now' or 'localtime' for today/yesterday — they depend on server timezone).
- Relative Date Logic — use these exact literals (IST, precomputed):
  - **today**: `orders.created_on >= '{today_start}' AND orders.created_on <= '{today_end}'`
  - **yesterday**: `orders.created_on >= '{yesterday_start}' AND orders.created_on <= '{yesterday_end}'`
  - **last X days** (X full days including today): use B = '{business_today}'. Filter: `orders.created_on >= (date('{business_today}', '-(X-1) days') || ' 05:00:00') AND orders.created_on < (date('{business_today}', '+1 day') || ' 05:00:00')`. E.g. last 7 days: start date('{business_today}', '-6 days').
  - **this month**: use SQLite date modifiers only. First day = `date('{business_today}', 'start of month')`. End (exclusive) = `date('{business_today}', 'start of month', '+1 month')`. Filter: `orders.created_on >= (date('{business_today}', 'start of month') || ' 05:00:00') AND orders.created_on < (date('{business_today}', 'start of month', '+1 month') || ' 05:00:00')`. Do NOT use dayofmonth(), DAY(), MONTH() — those do not exist in SQLite.
  - **this week** (Monday–Sunday business days): start = `date('{business_today}', '-6 days', 'weekday 1')` (Monday of the current week). Filter: `orders.created_on >= (date('{business_today}', '-6 days', 'weekday 1') || ' 05:00:00') AND orders.created_on < (date('{business_today}', '+1 day') || ' 05:00:00')`.
  - **last week**: `orders.created_on >= (date('{business_today}', '-13 days', 'weekday 1') || ' 05:00:00') AND orders.created_on < (date('{business_today}', '-6 days', 'weekday 1') || ' 05:00:00')`.
  - **last month**: `orders.created_on >= (date('{business_today}', 'start of month', '-1 month') || ' 05:00:00') AND orders.created_on < (date('{business_today}', 'start of month') || ' 05:00:00')`.
  - **this year**: `orders.created_on >= (date('{business_today}', 'start of year') || ' 05:00:00') AND orders.created_on < (date('{business_today}', 'start of year', '+1 year') || ' 05:00:00')`.
  - **a named month (e.g. "in June")**: use its most recent occurrence relative to '{business_today}' (previous year if that month has not started yet this year). Write the first day as a literal 'YYYY-MM-01'. Filter: `orders.created_on >= 'YYYY-MM-01 05:00:00' AND orders.created_on < (date('YYYY-MM-01', '+1 month') || ' 05:00:00')`.
  - **this quarter**: quarter start months are Jan/Apr/Jul/Oct. Build the quarter's first day 'YYYY-MM-01' from '{business_today}'. Filter: `orders.created_on >= 'YYYY-MM-01 05:00:00' AND orders.created_on < (date('YYYY-MM-01', '+3 months') || ' 05:00:00')`. **last quarter**: same pattern, previous quarter's first day.
  All relative-date windows use the 5 AM business-day boundary (`|| ' 05:00:00'`) with an exclusive upper bound.
"""

_SHARED_QUERY_RULES_BLOCK = """## QUERY RULES:
- ALWAYS filter by `orders.order_status = 'Success'` unless specified otherwise.
- When filtering by Item Name, ALWAYS join 'order_items' with 'menu_items' and filter on 'menu_items.name'. NEVER filter on 'order_items.name_raw'.
- `order_items` and `order_item_addons` link to `menu_items` via `menu_item_id`.
- To calculate "Total Sold" or "Revenue" for an item (which can be sold as a main item OR an add-on):
  - ✅ USE `UNION ALL` to combine results from `order_items` and `order_item_addons`.
  - ❌ DO NOT JOIN `order_items` directly to `order_item_addons`. This causes row explosion.
  - ⚠️ EACH subquery in the UNION must JOIN to `orders` independently if you need to filter by order date/status.
  - Example Pattern (order_item_addons links to order_items via order_item_id, NOT order_id):
    ```
    SELECT menu_item_id, SUM(qty) as total_sold, SUM(rev) as total_revenue FROM (
        SELECT oi.menu_item_id, oi.quantity as qty, oi.total_price as rev
        FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
        WHERE o.order_status = 'Success' AND o.created_on >= (date('{business_today}', '-89 days') || ' 05:00:00')
        UNION ALL
        SELECT oia.menu_item_id, oia.quantity as qty, oia.price * oia.quantity as rev
        FROM orders o JOIN order_items oi ON o.order_id = oi.order_id
        JOIN order_item_addons oia ON oi.order_item_id = oia.order_item_id
        WHERE o.order_status = 'Success' AND o.created_on >= (date('{business_today}', '-89 days') || ' 05:00:00')
    ) combined GROUP BY menu_item_id
    ```
"""

_SHARED_BUSINESS_DEFINITIONS_BLOCK = """## STANDARD BUSINESS DEFINITIONS:
- **Repeat Customer**: A customer who has placed > 1 successful order in their LIFETIME.
  - When matching "Repeat Customers in last 90 days", find customers active in last 90 days, then check their LIFETIME order count (total_orders > 1). Do NOT limit the "repeat" check to just the last 90 days.
- **Item Repeat Rate**: The % of unique customers who bought a specific item more than once in their LIFETIME.
  - Formula: (Count of Customers who bought Item X > 1 time ever) / (Total Unique Customers who bought Item X).
"""

_SHARED_COMMON_MISTAKES_BLOCK = """## COMMON MISTAKES TO AVOID:
- ❌ JOINing on `id` (e.g. `orders.id`, `menu_items.id`). THESE COLUMNS DO NOT EXIST.
- ✅ ALWAYS use explicit IDs: `orders.order_id`, `menu_items.menu_item_id`.
- ❌ Using `amount` or `revenue` columns.
- ✅ ALWAYS use `orders.total` or `order_items.total_price`.
- ❌ Using `orders.total` when grouping by Item or Category - THIS DUPLICATES REVENUE!
- ✅ ALWAYS use `order_items.total_price` OR `menu_items_summary_view.total_revenue` for Item/Category analysis; ONLY use `orders.total` when grouping by Order-level attributes (Date, Source, Order Type).
- ❌ Using Postgres/MySQL functions like `ILIKE`, `TIMESTAMPTZ`, `gen_random_uuid`, `dayofmonth()`, `DAY()`, `MONTH()`, `YEAR()` (date parts). Use SQLite only: `date(...)`, `strftime('%d', ...)` for day, and date modifiers like `'start of month'`, `'+1 month'`, `'-7 days'`.
- ❌ Filtering on `order_items.name_raw`. ALWAYS join with `menu_items` and use `menu_items.name`.
- ❌ Using `order_item_addons.total_price`. THIS COLUMN DOES NOT EXIST. Use `order_item_addons.price * order_item_addons.quantity` for add-on revenue.
- ❌ Joining `order_item_addons` on `order_id`. The table has `order_item_id` (FK to order_items). Use `JOIN order_item_addons oia ON oi.order_item_id = oia.order_item_id`.
"""

# --- Composed prompts ----------------------------------------------------------

SQL_GENERATION_PROMPT = (
    "\nYou are a SQLite expert for a restaurant analytics system.\n\n"
    + _SHARED_CONTEXT_BLOCK
    + """
## STRATEGY:
- For Menu Items Analysis (Revenue/Sales by Item), prefer using `menu_items_summary_view` as it has pre-calculated `total_revenue`, `total_sold` etc.
- For detailed Order Analysis, join `orders` and `order_items`.

"""
    + _SHARED_COLUMN_MAPPINGS_BLOCK
    + """
## OUTPUT RULES:
1. Return ONLY the SQL query. No markdown, no explanation, no backticks.
2. Use standard SQLite syntax (TEXT for UUIDs/Dates).
3. Limit results to 100 rows unless specified otherwise.

"""
    + _SHARED_DATE_RULES_BLOCK
    + "\n"
    + _SHARED_QUERY_RULES_BLOCK
    + "\n"
    + _SHARED_BUSINESS_DEFINITIONS_BLOCK
    + "\n"
    + _SHARED_COMMON_MISTAKES_BLOCK
    + """
## WHEN YOU CANNOT ANSWER:
If the question cannot be answered with our schema or data (e.g. we don't have that metric, dimension, or table), respond with exactly one line: CANNOT_ANSWER: <brief message to the user>. Otherwise return only the SQL.
"""
)

# Localtime variant of the date rules for the SQL Console "LLM Prompt" tab.
# Same structure as _SHARED_DATE_RULES_BLOCK, but relative dates are computed at
# query time from datetime('now', 'localtime') (assumed IST) instead of literals
# injected by the server — the console text is copied verbatim into an external
# LLM, so there is no server call to fill today/yesterday boundaries.
_CONSOLE_DATE_RULES_BLOCK = """## DATE & BUSINESS-DAY RULES:
- Dates are stored as TEXT 'YYYY-MM-DD HH:MM:SS' in IST. Use `date(orders.created_on)` to extract date, `strftime('%H', orders.created_on)` for hour.
- NEVER use `occurred_at` - it contains invalid data.
- NEVER use `created_at` - this is the system insertion time (technical metadata). ALWAYS use `created_on` - the actual Timestamp of Order Placement (Business Date).
- Business day (IST): A day runs from 5:00 AM to 4:59:59 AM next day IST. Assume the machine running the query is in IST; compute the current time with `datetime('now', 'localtime')`.
- Relative Date Logic (business day). First compute B = the current business date, then substitute the resulting 'YYYY-MM-DD' date into every filter below:
  - **B (current business date)**: `B = CASE WHEN strftime('%H', 'now', 'localtime') < '05' THEN date('now', 'localtime', '-1 day') ELSE date('now', 'localtime') END`.
  - **today**: `orders.created_on >= (B || ' 05:00:00') AND orders.created_on < (date(B, '+1 day') || ' 05:00:00')`
  - **yesterday**: `orders.created_on >= (date(B, '-1 day') || ' 05:00:00') AND orders.created_on < (B || ' 05:00:00')`
  - **last X days** (X full days including today): `orders.created_on >= (date(B, '-(X-1) days') || ' 05:00:00') AND orders.created_on < (date(B, '+1 day') || ' 05:00:00')`. E.g. last 7 days: start date(B, '-6 days').
  - **this month**: use SQLite date modifiers only. `orders.created_on >= (date(B, 'start of month') || ' 05:00:00') AND orders.created_on < (date(B, 'start of month', '+1 month') || ' 05:00:00')`. Do NOT use dayofmonth(), DAY(), MONTH() — those do not exist in SQLite.
  - **this week** (Monday–Sunday business days): `orders.created_on >= (date(B, '-6 days', 'weekday 1') || ' 05:00:00') AND orders.created_on < (date(B, '+1 day') || ' 05:00:00')`.
  - **last week**: `orders.created_on >= (date(B, '-13 days', 'weekday 1') || ' 05:00:00') AND orders.created_on < (date(B, '-6 days', 'weekday 1') || ' 05:00:00')`.
  - **last month**: `orders.created_on >= (date(B, 'start of month', '-1 month') || ' 05:00:00') AND orders.created_on < (date(B, 'start of month') || ' 05:00:00')`.
  - **this year**: `orders.created_on >= (date(B, 'start of year') || ' 05:00:00') AND orders.created_on < (date(B, 'start of year', '+1 year') || ' 05:00:00')`.
  - **a named month (e.g. "in June")**: use its most recent occurrence relative to B (previous year if that month has not started yet this year). Write the first day as a literal 'YYYY-MM-01'. `orders.created_on >= 'YYYY-MM-01 05:00:00' AND orders.created_on < (date('YYYY-MM-01', '+1 month') || ' 05:00:00')`.
  - **this quarter**: quarter start months are Jan/Apr/Jul/Oct. Build the quarter's first day 'YYYY-MM-01' from B. `orders.created_on >= 'YYYY-MM-01 05:00:00' AND orders.created_on < (date('YYYY-MM-01', '+3 months') || ' 05:00:00')`. **last quarter**: same pattern, previous quarter's first day.
  All relative-date windows use the 5 AM business-day boundary (`|| ' 05:00:00'`) with an exclusive upper bound. Because B is derived from `datetime('now', 'localtime')`, every window shifts automatically each day — this prompt never needs manual date edits.
"""

# SQL Console "LLM Prompt" tab. Reuses SQL_GENERATION_PROMPT verbatim except the
# date-rules block is swapped for the localtime variant. Composing by replacement
# keeps the console prompt in lock-step with AI Mode's schema, column mappings,
# query rules and anti-patterns (single source of truth); only the date logic
# diverges, which it must. Rendered via build_console_sql_prompt() in ai_mode.llm.sql_gen.
SQL_CONSOLE_PROMPT = SQL_GENERATION_PROMPT.replace(
    _SHARED_DATE_RULES_BLOCK, _CONSOLE_DATE_RULES_BLOCK
)

CHART_GENERATION_PROMPT = (
    "\nYou are a SQLite expert and data visualization specialist for a restaurant analytics system.\n\n"
    + _SHARED_CONTEXT_BLOCK
    + """
## STRATEGY:
- For Menu Items Analysis, use `menu_items_summary_view` (cols: `total_revenue`, `total_sold`, `sold_as_item`, `sold_as_addon`).

"""
    + _SHARED_COLUMN_MAPPINGS_BLOCK
    + """
## YOUR TASK:
Generate a chart configuration with SQL query for the user's visualization request.

## CHART RULES:
1. Limit results to 20 rows for charts unless specified otherwise.
2. Always alias result columns to simple names like "label" and "value".
3. For JOINs: order_items.menu_item_id = menu_items.menu_item_id (NOT menu_items.id!)

"""
    + _SHARED_DATE_RULES_BLOCK
    + "\n"
    + _SHARED_QUERY_RULES_BLOCK
    + "\n"
    + _SHARED_BUSINESS_DEFINITIONS_BLOCK
    + "\n"
    + _SHARED_COMMON_MISTAKES_BLOCK
    + """
## OUTPUT FORMAT (JSON only, no markdown):
{{
    "chart_type": "bar" | "line" | "pie",
    "sql": "SELECT ... AS label, ... AS value FROM ...",
    "x_key": "label",
    "y_key": "value",
    "title": "Human readable chart title"
}}

## CHART TYPE GUIDELINES:
- Use "bar" for comparisons (e.g., revenue by category, orders by source)
- Use "pie" for proportional data (e.g., % share by category)
- Use "line" for time series (e.g., daily revenue trend)
"""
)

# Phase 4: summary and report generation.
SUMMARY_GENERATION_PROMPT = """You are a restaurant analytics assistant. The user asked a question and we have run a query. Below is the data (as JSON rows). Write a short, clear summary (2–4 sentences) that answers the user's question and highlights the main numbers or insights. Use plain language. Do not repeat raw column names; use friendly terms (e.g. "revenue" not "total"). If there is a single key number (e.g. total revenue), lead with it."""

REPORT_GENERATION_PROMPT = """You are a restaurant analytics assistant writing a brief report. The user asked for a report. We have already run queries and may have tables, charts, or prior summaries in context. Write a short narrative report (a few paragraphs) that:
1. Answers the user's question clearly.
2. Weaves in the key numbers and insights from the data provided.
3. Uses plain language and avoids jargon.
Do not invent numbers; only use data that was provided. If no data was provided, say so and suggest what the user could ask for."""

# Phase 7: follow-up detection and context rewriting.
FOLLOW_UP_DETECTION_PROMPT = """You are a conversation analyst for a restaurant analytics chat. Given the previous user question and the current user message, decide if the current message is a FOLLOW-UP that continues or varies the previous question (e.g. "and yesterday?", "what about last week?", "same for last month").

Return ONLY a JSON object:
{"is_follow_up": true or false, "reason": "brief explanation"}

If the current message is a complete standalone question (e.g. "Show me top items"), return is_follow_up: false. If it is a fragment or continuation (e.g. "and yesterday?", "how about last week?"), return is_follow_up: true."""

CONTEXT_REWRITE_PROMPT = """You are a query rewriter for a restaurant analytics chat. The user previously asked a full question, and now sent a short follow-up. Your job is to combine them into ONE standalone question that could be answered without context.

Previous user question: {previous_question}
Current follow-up message: {current_message}

Return ONLY the single rewritten question. No explanation, no quotes, no preamble. Example: if previous was "Total orders for today" and current is "and yesterday?", return "Total orders for yesterday"."""

# Phase 8: reply-to-clarification — single call: decide + rewrite (clarification text + previous question + current message).
REPLY_TO_CLARIFICATION_AND_REWRITE_PROMPT = """You are a conversation analyst for a restaurant analytics chat. The assistant had just asked a clarification question. The user now sent a message.

Your job:
1. Decide if the user is DIRECTLY ANSWERING that clarification (e.g. "yesterday", "last week") or asking something NEW (e.g. "Show me top items").
2. If answering: merge the previous user question with the user's answer into ONE standalone question (e.g. previous "Total orders for which day?" + answer "yesterday" → "Total orders for yesterday"). Use the clarification question to understand what was missing.
3. If new query: the rewritten_query is the current message as-is.

Return ONLY a JSON object:
{"is_reply_to_clarification": true or false, "rewritten_query": "single standalone question or current message"}

Always include rewritten_query. When true it is the merged question; when false it is the user's current message."""
