"""
AI Mode: OpenAI client and model configuration from DB.
"""

import os

import openai

# §3.4: bound every LLM call so a slow/hung provider cannot stall the pipeline.
# - timeout is per-request. For streams it is httpx's between-chunks read timeout,
#   not a cumulative cap, so long report/summary streams are not cut off.
# - max_retries uses the SDK's exponential backoff + jitter, retrying 408/409/429
#   and >=500 plus connection errors, honoring Retry-After.
# Both apply to streaming and non-streaming calls (all sites build via get_ai_client).
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 2


def _llm_timeout() -> float:
    try:
        return float(os.environ.get("AI_LLM_TIMEOUT", _DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT


def _llm_max_retries() -> int:
    try:
        return int(os.environ.get("AI_LLM_MAX_RETRIES", _DEFAULT_MAX_RETRIES))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RETRIES


def get_ai_client(conn):
    """Fetch API Key from DB or specific ENV fallback."""
    try:
        from src.core.db.control import resolve_config_values

        api_key = resolve_config_values(conn, ("openai_api_key",)).get("openai_api_key")
        if api_key:
            return openai.OpenAI(
                api_key=api_key,
                timeout=_llm_timeout(),
                max_retries=_llm_max_retries(),
            )
    except Exception as e:
        print(f"Error fetching API key from DB: {e}")
    return None


def get_ai_model(conn):
    """Fetch Model Name from DB or default to gpt-5-mini."""
    try:
        from src.core.db.control import resolve_config_values

        model = resolve_config_values(conn, ("openai_model",)).get("openai_model")
        if model:
            return model
    except Exception as e:
        print(f"Error fetching AI model from DB: {e}")
    return "gpt-5-mini"
