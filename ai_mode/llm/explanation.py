"""
AI Mode: natural language explanation of query results.
Cached by (model, normalized_prompt, sql, row_count) so repeat questions whose
SQL and result shape are unchanged do not cost an LLM round-trip.
"""

import pandas as pd

from ai_mode.cache import get_or_call, normalize_prompt
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.llm.completion import chat_completion


def _generate_explanation_impl(conn, prompt: str, sql: str, df: pd.DataFrame) -> str:
    """Call LLM to explain results. Raises on LLM error (so failures are not cached)."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)

    summary_prompt = f"""
    The user asked: "{prompt}"
    We ran this SQL: "{sql}"
    We got {len(df)} rows of data.

    Please explain the result briefly in 1-2 bullet points. Highlight the key insight if possible (e.g. "Total revenue is X").
    """

    response = chat_completion(
        client, "generate_explanation", model,
        messages=[{"role": "user", "content": summary_prompt}],
        temperature=0.5
    )
    return response.choices[0].message.content.strip()


def generate_explanation(conn, prompt: str, sql: str, df: pd.DataFrame) -> str:
    """Explain the results in simple terms. Falls back to a generic line on error (never cached)."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return "Here are the results."

    normalized = normalize_prompt(prompt)
    try:
        return get_or_call(
            "generate_explanation",
            (model, normalized, sql, len(df)),
            lambda: _generate_explanation_impl(conn, prompt, sql, df),
        )
    except Exception as e:
        print(f"⚠️ Explanation generation failed, using generic line: {e}")
        return "Here are the results."
