"""
AI Mode: intent classification via LLM.
"""

import json
from typing import Dict, Any, List

from ai_mode.client import get_ai_client, get_ai_model
from ai_mode.prompt_ai_mode import SYSTEM_ROUTER_PROMPT


def classify_intent(conn, prompt: str, history: List[Dict] = None) -> Dict[str, Any]:
    """Classify the user's intent using LLM."""
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return {"intent": "GENERAL_CHAT", "reason": "No API key config"}

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_ROUTER_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"❌ Error in classify_intent: {str(e)}")
        return {"intent": "GENERAL_CHAT", "reason": f"Error: {str(e)}"}
