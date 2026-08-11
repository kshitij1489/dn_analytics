"""
AI Mode: request-scoped telemetry for cost / latency / cache-effectiveness (§4).

Collects a per-step trace for one user query — each entry records the step,
whether it was served by the LLM or the cache, the model, wall-clock latency,
and token usage. The orchestrator uses the trace to:
  - populate ai_logs aggregate columns (model, total tokens, llm_calls, cache_hits)
  - persist the per-step trace to ai_call_trace (keyed by query_id)

Always-on and independent of the debug panel. Uses a ContextVar so concurrent
requests never clobber each other (unlike debug_log's module-global fallback).
"""

from contextvars import ContextVar
from typing import Any, Dict, List, Optional

# Request-scoped trace list. Set by orchestrator at request start; read by the
# LLM completion wrapper and the cache layer.
_trace_ctx: ContextVar[Optional[List[dict]]] = ContextVar("ai_call_trace", default=None)


def start_trace() -> List[dict]:
    """Begin a fresh trace for the current request and return the backing list."""
    trace: List[dict] = []
    _trace_ctx.set(trace)
    return trace


def get_trace() -> Optional[List[dict]]:
    """Return the current request's trace list, or None if telemetry is not active."""
    return _trace_ctx.get(None)


def reset_trace() -> None:
    """Detach the trace from the current context (call after logging)."""
    _trace_ctx.set(None)


def record_llm_call(
    step: str,
    model: Optional[str],
    latency_ms: Optional[int],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
) -> None:
    """Record one LLM round-trip (a cache miss). No-op when telemetry is inactive."""
    trace = _trace_ctx.get(None)
    if trace is None:
        return
    trace.append({
        "step": step,
        "source": "llm",
        "model": model,
        "latency_ms": int(latency_ms) if latency_ms is not None else None,
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
    })


def record_cache_hit(step: str, model: Optional[str] = None) -> None:
    """Record one step served from the LLM cache. No-op when telemetry is inactive."""
    trace = _trace_ctx.get(None)
    if trace is None:
        return
    trace.append({
        "step": step,
        "source": "cache",
        "model": model,
        "latency_ms": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    })


def summarize(trace: Optional[List[dict]]) -> Dict[str, Any]:
    """
    Aggregate a trace into ai_logs columns:
      model, llm_calls, cache_hits, total_prompt_tokens, total_completion_tokens.
    model is taken from the first LLM step (None if the query was fully cached).
    """
    if not trace:
        return {
            "model": None,
            "llm_calls": 0,
            "cache_hits": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
        }
    llm_calls = sum(1 for e in trace if e.get("source") == "llm")
    cache_hits = sum(1 for e in trace if e.get("source") == "cache")
    total_prompt = sum(int(e.get("prompt_tokens") or 0) for e in trace)
    total_completion = sum(int(e.get("completion_tokens") or 0) for e in trace)
    model = None
    for e in trace:
        if e.get("model"):
            model = e["model"]
            break
    return {
        "model": model,
        "llm_calls": llm_calls,
        "cache_hits": cache_hits,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
    }
