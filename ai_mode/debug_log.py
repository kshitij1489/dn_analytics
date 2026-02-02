"""
AI Mode: request-scoped debug log for a chat.
Records user question, then for each LLM/cache step: step name, cache hit/miss, output preview.
Used to verify caching and LLM prompts during testing.
"""

import json
from contextvars import ContextVar
from typing import Any, List, Optional

# Request-scoped list of debug entries. Set by orchestrator; read by cache/LLM layers.
_debug_log_ctx: ContextVar[Optional[List[dict]]] = ContextVar(
    "ai_debug_log", default=None
)

# Max length for input/output previews in log entries
PREVIEW_MAX = 800


def preview_value(value: Any) -> str:
    """Produce a short string preview of a value for debug display."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:PREVIEW_MAX] + ("..." if len(value) > PREVIEW_MAX else "")
    try:
        s = json.dumps(value, default=str)
        return s[:PREVIEW_MAX] + ("..." if len(s) > PREVIEW_MAX else "")
    except Exception:
        return str(value)[:PREVIEW_MAX]


def set_debug_log(log: Optional[List[dict]]) -> None:
    """Set the current request's debug log list. Pass None to disable logging."""
    _debug_log_ctx.set(log)


def get_debug_log() -> Optional[List[dict]]:
    """Return the current request's debug log list, or None if not set."""
    return _debug_log_ctx.get(None)


def append_entry(
    step: str,
    source: str,
    output_preview: str = "",
    input_preview: str = "",
) -> None:
    """
    Append one debug entry. Called from cache (get_or_call, get, set) and chart.
    step: e.g. "correct_query", "classify_intent", "generate_chart_config"
    source: "cache" or "llm"
    output_preview: short preview of the response (truncated)
    input_preview: optional short preview of the input (e.g. prompt)
    """
    log = get_debug_log()
    if log is None:
        return
    log.append({
        "step": step,
        "source": source,
        "input_preview": preview_value(input_preview) if input_preview else "",
        "output_preview": preview_value(output_preview),
    })
