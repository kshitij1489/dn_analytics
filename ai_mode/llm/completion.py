"""
AI Mode: single choke point for OpenAI chat completions.

Wraps client.chat.completions.create so every non-streaming LLM call is timed
and its model + token usage (already carried on the response's `usage`, previously
discarded) is recorded into the request telemetry trace. Streaming handlers can't
use this (usage arrives on the final chunk) and record telemetry themselves.
"""

import time

from ai_mode.telemetry import record_llm_call


def chat_completion(client, step: str, model: str, **kwargs):
    """
    Run client.chat.completions.create(model=model, **kwargs), record telemetry
    (step, model, latency, prompt/completion tokens), and return the raw response.
    Telemetry recording never affects the returned value or raised exceptions.
    """
    started = time.perf_counter()
    response = client.chat.completions.create(model=model, **kwargs)
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        usage = getattr(response, "usage", None)
        record_llm_call(
            step,
            model,
            latency_ms,
            getattr(usage, "prompt_tokens", 0) if usage else 0,
            getattr(usage, "completion_tokens", 0) if usage else 0,
        )
    except Exception:
        pass
    return response
