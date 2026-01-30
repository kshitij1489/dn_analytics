"""
AI Mode: main orchestrator — correct → classify → plan actions → execute sequence (Phase 3).
"""

from typing import Dict, List

from src.api.models import AIResponse
from ai_mode.spelling import correct_query
from ai_mode.followup import (
    resolve_follow_up,
    get_last_ai_message,
    get_previous_user_question,
    is_reply_to_clarification,
    rewrite_with_context,
)
from ai_mode.intent import classify_intent
from ai_mode.planner import plan_actions
from ai_mode.actions import (
    RUN_SQL,
    GENERATE_CHART,
    GENERATE_SUMMARY,
    GENERATE_REPORT,
    ASK_CLARIFICATION,
    GENERAL_CHAT,
)
from ai_mode.context import empty_context
from ai_mode.handlers import (
    run_run_sql,
    run_generate_chart,
    run_generate_summary,
    run_generate_report,
    run_ask_clarification,
    run_general_chat,
)
from ai_mode.logging import log_interaction

ACTION_HANDLERS = {
    RUN_SQL: run_run_sql,
    GENERATE_CHART: run_generate_chart,
    GENERATE_SUMMARY: run_generate_summary,
    GENERATE_REPORT: run_generate_report,
    ASK_CLARIFICATION: run_ask_clarification,
    GENERAL_CHAT: run_general_chat,
}


async def process_chat(
    prompt: str,
    conn,
    history: List[Dict] = None,
    last_ai_was_clarification: bool = False,
) -> AIResponse:
    """
    Main orchestrator: correct → (Phase 8: reply-to-clarification vs new query) →
    Phase 7 follow-up rewrite → classify intent → plan actions → execute → response.
    """
    raw_prompt = prompt
    prompt = correct_query(conn, prompt)
    previous_query_ignored = False
    query_status_this_turn = "complete"
    pending_clarification_question = None

    # Phase 8: if last AI was a clarification, decide reply vs new query
    if last_ai_was_clarification and history:
        clarification_text = get_last_ai_message(history)
        previous_user_question = get_previous_user_question(history)
        if clarification_text and previous_user_question:
            if is_reply_to_clarification(conn, prompt, clarification_text):
                prompt = rewrite_with_context(conn, prompt, previous_user_question)
                if prompt:
                    print(f"[AI Mode] reply-to-clarification merged: {prompt!r}")
            else:
                previous_query_ignored = True
                print(f"[AI Mode] new query after clarification (previous ignored)")

    # Phase 7: if current message is a follow-up (e.g. "and yesterday?"), rewrite using previous question from history
    prompt = resolve_follow_up(conn, prompt, history)

    classification = classify_intent(conn, prompt, history)
    intent = classification.get("intent", "GENERAL_CHAT")
    action_sequence = plan_actions(classification)

    # Phase 5.3: trace logging for debugging
    print(f"[AI Mode] intent={intent} actions={action_sequence}")

    context = empty_context()
    parts: List[Dict] = []

    for action in action_sequence:
        handler = ACTION_HANDLERS.get(action, run_general_chat)
        try:
            if action == ASK_CLARIFICATION:
                reason = classification.get("reason", "I need a bit more info.")
                part, context = run_ask_clarification(
                    prompt, context, conn,
                    reason=reason,
                )
                query_status_this_turn = "incomplete"
                pending_clarification_question = reason
            else:
                part, context = handler(prompt, context, conn)
            parts.append(part)
        except Exception as e:
            print(f"[AI Mode] step failed: action={action} error={e}")
            raise

    if not parts:
        part = {"type": "text", "content": "I couldn't determine what to do."}
        parts = [part]

    # Build final response: single-part (backward compatible) or multi-part
    if len(parts) == 1:
        p = parts[0]
        ai_resp = AIResponse(
            type=p["type"],
            content=p["content"],
            explanation=p.get("explanation"),
            sql_query=p.get("sql_query"),
            confidence=1.0,
        )
    else:
        ai_resp = AIResponse(
            type="multi",
            content=parts,
            confidence=1.0,
        )

    # Phase 5.1: expose corrected question for UI (what we actually used)
    ai_resp.corrected_prompt = prompt
    # Phase 8: query lifecycle state
    ai_resp.query_status = query_status_this_turn
    ai_resp.pending_clarification_question = pending_clarification_question
    ai_resp.previous_query_ignored = previous_query_ignored if previous_query_ignored else None

    # Phase 6: collect SQL and explanation for logging (no large result data)
    sql_for_log = None
    explanation_parts = []
    for p in parts:
        if p.get("sql_query"):
            sql_for_log = p.get("sql_query") if sql_for_log is None else sql_for_log
        if p.get("explanation"):
            explanation_parts.append(p["explanation"])
    explanation_for_log = " ".join(explanation_parts).strip() or None

    ai_resp.log_id = log_interaction(
        conn,
        prompt,
        intent,
        ai_resp,
        sql_for_log,
        raw_user_query=raw_prompt,
        corrected_query=prompt,
        action_sequence=action_sequence,
        explanation=explanation_for_log,
    )
    return ai_resp
