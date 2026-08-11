"""
AI Mode: intent classification via LLM.
Responses are cached by (model, normalized_prompt). History is not used in the LLM call;
if you add history to the API, the cache key must be updated (e.g. include hash of history).
"""

import json
from typing import Dict, Any, List

from ai_mode.cache import get_or_call, normalize_prompt
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.llm.completion import chat_completion
from ai_mode.llm.schemas import IntentResult
from ai_mode.prompts.prompt_ai_mode import INTENT_CLASSIFICATION_PROMPT


def _classify_intent_impl(conn, prompt: str) -> Dict[str, Any]:
    """Call LLM to classify intent. Returns dict with intent and reason. Raises on LLM error (so failures are not cached)."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    response = chat_completion(
        client, "classify_intent", model,
        messages=[
            {"role": "system", "content": INTENT_CLASSIFICATION_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    # Validate shape (P1 #9): a malformed response raises here and the caller falls
    # back to GENERAL_CHAT without caching, instead of KeyError-ing downstream.
    return IntentResult.model_validate(json.loads(response.choices[0].message.content or "{}")).model_dump()


def classify_intent(conn, prompt: str, history: List[Dict] = None) -> Dict[str, Any]:
    """
    Classify the user's intent using LLM.
    Cached by (model, normalized_prompt). Adding context/history to the LLM input requires updating the cache key.
    Falls back to GENERAL_CHAT on error; the fallback is never cached.
    """
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"intent": "GENERAL_CHAT", "reason": "No API key config"}

    normalized = normalize_prompt(prompt)
    try:
        return get_or_call("classify_intent", (model, normalized), lambda: _classify_intent_impl(conn, prompt))
    except Exception as e:
        print(f"❌ Error in classify_intent: {str(e)}")
        return {"intent": "GENERAL_CHAT", "reason": f"Error: {str(e)}"}
