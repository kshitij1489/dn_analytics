from fastapi import APIRouter, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from typing import List, Dict, AsyncGenerator
import os
import json
from src.api.models import AIQueryRequest, AIResponse, AIFeedbackRequest
from src.api.dependencies import get_db
from src.core.queries import insights_queries # For future use if needed
from services.ai_service import process_chat
from ai_mode.handlers import run_generate_report_streaming, run_generate_summary_streaming

router = APIRouter()

@router.post("/chat", response_model=AIResponse)
async def chat(request: AIQueryRequest, conn=Depends(get_db)):
    """
    Main endpoint for AI interaction. 
    Handles Intent Classification -> Execution -> Response.
    """
    try:
        response = await process_chat(
            request.prompt,
            conn,
            request.history,
            last_ai_was_clarification=request.last_ai_was_clarification or False,
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(request: AIQueryRequest, conn=Depends(get_db)):
    """
    Streaming endpoint for AI chat. Returns Server-Sent Events (SSE).
    Best for long reports/summaries that benefit from progressive rendering.
    """
    async def generate() -> AsyncGenerator[str, None]:
        try:
            # For streaming, we identify report/summary requests and stream them
            # Otherwise fall back to non-streaming response
            prompt = request.prompt.lower()
            
            if "report" in prompt:
                async for chunk in run_generate_report_streaming(request.prompt, conn):
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
            elif "summary" in prompt or "summarize" in prompt:
                async for chunk in run_generate_summary_streaming(request.prompt, conn):
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
            else:
                # Non-streaming fallback: run normal chat and return as single event
                response = await process_chat(
                    request.prompt,
                    conn,
                    request.history,
                    last_ai_was_clarification=request.last_ai_was_clarification or False,
                )
                yield f"data: {json.dumps({'type': 'complete', 'content': response.content, 'response': response.dict()})}\n\n"
            
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )


@router.post("/feedback")
def submit_feedback(feedback: AIFeedbackRequest, conn=Depends(get_db)):
    """Save user feedback for a query result"""
    try:
        # SQLite uses :name binding for dicts
        query = """
            INSERT INTO ai_feedback (log_id, is_positive, comment, created_at)
            VALUES (:log_id, :is_positive, :comment, datetime('now'))
        """
        conn.execute(query, {
            "log_id": feedback.log_id,
            "is_positive": feedback.is_positive,
            "comment": feedback.comment
        })
        conn.commit()
        return {"status": "recorded"}
    except Exception as e:
        print(f"❌ Error saving feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save feedback")

