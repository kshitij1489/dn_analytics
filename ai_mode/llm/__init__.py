"""AI Mode: LLM calls (client, schema, spelling, intent, SQL, chart, follow-up, explanation)."""

from ai_mode.llm.chart import generate_chart_config
from ai_mode.llm.client import get_ai_client, get_ai_model
from ai_mode.llm.explanation import generate_explanation
from ai_mode.llm.followup import (
    get_last_ai_message,
    get_previous_user_question,
    is_follow_up,
    resolve_follow_up,
    resolve_reply_to_clarification,
    rewrite_with_context,
)
from ai_mode.llm.intent import classify_intent
from ai_mode.llm.schema import clear_schema_cache, get_schema_context, get_schema_hash
from ai_mode.llm.spelling import correct_query
from ai_mode.llm.sql_gen import generate_sql

__all__ = [
    "classify_intent",
    "clear_schema_cache",
    "correct_query",
    "generate_chart_config",
    "generate_explanation",
    "generate_sql",
    "get_ai_client",
    "get_ai_model",
    "get_last_ai_message",
    "get_previous_user_question",
    "get_schema_context",
    "get_schema_hash",
    "is_follow_up",
    "resolve_follow_up",
    "resolve_reply_to_clarification",
    "rewrite_with_context",
]
