"""
AI Mode: spelling and grammar correction for the user's question (Phase 1).
Uses a single short LLM call to fix typos before intent classification.
"""

from ai_mode.client import get_ai_client, get_ai_model
from ai_mode.prompt_ai_mode import SPELLING_CORRECTION_PROMPT


def correct_query(conn, prompt: str) -> str:
    """
    Correct typos and obvious grammar in the user's question.
    Returns the corrected string, or the original if API is unavailable or on error.
    """
    if not prompt or not prompt.strip():
        return prompt

    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return prompt

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
