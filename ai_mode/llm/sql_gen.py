"""
AI Mode: natural language to SQL generation and execution.
Responses are cached by (model, schema_hash, business_date, normalized_prompt).
Today/yesterday use IST business-day boundaries injected from Python so SQL is correct regardless of server timezone.
"""

from datetime import datetime, timedelta

from ai_mode.cache import get_or_call, normalize_prompt
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.llm.schema import get_schema_context, get_schema_hash
from ai_mode.prompts.prompt_ai_mode import SQL_GENERATION_PROMPT
from src.core.utils.business_date import get_current_business_date, get_business_date_range


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


def _generate_sql_impl(conn, prompt: str) -> str:
    """Call LLM to generate SQL. Raises ValueError if API not configured or CANNOT_ANSWER."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        raise ValueError("API Key not configured. Please add an OpenAI API Key in Configuration.")

    schema = get_schema_context()
    date_ctx = _business_date_context()

    response = client.chat.completions.create(
        model=model,
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

    schema_hash = get_schema_hash()
    business_today = get_current_business_date()
    normalized = normalize_prompt(prompt)
    return get_or_call(
        "generate_sql",
        (model, schema_hash, business_today, normalized),
        lambda: _generate_sql_impl(conn, prompt),
    )
