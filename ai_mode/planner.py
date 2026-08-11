"""
AI Mode: action planner — turns classifier output into an ordered list of actions (Phase 2).
"""

from typing import Dict, Any, List

from ai_mode.actions import intent_to_actions


def plan_actions(classification: Dict[str, Any]) -> List[str]:
    """
    Return an ordered list of action identifiers derived from the classifier "intent".

    Note: INTENT_CLASSIFICATION_PROMPT only returns {"intent", "reason"}, so there is
    no LLM-supplied "actions" list to honor. Multi-step sequences are produced by
    intent_to_actions. If the classifier is ever extended to emit an explicit
    "actions" list, validate it against ALL_ACTIONS here before returning it.
    """
    intent = classification.get("intent", "GENERAL_CHAT")
    return intent_to_actions(intent)
