from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from typing import List, Dict, AsyncGenerator
import os
import json
from src.api.models import AIQueryRequest, AIResponse, AIFeedbackRequest
from src.api.dependencies import get_db
from src.core.queries import insights_queries # For future use if needed
from ai_mode.orchestrator import process_chat, process_chat_stream
from ai_mode.cache import clear_cache as llm_clear_cache
from ai_mode import debug_log as ai_debug_log

router = APIRouter()

# Last request's debug log entries; populated when debug is enabled for /chat or /chat/stream
_last_debug_entries: List[dict] = []


@router.post("/chat", response_model=AIResponse)
async def chat(request: AIQueryRequest, conn=Depends(get_db)):
    """
    Main endpoint for AI interaction.
    Handles Intent Classification -> Execution -> Response.
    """
    local_debug_log: List[dict] = []
    global _last_debug_entries
    _last_debug_entries = local_debug_log
    ai_debug_log.set_current_request_log(local_debug_log)

    try:
        response = await process_chat(
            request.prompt,
            conn,
            request.history,
            request.last_ai_was_clarification or False,
            debug_log=local_debug_log,
        )
        return response
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().exception("AI /chat failed", extra={"context": {"endpoint": "/api/ai/chat"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        ai_debug_log.set_current_request_log(None)


@router.post("/chat/stream")
async def chat_stream(request: AIQueryRequest, conn=Depends(get_db)):
    """
    Streaming endpoint for AI chat. Returns Server-Sent Events (SSE).
    Best for long reports/summaries that benefit from progressive rendering.
    """

    async def generate() -> AsyncGenerator[str, None]:
        local_debug_log: List[dict] = []
        global _last_debug_entries
        _last_debug_entries = local_debug_log
        ai_debug_log.set_current_request_log(local_debug_log)

        try:
            async for event_str in process_chat_stream(
                request.prompt,
                conn,
                request.history,
                request.last_ai_was_clarification or False,
                debug_log=local_debug_log,
            ):
                yield f"data: {event_str}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            try:
                from src.core.error_log import get_error_logger
                get_error_logger().exception("AI /chat/stream failed", extra={"context": {"endpoint": "/api/ai/chat/stream"}})
            except Exception:
                pass
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            ai_debug_log.set_current_request_log(None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@router.get("/suggestions")
def get_suggestions(limit: int = 10, conn=Depends(get_db)):
    """Get popular/recent queries for suggestions."""
    try:
        cursor = conn.execute("""
            SELECT user_query, COUNT(*) as freq, MAX(created_at) as last_used
            FROM ai_logs
            WHERE user_query IS NOT NULL AND user_query != ''
            GROUP BY user_query
            ORDER BY freq DESC, last_used DESC
            LIMIT ?
        """, (limit,))
        return [{"query": row["user_query"], "frequency": row["freq"]} for row in cursor.fetchall()]
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error getting suggestions: {e}", extra={"context": {"endpoint": "/api/ai/suggestions"}})
        except Exception:
            pass
        return []


@router.post("/feedback")
def submit_feedback(feedback: AIFeedbackRequest, conn=Depends(get_db)):
    """Save user feedback for a query result"""
    try:
        # SQLite uses :name binding for dicts
        query = """
            INSERT INTO ai_feedback (query_id, is_positive, comment, created_at)
            VALUES (:query_id, :is_positive, :comment, datetime('now'))
        """
        conn.execute(query, {
            "query_id": feedback.query_id,
            "is_positive": feedback.is_positive,
            "comment": feedback.comment
        })
        conn.commit()
        return {"status": "recorded"}
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().exception("Error saving feedback", extra={"context": {"endpoint": "/api/ai/feedback"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to save feedback")


@router.get("/debug/logs")
def get_debug_logs():
    """
    Return debug log entries for the last chat request.
    Used by the AI Mode Debug panel to show user question, cache hit/miss, and LLM/cache responses.
    """
    return {"entries": _last_debug_entries}


@router.post("/debug/clear-cache")
def clear_llm_cache():
    """
    Clear the LLM response cache (all entries).
    Use after prompt/schema changes so the next requests hit the LLM with updated logic.
    """
    try:
        llm_clear_cache(None)
        return {"status": "ok", "message": "LLM cache cleared."}
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error clearing LLM cache: {e}", extra={"context": {"endpoint": "/api/ai/debug/clear-cache"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/debug/init")
def init_debug_logs():
    """
    Initialize the AI debug logs directory.
    Creates 'ai_mode/ai_debug_logs' if it doesn't exist.
    """
    try:
        log_dir = os.path.join(os.getcwd(), "ai_mode", "ai_debug_logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            print(f"🐞 Created debug log directory: {log_dir}")
        return {"status": "ok", "path": log_dir}
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error initializing debug logs: {e}", extra={"context": {"endpoint": "/api/ai/debug/init"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

