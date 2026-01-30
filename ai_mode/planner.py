"""
AI Mode: action planner — turns classifier output into an ordered list of actions (Phase 2).
"""

from typing import Dict, Any, List

from ai_mode.actions import intent_to_actions, ALL_ACTIONS


def plan_actions(classification: Dict[str, Any]) -> List[str]:
    """
    Return an ordered list of action identifiers from the classifier output.
    Uses "actions" from the LLM if present and valid; otherwise derives from "intent".
    """
    raw_actions = classification.get("actions")
    if isinstance(raw_actions, list) and len(raw_actions) > 0:
        # Validate and filter to known actions only
        valid = [a for a in raw_actions if a in ALL_ACTIONS]
        if valid:
            return valid
    intent = classification.get("intent", "GENERAL_CHAT")
    return intent_to_actions(intent)
