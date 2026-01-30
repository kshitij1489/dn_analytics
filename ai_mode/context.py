"""
AI Mode: run context passed between steps in a multi-step action sequence (Phase 3).

Context accumulates results from previous actions so follow-up actions
(e.g. GENERATE_SUMMARY after RUN_SQL) can use prior data.
"""

from typing import Dict, Any, List


def empty_context() -> Dict[str, Any]:
    """Return a fresh context for a new run."""
    return {
        "parts": [],           # List of { type, content, explanation?, sql_query? }
        "last_table_data": None,   # List[Dict] from last RUN_SQL (for summary/report)
        "last_sql": None,          # str, last SQL executed
        "last_chart_config": None, # Dict from last GENERATE_CHART
        "last_explanation": None,  # str from last step
    }


def add_part(ctx: Dict[str, Any], part_type: str, content: Any,
             explanation: str = None, sql_query: str = None) -> Dict[str, Any]:
    """
    Append a result part to context and update last_* fields. Returns updated context.
    """
    part = {"type": part_type, "content": content}
    if explanation is not None:
        part["explanation"] = explanation
    if sql_query is not None:
        part["sql_query"] = sql_query

    new_ctx = ctx.copy()
    new_ctx["parts"] = ctx["parts"] + [part]
    new_ctx["last_explanation"] = explanation

    if part_type == "table":
        new_ctx["last_table_data"] = content if isinstance(content, list) else None
        new_ctx["last_sql"] = sql_query
    elif part_type == "chart":
        new_ctx["last_chart_config"] = content if isinstance(content, dict) else None
        new_ctx["last_sql"] = (content or {}).get("sql_query") if isinstance(content, dict) else sql_query
    elif part_type == "text":
        pass  # leave last_table_data / last_sql as-is for potential summary use

    return new_ctx
