"""
AI Mode: action vocabulary and intent-to-actions mapping (Phase 2).

Actions are the executable steps the orchestrator can run.
Intents from the classifier map to one or more actions (single-step for now).
"""

from typing import List

# Action identifiers used by the planner and orchestrator.
RUN_SQL = "RUN_SQL"
GENERATE_CHART = "GENERATE_CHART"
GENERATE_SUMMARY = "GENERATE_SUMMARY"   # Phase 4
GENERATE_REPORT = "GENERATE_REPORT"     # Phase 4
GENERAL_CHAT = "GENERAL_CHAT"
ASK_CLARIFICATION = "ASK_CLARIFICATION"
OUT_OF_SCOPE = "OUT_OF_SCOPE"

ALL_ACTIONS = frozenset({
    RUN_SQL,
    GENERATE_CHART,
    GENERATE_SUMMARY,
    GENERATE_REPORT,
    GENERAL_CHAT,
    ASK_CLARIFICATION,
    OUT_OF_SCOPE,
})


def intent_to_actions(intent: str) -> List[str]:
    """
    Map classifier intent to an ordered list of actions.
    Single action per intent for now; Phase 3 will support multi-step sequences.
    """
    mapping = {
        "SQL_QUERY": [RUN_SQL],
        "CHART_REQUEST": [GENERATE_CHART],
        "SUMMARY_REQUEST": [GENERATE_SUMMARY],
        "REPORT_REQUEST": [GENERATE_REPORT],
        "CLARIFICATION_NEEDED": [ASK_CLARIFICATION],
        "GENERAL_CHAT": [GENERAL_CHAT],
        "OUT_OF_SCOPE": [OUT_OF_SCOPE],
    }
    return mapping.get(intent, [GENERAL_CHAT])
