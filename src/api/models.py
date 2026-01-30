"""
Pydantic Models for API Request/Response Schemas

This module consolidates all Pydantic models used across routers.
"""

from pydantic import BaseModel
from typing import Optional, Any, Dict


# --- Job Management Models ---

class JobResponse(BaseModel):
    """Response model for async job status"""
    job_id: str
    status: str
    message: str
    progress: float
    stats: Optional[Dict[str, Any]] = None


# --- SQL Console Models ---

class QueryRequest(BaseModel):
    """Request model for SQL query execution"""
    query: str


# --- Menu Management Models ---

class MergeRequest(BaseModel):
    """Request to merge two menu items"""
    source_id: str
    target_id: str


class UndoMergeRequest(BaseModel):
    """Request to undo a menu item merge"""
    merge_id: int


class RemapRequest(BaseModel):
    """Request to remap an order item to different menu item/variant"""
    order_item_id: str
    new_menu_item_id: str
    new_variant_id: str


class VerifyRequest(BaseModel):
    """Request to verify a menu item, optionally renaming it"""
    menu_item_id: str
    new_name: Optional[str] = None
    new_type: Optional[str] = None


# --- Resolutions Models ---

class ResolutionMergeRequest(BaseModel):
    """Request to merge menu items via resolutions endpoint"""
    menu_item_id: str
    target_menu_item_id: str


class RenameRequest(BaseModel):
    """Request to rename a menu item"""
    menu_item_id: str
    new_name: str
    new_type: str



# --- AI Mode Models ---

class AIQueryRequest(BaseModel):
    """Request model for AI natural language query"""
    prompt: str
    history: Optional[list] = None   # List of {"role": "user"|"ai", "content": ...}
    last_ai_was_clarification: Optional[bool] = None  # Phase 8: True if last AI message was a clarification question


class AIResponse(BaseModel):
    """Unified response model for all AI interactions"""
    type: str  # 'text', 'table', 'chart', 'multi'
    content: Any
    explanation: Optional[str] = None
    sql_query: Optional[str] = None
    log_id: Optional[str] = None
    confidence: float = 1.0
    corrected_prompt: Optional[str] = None  # Phase 5.1: spelling-corrected question (when shown in UI)
    query_status: Optional[str] = None  # Phase 8: "complete" | "incomplete" | "ignored"
    pending_clarification_question: Optional[str] = None  # Phase 8: when query_status=incomplete, the question we asked
    previous_query_ignored: Optional[bool] = None  # Phase 8: True when user sent a new query after we asked for clarification


class AIFeedbackRequest(BaseModel):
    """Request query for user feedback"""
    log_id: str
    is_positive: bool
    comment: Optional[str] = None
