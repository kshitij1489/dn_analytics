"""
AI Mode: natural language to SQL generation and execution.
Responses are cached by (model, schema_hash, business_date, normalized_prompt).
Today/yesterday use IST business-day boundaries injected from Python so SQL is correct regardless of server timezone.
"""

import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from ai_mode.cache import get_or_call, normalize_prompt
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.llm.completion import chat_completion
from ai_mode.llm.schema import get_schema_context, get_schema_hash
from ai_mode.prompts.prompt_ai_mode import SQL_CONSOLE_PROMPT, SQL_GENERATION_PROMPT
from src.core.utils.business_date import get_current_business_date, get_business_date_range


def ensure_read_only_sql(sql_query: str) -> None:
    """
    Guard for LLM-generated SQL: only SELECT/WITH statements may run.
    Raises ValueError otherwise (hallucinated or prompt-injected UPDATE/DELETE/DROP must not execute).
    The human SQL console (/api/sql/query) deliberately allows writes; the AI path must not.
    """
    first_token = (sql_query or "").strip().split(None, 1)
    token = first_token[0].upper().rstrip("(") if first_token else ""
    if token not in ("SELECT", "WITH"):
        raise ValueError(
            "The generated query was not a read-only SELECT and was blocked. Please rephrase your question."
        )


def _main_db_path(conn) -> Optional[str]:
    """File path of conn's 'main' database, or None for in-memory/unknown DBs."""
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            name = row["name"] if isinstance(row, sqlite3.Row) else row[1]
            if name == "main":
                path = row["file"] if isinstance(row, sqlite3.Row) else row[2]
                return path or None
    except Exception:
        return None
    return None


def read_sql_readonly(conn, sql_query: str) -> pd.DataFrame:
    """
    Execute an (already SELECT/WITH-validated) LLM-generated query on a read-only
    connection to conn's DB file: SQLite URI ``mode=ro`` + ``PRAGMA query_only=1``.
    Defense in depth (§3.5) — even if ensure_read_only_sql were bypassed, the engine
    itself rejects any write. Falls back to conn for in-memory DBs (tests) where a
    separate mode=ro handle cannot attach to the same database.
    """
    path = _main_db_path(conn)
    if not path or path == ":memory:":
        return pd.read_sql_query(sql_query, conn)
    ro = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, check_same_thread=False, timeout=30.0
    )
    try:
        ro.row_factory = sqlite3.Row
        ro.execute("PRAGMA query_only = 1;")
        return pd.read_sql_query(sql_query, ro)
    finally:
        ro.close()


def _business_date_context():
    """IST business-day boundaries for today and yesterday. Used so SQL does not rely on SQLite localtime."""
    business_today = get_current_business_date()
    today_start, today_end = get_business_date_range(business_today)
    # Yesterday = (business_today - 1 day) as date string
    b_today_dt = datetime.strptime(business_today, "%Y-%m-%d").date()
    business_yesterday = (b_today_dt - timedelta(days=1)).isoformat()
    yesterday_start, yesterday_end = get_business_date_range(business_yesterday)
    return {
        "business_today": business_today,
        "business_yesterday": business_yesterday,
        "today_start": today_start,
        "today_end": today_end,
        "yesterday_start": yesterday_start,
        "yesterday_end": yesterday_end,
    }


def build_console_sql_prompt() -> str:
    """
    Render the SQL-generation prompt shown in the SQL Console "LLM Prompt" tab.

    Same schema + rules as AI Mode's SQL generation (single source of truth via
    SQL_CONSOLE_PROMPT), but relative-date logic uses SQLite datetime('now', 'localtime')
    instead of server-injected literals: the text is copied verbatim into an external
    LLM, so it must compute business-day windows at query time (machine assumed IST).
    No DB or API key needed — the schema comes from the same source as AI Mode.
    """
    schema = get_schema_context()
    prompt = SQL_CONSOLE_PROMPT.format(schema=schema, business_today="now")
    # The shared UNION-ALL example embeds date('{business_today}', '-89 days'); make it
    # explicitly localtime so the example matches the localtime date rules above it.
    return prompt.replace("date('now', '-89 days')", "date('now', '-89 days', 'localtime')")


def _generate_sql_impl(conn, prompt: str) -> str:
    """Call LLM to generate SQL. Raises ValueError if API not configured or CANNOT_ANSWER."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        raise ValueError("API Key not configured. Please add an OpenAI API Key in Configuration.")

    schema = get_schema_context(conn)
    date_ctx = _business_date_context()

    response = chat_completion(
        client, "generate_sql", model,
        messages=[
            {"role": "system", "content": SQL_GENERATION_PROMPT.format(schema=schema, **date_ctx)},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    if raw.upper().startswith("CANNOT_ANSWER:"):
        msg = raw.split(":", 1)[1].strip()
        raise ValueError(msg or "We don't have data to answer that question.")
    if raw.startswith("```sql"):
        raw = raw.replace("```sql", "", 1).replace("```", "", 1)
    elif raw.startswith("```"):
        raw = raw.replace("```", "", 1).replace("```", "", 1)
    return raw.strip()


def generate_sql(conn, prompt: str) -> str:
    """Generate SQL from natural language. Raises ValueError if API not configured or CANNOT_ANSWER."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        raise ValueError("API Key not configured. Please add an OpenAI API Key in Configuration.")

    schema_hash = get_schema_hash(conn)
    business_today = get_current_business_date()
    normalized = normalize_prompt(prompt)
    return get_or_call(
        "generate_sql",
        (model, schema_hash, business_today, normalized),
        lambda: _generate_sql_impl(conn, prompt),
    )
