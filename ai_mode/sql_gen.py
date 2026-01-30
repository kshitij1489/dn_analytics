"""
AI Mode: natural language to SQL generation and execution.
"""

from datetime import datetime

from ai_mode.client import get_ai_client, get_ai_model
from ai_mode.prompt_ai_mode import SQL_GENERATION_PROMPT
from ai_mode.schema import get_schema_context


def generate_sql(conn, prompt: str) -> str:
    """Generate SQL from natural language. Raises ValueError if API not configured."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        raise ValueError("API Key not configured. Please add an OpenAI API Key in Configuration.")

    schema = get_schema_context()
    today = datetime.now().strftime('%Y-%m-%d')

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SQL_GENERATION_PROMPT.format(schema=schema, today=today)},
            {"role": "user", "content": prompt}
        ],
        temperature=0
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
