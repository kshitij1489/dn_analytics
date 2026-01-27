from fastapi import APIRouter, Depends, HTTPException, Body
from typing import List, Dict
import os
from src.api.models import AIQueryRequest, AIResponse, AIFeedbackRequest
from src.api.dependencies import get_db
from src.core.queries import insights_queries # For future use if needed
from services.ai_service import process_chat

router = APIRouter()

@router.post("/chat", response_model=AIResponse)
async def chat(request: AIQueryRequest, conn=Depends(get_db)):
    """
    Main endpoint for AI interaction. 
    Handles Intent Classification -> Execution -> Response.
    """
    try:
        response = await process_chat(request.prompt, conn, request.history)
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


