"""
AI Mode: main orchestrator — correct → classify → plan actions → execute sequence (Phase 3).
"""


from typing import Dict, List, AsyncGenerator, Optional
import json
import asyncio

from src.api.models import AIResponse
from ai_mode.debug_log import set_debug_log
from ai_mode.llm.spelling import correct_query
from ai_mode.llm.followup import (
    resolve_follow_up,
    get_last_ai_message,
    get_previous_user_question,
    resolve_reply_to_clarification,
)
from ai_mode.llm.intent import classify_intent
from ai_mode.planner import plan_actions
from ai_mode.actions import (
    RUN_SQL,
    GENERATE_CHART,
    GENERATE_SUMMARY,
    GENERATE_REPORT,
    ASK_CLARIFICATION,
    GENERAL_CHAT,
    OUT_OF_SCOPE,
)
from ai_mode.context import empty_context
from ai_mode.handlers import (
    run_run_sql,
    run_generate_chart,
    run_generate_summary,
    run_generate_report,
    run_ask_clarification,
    run_out_of_scope,
    run_general_chat,
    run_generate_summary_streaming,
    run_generate_report_streaming,
)
from ai_mode.logging import log_interaction


ACTION_HANDLERS = {
    RUN_SQL: run_run_sql,
    GENERATE_CHART: run_generate_chart,
    GENERATE_SUMMARY: run_generate_summary,
    GENERATE_REPORT: run_generate_report,
    ASK_CLARIFICATION: run_ask_clarification,
    OUT_OF_SCOPE: run_out_of_scope,
    GENERAL_CHAT: run_general_chat,
}

STREAMING_HANDLERS = {
    GENERATE_SUMMARY: run_generate_summary_streaming,
    GENERATE_REPORT: run_generate_report_streaming,
}


async def process_chat_stream(
    prompt: str,
    conn,
    history: List[Dict] = None,
    last_ai_was_clarification: bool = False,
    debug_log: Optional[List[dict]] = None,
) -> AsyncGenerator[str, None]:
    """
    Streaming orchestrator:
    Yields SSE events:
    - {"type": "status", "content": "..."}  (progress updates)
    - {"type": "chunk", "content": "..."}   (text tokens)
    - {"type": "part", "part": {...}}       (completed part, e.g. table/chart/text)
    - {"type": "complete", "response": {...}} (final AIResponse object)
    - {"type": "debug", "entries": [...]}  (when debug_log is provided)
    """
    raw_prompt = prompt
    if debug_log is not None:
        set_debug_log(debug_log)
        debug_log.append({
            "step": "user_query",
            "source": "user",
            "input_preview": raw_prompt[:800] + ("..." if len(raw_prompt) > 800 else ""),
            "output_preview": "",
        })

    # 1. Spelling
    yield json.dumps({"type": "status", "content": "Correcting spelling..."})
    prompt = correct_query(conn, prompt)
    
    # 2. Context / Follow-up / Reply-to-clarification
    previous_query_ignored = False
    query_status_this_turn = "complete"
    pending_clarification_question = None

    handled_reply_to_clarification = False
    if last_ai_was_clarification and history:
        clarification_text = get_last_ai_message(history)
        previous_user_question = get_previous_user_question(history)
        if clarification_text and previous_user_question:
            # yield json.dumps({"type": "status", "content": "Checking clarification..."}) 
            is_reply, prompt = resolve_reply_to_clarification(
                conn, clarification_text, previous_user_question, prompt
            )
            handled_reply_to_clarification = True
            if not is_reply:
                previous_query_ignored = True

    if not handled_reply_to_clarification:
        # yield json.dumps({"type": "status", "content": "Checking for follow-up..."})
        prompt = resolve_follow_up(conn, prompt, history)

    # 3. Intent Classification
    yield json.dumps({"type": "status", "content": "Understanding intent..."})
    classification = classify_intent(conn, prompt, history)
    intent = classification.get("intent", "GENERAL_CHAT")
    action_sequence = plan_actions(classification)
    
    # 4. Execution
    context = empty_context()
    parts: List[Dict] = []
    step_error: Optional[str] = None  # Persist for ai_logs.error_message

    for action in action_sequence:
        yield json.dumps({"type": "status", "content": f"Executing: {action}..."})
        
        # Check if we can stream this action
        if action in STREAMING_HANDLERS:
            streaming_handler = STREAMING_HANDLERS[action]
            
            # Streaming actions (Report/Summary) usually produce text content
            accumulated_text = ""
            async for chunk in streaming_handler(prompt, context, conn):
                accumulated_text += chunk
                yield json.dumps({"type": "chunk", "content": chunk})
                
            # Create the part object from accumulated text
            part = {"type": "text", "content": accumulated_text}
            
            # Add to context (requires logic similar to handlers)
            # We assume streaming handlers return text content.
            # We need to see if sql_used was returned. Currently streaming handlers in handlers.py 
            # don't return structure, just text chunks.
            # We can re-fetch context attributes if they were modified, or just minimal update.
            # *Correction*: streaming handlers don't update context in-place easily 
            # if they are just yielding strings.
            # See handlers.py: streaming handlers yield strings. 
            # We must reconstruct the 'part' and 'context' update here.
            
            # Retrieve SQL from context if it was set by _get_data_for_summary
            # But wait, _get_data_for_summary is called INSIDE the handler.
            # The streaming handler does NOT return the SQL used. 
            # This is a limitation. For now, we accept we might miss the SQL in the logs 
            # for streaming actions unless we refactor handlers to yield events too.
            # Let's assume for now logging SQL is less critical for reports than the content.
            
            # Update context for subsequent steps (if any)
            # Note: add_part helper is useful but we are outside.
            # We'll just append to context['parts'] manually or use add_part
            # imports are available.
             
            # Attempt to extract SQL if it was in the text (unlikely) or just ignore.
            sql_used = context.get("last_sql") # Might be set if previous step was RUN_SQL
            
            from ai_mode.context import add_part
            context = add_part(context, "text", accumulated_text, None, sql_used)
            parts.append(part)
            
            # Emit the completed part
            yield json.dumps({"type": "part", "part": part})
            
        else:
            # Standard synchronous handler
            handler = ACTION_HANDLERS.get(action, run_general_chat)
            try:
                if action == ASK_CLARIFICATION:
                    reason = classification.get("reason", "I need a bit more info.")
                    part, context = run_ask_clarification(prompt, context, conn, reason=reason)
                else:
                    part, context = handler(prompt, context, conn)
                
                parts.append(part)
                if part.get("clarification"):
                    query_status_this_turn = "incomplete"
                    pending_clarification_question = part["content"]
                
                # Yield the part
                yield json.dumps({"type": "part", "part": part})
                
            except Exception as e:
                step_error = f"{action}: {e}"
                try:
                    from src.core.error_log import get_error_logger
                    get_error_logger().exception(
                        f"[AI Mode] step failed: action={action} error={e}",
                        extra={"context": {"action": action, "step_error": step_error}},
                    )
                except Exception:
                    pass
                err_part = {"type": "text", "content": f"Error executing {action}: {e}"}
                parts.append(err_part)
                yield json.dumps({"type": "part", "part": err_part})
                break  # fail-fast: do not run remaining actions

    if not parts:
        part = {"type": "text", "content": "I couldn't determine what to do."}
        parts = [part]
        yield json.dumps({"type": "part", "part": part})

    # 5. Build Final Response
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

    ai_resp.corrected_prompt = prompt
    ai_resp.query_status = query_status_this_turn
    ai_resp.pending_clarification_question = pending_clarification_question
    ai_resp.previous_query_ignored = previous_query_ignored if previous_query_ignored else None
    
    # 6. Logging
    sql_for_log = None
    explanation_parts = []
    for p in parts:
        if p.get("sql_query"):
            sql_for_log = p.get("sql_query") if sql_for_log is None else sql_for_log
        if p.get("explanation"):
            explanation_parts.append(p["explanation"])
    explanation_for_log = " ".join(explanation_parts).strip() or None

    ai_resp.query_id = log_interaction(
        conn,
        prompt,
        intent,
        ai_resp,
        sql_for_log,
        error=step_error,
        raw_user_query=raw_prompt,
        corrected_query=prompt,
        action_sequence=action_sequence,
        explanation=explanation_for_log,
    )

    if debug_log is not None:
        if step_error:
            debug_log.append({
                "step": "error",
                "source": "orchestrator",
                "output_preview": step_error,
                "input_preview": "",
            })
        set_debug_log(None)
        yield json.dumps({"type": "debug", "entries": debug_log})

    yield json.dumps({"type": "complete", "response": ai_resp.dict()})


async def process_chat(
    prompt: str,
    conn,
    history: List[Dict] = None,
    last_ai_was_clarification: bool = False,
    debug_log: Optional[List[dict]] = None,
) -> AIResponse:
    """
    Sync wrapper around streaming orchestrator.
    Consumes the stream and returns the final AIResponse.
    """
    final_response_dict = None

    async for event_str in process_chat_stream(
        prompt, conn, history, last_ai_was_clarification, debug_log
    ):
        try:
            event = json.loads(event_str)
            if event["type"] == "complete":
                final_response_dict = event["response"]
        except Exception:
            pass
            
    if final_response_dict:
        # Reconstruct AIResponse from dict
        # We need to handle the dict conversion carefully if nested models exist
        # But AIResponse is flat-ish, should be fine.
        return AIResponse(**final_response_dict)
    
    # Fallback if stream didn't complete properly
    return AIResponse(
        type="text", 
        content="Internal Error: No response generated.", 
        confidence=0.0
    )

