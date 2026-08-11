from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from typing import List, Dict, AsyncGenerator, Optional
import json
from src.api.models import AIQueryRequest, AIResponse, AIFeedbackRequest, CacheEntryPatchRequest
from src.api.dependencies import get_db
from src.core.queries import insights_queries # For future use if needed
from ai_mode.orchestrator import process_chat, process_chat_stream
from ai_mode.logging import fetch_debug_entries
from ai_mode.cache import (
    clear_cache as llm_clear_cache,
    list_entries as llm_list_entries,
    set_incorrect as llm_set_incorrect,
    get_cache_counters as llm_cache_counters,
)

router = APIRouter()


@router.post("/chat", response_model=AIResponse)
async def chat(request: AIQueryRequest, conn=Depends(get_db)):
    """
    Main endpoint for AI interaction.
    Handles Intent Classification -> Execution -> Response.
    """
    local_debug_log: List[dict] = []

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


@router.post("/chat/stream")
async def chat_stream(request: AIQueryRequest, conn=Depends(get_db)):
    """
    Streaming endpoint for AI chat. Returns Server-Sent Events (SSE).
    Best for long reports/summaries that benefit from progressive rendering.
    """

    async def generate() -> AsyncGenerator[str, None]:
        local_debug_log: List[dict] = []

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


@router.get("/prompt-context")
def get_prompt_context():
    """
    SQL Console "LLM Prompt" tab: the SQL-generation prompt to paste into an external LLM.
    Single source of truth — same schema + rules as AI Mode, generated on demand from
    ai_mode.prompts (no hand-maintained copy). Relative dates use SQLite localtime because
    the pasted text has no server to inject business-day literals.
    """
    try:
        from ai_mode.llm.sql_gen import build_console_sql_prompt
        return {"prompt": build_console_sql_prompt()}
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error building prompt context: {e}", extra={"context": {"endpoint": "/api/ai/prompt-context"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


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


@router.get("/debug/cache-entries")
def get_llm_cache_entries(limit: int = 500):
    """
    Return LLM cache entries for telemetry (key_hash, call_id, value_preview, created_at, last_used_at, is_incorrect).
    """
    try:
        return {"entries": llm_list_entries(limit=limit)}
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error listing LLM cache: {e}", extra={"context": {"endpoint": "/api/ai/debug/cache-entries"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/debug/cache-entries/{key_hash}")
def patch_llm_cache_entry(key_hash: str, body: CacheEntryPatchRequest):
    """
    Update a cache entry (e.g. set is_incorrect for human feedback).
    """
    try:
        updated = llm_set_incorrect(key_hash, body.is_incorrect)
        if not updated:
            raise HTTPException(status_code=404, detail="Cache entry not found")
        return {"status": "ok", "key_hash": key_hash, "is_incorrect": body.is_incorrect}
    except HTTPException:
        raise
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error updating cache entry: {e}", extra={"context": {"endpoint": "/api/ai/debug/cache-entries"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/logs")
def get_debug_logs(query_id: Optional[str] = None, conn=Depends(get_db)):
    """
    Return debug log entries for a chat request (user question, cache hit/miss, and
    LLM/cache response per step) for the AI Mode Debug panel.
    §3.7: reads from persisted ai_debug_log keyed by query_id (race-free) instead of
    the old cross-request in-memory global. Without query_id, returns the most-recent
    query's steps.
    """
    return {"entries": fetch_debug_entries(conn, query_id)}


@router.get("/debug/trace/{query_id}")
def get_call_trace(query_id: str, conn=Depends(get_db)):
    """
    §4 telemetry: per-step trace for one query (step, source cache/llm, model,
    latency_ms, prompt/completion tokens). Answers per-step latency/cost questions.
    """
    try:
        cursor = conn.execute(
            """
            SELECT step, source, model, latency_ms, prompt_tokens, completion_tokens, created_at
            FROM ai_call_trace
            WHERE query_id = ?
            ORDER BY trace_id ASC
            """,
            (query_id,),
        )
        return {"query_id": query_id, "trace": [dict(row) for row in cursor.fetchall()]}
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error reading call trace: {e}", extra={"context": {"endpoint": "/api/ai/debug/trace"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/cache-counters")
def get_cache_hit_counters():
    """
    §4 telemetry: global LLM cache hit/miss counters per call_id (cache effectiveness
    over time). Each entry: {call_id, hits, misses}.
    """
    try:
        return {"counters": llm_cache_counters()}
    except Exception as e:
        try:
            from src.core.error_log import get_error_logger
            get_error_logger().error(f"Error reading cache counters: {e}", extra={"context": {"endpoint": "/api/ai/debug/cache-counters"}})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


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

