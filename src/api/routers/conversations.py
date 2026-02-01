"""
Conversations Router - AI Conversation Persistence

Provides endpoints for managing AI conversation sessions and messages.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Any
import uuid
import json

from src.api.dependencies import get_db


router = APIRouter()


# --- Pydantic Models ---

class ConversationCreate(BaseModel):
    """Request to create a new conversation"""
    title: Optional[str] = None


class ConversationResponse(BaseModel):
    """Response model for a conversation"""
    conversation_id: str
    title: Optional[str]
    started_at: str
    updated_at: str
    synced_at: Optional[str]
    message_count: int = 0


class MessageCreate(BaseModel):
    """Request to add a message to a conversation"""
    role: str  # 'user' or 'ai'
    content: Any  # Can be string or JSON object
    type: Optional[str] = "text"  # 'text', 'table', 'chart', 'multi'
    sql_query: Optional[str] = None
    explanation: Optional[str] = None
    log_id: Optional[str] = None
    query_status: Optional[str] = None


class MessageResponse(BaseModel):
    """Response model for a message"""
    message_id: str
    conversation_id: str
    role: str
    content: Any
    type: Optional[str]
    sql_query: Optional[str]
    explanation: Optional[str]
    log_id: Optional[str]
    query_status: Optional[str]
    created_at: str


# --- Endpoints ---

@router.post("", response_model=ConversationResponse)
def create_conversation(request: ConversationCreate, conn=Depends(get_db)):
    """Create a new conversation session"""
    conversation_id = str(uuid.uuid4())
    title = request.title or f"Conversation {conversation_id[:8]}"
    
    conn.execute("""
        INSERT INTO ai_conversations (conversation_id, title)
        VALUES (?, ?)
    """, (conversation_id, title))
    conn.commit()
    
    return ConversationResponse(
        conversation_id=conversation_id,
        title=title,
        started_at="",  # Will be set by DB default
        updated_at="",
        synced_at=None,
        message_count=0
    )


@router.get("", response_model=List[ConversationResponse])
def list_conversations(limit: int = 20, offset: int = 0, conn=Depends(get_db)):
    """List recent conversations with message counts"""
    cursor = conn.execute("""
        SELECT 
            c.conversation_id,
            c.title,
            c.started_at,
            c.updated_at,
            c.synced_at,
            COUNT(m.message_id) as message_count
        FROM ai_conversations c
        LEFT JOIN ai_messages m ON c.conversation_id = m.conversation_id
        GROUP BY c.conversation_id
        ORDER BY c.updated_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    
    rows = cursor.fetchall()
    return [
        ConversationResponse(
            conversation_id=row["conversation_id"],
            title=row["title"],
            started_at=row["started_at"] or "",
            updated_at=row["updated_at"] or "",
            synced_at=row["synced_at"],
            message_count=row["message_count"]
        )
        for row in rows
    ]


@router.get("/{conversation_id}", response_model=List[MessageResponse])
def get_conversation_messages(conversation_id: str, conn=Depends(get_db)):
    """Get all messages for a conversation"""
    # Verify conversation exists
    cursor = conn.execute(
        "SELECT conversation_id FROM ai_conversations WHERE conversation_id = ?",
        (conversation_id,)
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    cursor = conn.execute("""
        SELECT 
            message_id, conversation_id, role, content, type,
            sql_query, explanation, log_id, query_status, created_at
        FROM ai_messages
        WHERE conversation_id = ?
        ORDER BY created_at ASC
    """, (conversation_id,))
    
    rows = cursor.fetchall()
    messages = []
    for row in rows:
        # Parse content if it's JSON
        content = row["content"]
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass  # Keep as string
        
        messages.append(MessageResponse(
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=content,
            type=row["type"],
            sql_query=row["sql_query"],
            explanation=row["explanation"],
            log_id=row["log_id"],
            query_status=row["query_status"],
            created_at=row["created_at"] or ""
        ))
    
    return messages


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
def add_message(conversation_id: str, request: MessageCreate, conn=Depends(get_db)):
    """Add a message to a conversation"""
    # Verify conversation exists
    cursor = conn.execute(
        "SELECT conversation_id FROM ai_conversations WHERE conversation_id = ?",
        (conversation_id,)
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    message_id = str(uuid.uuid4())
    
    # Serialize content to JSON if not a string
    content_str = request.content if isinstance(request.content, str) else json.dumps(request.content)
    
    conn.execute("""
        INSERT INTO ai_messages (
            message_id, conversation_id, role, content, type,
            sql_query, explanation, log_id, query_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        message_id, conversation_id, request.role, content_str, request.type,
        request.sql_query, request.explanation, request.log_id, request.query_status
    ))
    
    # Update conversation's updated_at
    conn.execute("""
        UPDATE ai_conversations SET updated_at = CURRENT_TIMESTAMP
        WHERE conversation_id = ?
    """, (conversation_id,))
    
    conn.commit()
    
    return MessageResponse(
        message_id=message_id,
        conversation_id=conversation_id,
        role=request.role,
        content=request.content,
        type=request.type,
        sql_query=request.sql_query,
        explanation=request.explanation,
        log_id=request.log_id,
        query_status=request.query_status,
        created_at=""
    )


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, conn=Depends(get_db)):
    """Delete a conversation and all its messages"""
    cursor = conn.execute(
        "SELECT conversation_id FROM ai_conversations WHERE conversation_id = ?",
        (conversation_id,)
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Messages will be deleted via CASCADE
    conn.execute(
        "DELETE FROM ai_conversations WHERE conversation_id = ?",
        (conversation_id,)
    )
    conn.commit()
    
    return {"status": "deleted", "conversation_id": conversation_id}


@router.delete("/{conversation_id}/messages/{message_id}")
def delete_message(conversation_id: str, message_id: str, conn=Depends(get_db)):
    """Delete a specific message from a conversation"""
    cursor = conn.execute(
        "SELECT message_id FROM ai_messages WHERE message_id = ? AND conversation_id = ?",
        (message_id, conversation_id)
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Message not found")
    
    conn.execute(
        "DELETE FROM ai_messages WHERE message_id = ?",
        (message_id,)
    )
    
    # Update conversation's updated_at
    conn.execute("""
        UPDATE ai_conversations SET updated_at = CURRENT_TIMESTAMP
        WHERE conversation_id = ?
    """, (conversation_id,))
    
    conn.commit()
    
    return {"status": "deleted", "message_id": message_id}
