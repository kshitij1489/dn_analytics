"""
AI Mode: intent classification via LLM.
Responses are cached by (model, normalized_prompt). History is not used in the LLM call;
if you add history to the API, the cache key must be updated (e.g. include hash of history).
"""

import json
from typing import Dict, Any, List

from ai_mode.cache import get_or_call, normalize_prompt
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.prompts.prompt_ai_mode import INTENT_CLASSIFICATION_PROMPT


def _classify_intent_impl(conn, prompt: str) -> Dict[str, Any]:
    """Call LLM to classify intent. Returns dict with intent and reason."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": INTENT_CLASSIFICATION_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"❌ Error in classify_intent: {str(e)}")
        return {"intent": "GENERAL_CHAT", "reason": f"Error: {str(e)}"}


def classify_intent(conn, prompt: str, history: List[Dict] = None) -> Dict[str, Any]:
    """
    Classify the user's intent using LLM.
    Cached by (model, normalized_prompt). Adding context/history to the LLM input requires updating the cache key.
    """
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"intent": "GENERAL_CHAT", "reason": "No API key config"}

    normalized = normalize_prompt(prompt)
    return get_or_call("classify_intent", (model, normalized), lambda: _classify_intent_impl(conn, prompt))
