"""
AI Mode: follow-up detection and context rewriting (Phase 7).

Detects when the current message is a follow-up to a previous query (e.g. "and yesterday?")
and rewrites it into a standalone query using conversation history.
"""

import json
from typing import Dict, List, Any, Optional

from ai_mode.client import get_ai_client, get_ai_model
from ai_mode.prompt_ai_mode import (
    FOLLOW_UP_DETECTION_PROMPT,
    CONTEXT_REWRITE_PROMPT,
    REPLY_TO_CLARIFICATION_AND_REWRITE_PROMPT,
)

# Phase 7.4: how much history to use (last N user+ai pairs)
HISTORY_WINDOW = 10


def get_last_ai_message(history: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """
    Return the last AI message from history (e.g. the clarification question).
    history is list of {role: "user"|"ai", content: ...}.
    """
    if not history:
        return None
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg.get("role") == "ai":
            content = msg.get("content")
            if content is None:
                return None
            if isinstance(content, str):
                return content.strip() if content.strip() else None
            return str(content).strip() or None
    return None


def get_previous_user_question(history: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """
    Return the last user message from history (the previous user question).
    history is list of {role: "user"|"ai", content: ...}. content may be string or stringified.
    """
    if not history:
        return None
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg.get("role") == "user":
            content = msg.get("content")
            if content is None:
                return None
            if isinstance(content, str):
                return content.strip() if content.strip() else None
            return str(content).strip() or None
    return None


def is_follow_up(conn, current_message: str, previous_user_question: str) -> bool:
    """
    Determine if current_message is a follow-up that continues previous_user_question.
    Uses LLM with FOLLOW_UP_DETECTION_PROMPT. Returns False on error or no API.
    """
    if not current_message or not previous_user_question:
        return False
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return False
    try:
        user_content = f"""Previous user question: {previous_user_question}
Current user message: {current_message}"""
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": FOLLOW_UP_DETECTION_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=150,
            response_format={"type": "json_object"},
        )
        out = json.loads(response.choices[0].message.content or "{}")
        return out.get("is_follow_up", False)
    except Exception as e:
        print(f"⚠️ Follow-up detection failed, treating as standalone: {e}")
        return False


def rewrite_with_context(
    conn, current_message: str, previous_user_question: str
) -> str:
    """
    Rewrite the follow-up message into a standalone question using the previous user question.
    Returns the rewritten string, or current_message on error.
    """
    if not current_message or not previous_user_question:
        return current_message
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return current_message
    try:
        prompt = CONTEXT_REWRITE_PROMPT.format(
            previous_question=previous_user_question,
            current_message=current_message,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=300,
        )
        rewritten = (response.choices[0].message.content or "").strip()
        return rewritten if rewritten else current_message
    except Exception as e:
        print(f"⚠️ Context rewrite failed, using original: {e}")
        return current_message


def resolve_follow_up(
    conn, prompt: str, history: Optional[List[Dict[str, Any]]] = None
) -> str:
    """
    If the current (corrected) prompt is a follow-up to the previous user question in history,
    rewrite it into a standalone query. Otherwise return prompt unchanged.
    Phase 7.4: uses last user message from history; no history or no previous user → return prompt.
    """
    if not prompt or not prompt.strip():
        return prompt
    previous = get_previous_user_question(history)
    if not previous:
        return prompt
    if not is_follow_up(conn, prompt, previous):
        return prompt
    rewritten = rewrite_with_context(conn, prompt, previous)
    if rewritten and rewritten != prompt:
        print(f"[AI Mode] follow-up rewritten: {prompt!r} -> {rewritten!r}")
    return rewritten if rewritten else prompt


def resolve_reply_to_clarification(
    conn,
    clarification_text: str,
    previous_user_question: str,
    current_message: str,
) -> tuple[bool, str]:
    """
    Single LLM call: decide if current_message is a reply to the clarification,
    and if so rewrite previous_user_question + answer into one standalone query.
    Returns (is_reply_to_clarification, effective_query). On error returns (False, current_message).
    """
    if not current_message or not clarification_text or not previous_user_question:
        return (False, current_message or "")
    client = get_ai_client(conn)
    model = get_ai_model(conn)
    if not client:
        return (False, current_message)
    try:
        user_content = f"""Assistant asked: {clarification_text}
Previous user question: {previous_user_question}
User now said: {current_message}"""
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": REPLY_TO_CLARIFICATION_AND_REWRITE_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        out = json.loads(response.choices[0].message.content or "{}")
        is_reply = out.get("is_reply_to_clarification", False)
        rewritten = (out.get("rewritten_query") or "").strip()
        effective = rewritten if (is_reply and rewritten) else current_message
        return (is_reply, effective)
    except Exception as e:
        print(f"⚠️ Reply-to-clarification failed, treating as new query: {e}")
        return (False, current_message)
