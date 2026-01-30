"""
AI Mode: natural language explanation of query results.
"""

import pandas as pd

from ai_mode.client import get_ai_client, get_ai_model


def generate_explanation(conn, prompt: str, sql: str, df: pd.DataFrame) -> str:
    """Explain the results in simple terms."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return "Here are the results."

    summary_prompt = f"""
    The user asked: "{prompt}"
    We ran this SQL: "{sql}"
    We got {len(df)} rows of data.

    Please explain the result briefly in 1-2 bullet points. Highlight the key insight if possible (e.g. "Total revenue is X").
    """

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": summary_prompt}],
        temperature=0.5
    )
    return response.choices[0].message.content.strip()
