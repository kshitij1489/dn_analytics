"""
AI Mode: pydantic models for LLM structured (JSON) outputs (P1 #9).

Every LLM call that uses response_format={"type":"json_object"} — intent,
follow-up detection, reply-to-clarification, chart config — parses through one of
these models. A malformed / off-shape response then raises a clean ValidationError
that the caller's error handling turns into a graceful fallback, instead of a
KeyError/TypeError surfacing mid-stream. Models are lenient (extra keys ignored,
sensible defaults) so a merely-incomplete response still degrades usefully.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class IntentResult(BaseModel):
    """INTENT_CLASSIFICATION_PROMPT output: {"intent": ..., "reason": ...}."""
    model_config = ConfigDict(extra="ignore")

    intent: str = "GENERAL_CHAT"
    reason: Optional[str] = ""


class FollowUpResult(BaseModel):
    """FOLLOW_UP_DETECTION_PROMPT output: {"is_follow_up": bool}."""
    model_config = ConfigDict(extra="ignore")

    is_follow_up: bool = False


class ReplyToClarificationResult(BaseModel):
    """REPLY_TO_CLARIFICATION_AND_REWRITE_PROMPT output."""
    model_config = ConfigDict(extra="ignore")

    is_reply_to_clarification: bool = False
    rewritten_query: Optional[str] = ""


class ChartConfigResult(BaseModel):
    """CHART_GENERATION_PROMPT output: chart spec + the SQL that feeds it."""
    model_config = ConfigDict(extra="ignore")

    chart_type: Optional[str] = "bar"
    x_key: Optional[str] = "label"
    y_key: Optional[str] = "value"
    title: Optional[str] = "Chart"
    sql: Optional[str] = ""
