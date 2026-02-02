"""
AI Mode: spelling and grammar correction for the user's question (Phase 1).
Uses a single short LLM call to fix typos before intent classification.
Responses are cached by (model, normalized_prompt); see docs/LLM_CACHE_PLAN.md.
"""

from ai_mode.cache import get_or_call, normalize_prompt
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.prompts.prompt_ai_mode import SPELLING_CORRECTION_PROMPT


def _correct_query_impl(conn, prompt: str) -> str:
    """Call LLM to correct spelling/grammar. Returns corrected string or original on error."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SPELLING_CORRECTION_PROMPT},
                {"role": "user", "content": prompt.strip()},
            ],
            temperature=0,
            max_tokens=500,
        )
        corrected = (response.choices[0].message.content or "").strip()
        return corrected if corrected else prompt
    except Exception as e:
        print(f"⚠️ Spelling correction failed, using original: {e}")
        return prompt


def correct_query(conn, prompt: str) -> str:
    """
    Correct typos and obvious grammar in the user's question.
    Returns the corrected string, or the original if API is unavailable or on error.
    Cached by (model, normalized_prompt). Adding context to the LLM input requires updating the cache key.
    """
    if not prompt or not prompt.strip():
        return prompt

    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return prompt

    normalized = normalize_prompt(prompt)
    return get_or_call("correct_query", (model, normalized), lambda: _correct_query_impl(conn, prompt))
