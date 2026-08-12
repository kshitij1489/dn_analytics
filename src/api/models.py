"""
Pydantic Models for API Request/Response Schemas

This module consolidates all Pydantic models used across routers.
"""

from pydantic import BaseModel
from typing import Optional, Any, Dict, List


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


class VariantMappingRequest(BaseModel):
    """Variant mapping used when merging a source item into a target item."""
    source_variant_id: str
    target_variant_id: Optional[str] = None
    new_variant_name: Optional[str] = None


# --- Menu Management Models ---

class GlobalMenuPreviewReference(BaseModel):
    """Opaque server preview fields; ignored by legacy restaurant mode."""
    global_mutation_id: Optional[str] = None
    global_preview_digest: Optional[str] = None
    global_menu_group_id: Optional[str] = None
    global_preview_revision: Optional[int] = None
    global_coverage_complete: Optional[bool] = None
    global_conflicts: Optional[List[Dict[str, Any]]] = None
    global_mutation_type: Optional[str] = None
    global_mutation_payload: Optional[Dict[str, Any]] = None


class GlobalMenuMutationPreviewRequest(BaseModel):
    mutation_type: str
    payload: Dict[str, Any]
    mutation_id: Optional[str] = None


class GlobalMenuLocalMutationPreviewRequest(BaseModel):
    mutation_type: str
    source_local_menu_item_id: Optional[str] = None
    source_local_variant_id: Optional[str] = None
    target_local_menu_item_id: Optional[str] = None
    target_local_variant_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    mutation_id: Optional[str] = None


class GlobalMenuMutationCommitRequest(GlobalMenuPreviewReference):
    pass


class GlobalMenuResolutionContextRequest(BaseModel):
    local_menu_item_id: str
    local_variant_id: Optional[str] = None


class MergeRequest(GlobalMenuPreviewReference):
    """Request to merge two menu items"""
    source_id: str
    target_id: str
    variant_mappings: Optional[List[VariantMappingRequest]] = None


class UndoMergeRequest(GlobalMenuPreviewReference):
    """Request to undo a menu item merge"""
    merge_id: int


class RetypeMenuItemRequest(GlobalMenuPreviewReference):
    """Request to change a menu item's type, keeping its name"""
    menu_item_id: str
    new_type: str


class RemapRequest(GlobalMenuPreviewReference):
    """Request to remap an order item to different menu item/variant"""
    order_item_id: str
    new_menu_item_id: str
    new_variant_id: str


class CreateVariantTypeRequest(GlobalMenuPreviewReference):
    """Request to create a new variant type from the Variants tab."""
    variant_name: str
    description: Optional[str] = None
    unit: Optional[str] = None
    value: Optional[float] = None


class UpdateVariantMappingRequest(GlobalMenuPreviewReference):
    """Request to update an existing menu item + variant mapping to a new variant."""
    menu_item_id: str
    current_variant_id: str
    new_variant_id: str


class VerifyRequest(GlobalMenuPreviewReference):
    """Request to verify a menu item, optionally renaming it"""
    menu_item_id: str
    new_name: Optional[str] = None
    new_type: Optional[str] = None
    new_variant_id: Optional[str] = None


class ResolveVariantRequest(GlobalMenuPreviewReference):
    """Resolve a single unresolved menu item + variant pair."""
    source_menu_item_id: str
    source_variant_id: str
    target_menu_item_id: Optional[str] = None
    new_name: Optional[str] = None
    new_type: Optional[str] = None
    target_variant_id: Optional[str] = None
    new_variant_name: Optional[str] = None


class VerifyAssignmentRequest(BaseModel):
    """Verify exact assignment keys without changing their menu identities."""
    assignment_order_item_ids: List[str]
    expected_global_menu_item_id: str
    expected_global_variant_id: str
    mutation_id: Optional[str] = None


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
    query_id: Optional[str] = None
    confidence: float = 1.0
    corrected_prompt: Optional[str] = None  # Phase 5.1: spelling-corrected question (when shown in UI)
    query_status: Optional[str] = None  # Phase 8: "complete" | "incomplete" | "ignored"
    pending_clarification_question: Optional[str] = None  # Phase 8: when query_status=incomplete, the question we asked
    previous_query_ignored: Optional[bool] = None  # Phase 8: True when user sent a new query after we asked for clarification


class AIFeedbackRequest(BaseModel):
    """Request body for user feedback (thumbs up/down)."""
    query_id: str
    is_positive: bool
    comment: Optional[str] = None


class CacheEntryPatchRequest(BaseModel):
    """Request body for marking a cache entry as incorrect (human feedback for cloud learning)."""
    is_incorrect: bool


# --- Customer Profile Models ---

class CustomerSearchResponse(BaseModel):
    """Response model for customer search results"""
    customer_id: str
    name: str
    phone: Optional[str] = None
    total_spent: Optional[float] = 0.0
    last_order_date: Optional[str] = None
    is_verified: bool = False


class CustomerProfileCustomer(CustomerSearchResponse):
    """Customer details shown in the profile view."""
    address: Optional[str] = None


class CustomerAddressResponse(BaseModel):
    """Structured address row for a customer."""
    address_id: int
    customer_id: str
    label: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    is_default: bool = False


class CustomerProfileOrder(BaseModel):
    """Order summary for customer profile view"""
    order_id: str
    order_number: str  # e.g. petpooja_order_id or internal ID
    created_on: str
    items_summary: str  # "Burger (2), Fries (1)"
    total_amount: float
    order_source: str  # e.g. Zomato, Swiggy
    status: str
    is_verified: bool


class CustomerProfileResponse(BaseModel):
    """Complete customer profile with order history"""
    customer: CustomerProfileCustomer
    orders: list[CustomerProfileOrder]
    addresses: list[CustomerAddressResponse] = []


class CustomerSimilarityCandidatePerson(BaseModel):
    """Compact customer summary for similarity review and merge preview."""
    customer_id: str
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    total_orders: int = 0
    total_spent: float = 0.0
    last_order_date: Optional[str] = None
    is_verified: bool = False


class CustomerSimilarityCandidate(BaseModel):
    """One suggested duplicate/related customer pair."""
    source_customer: CustomerSimilarityCandidatePerson
    target_customer: CustomerSimilarityCandidatePerson
    score: float
    model_name: str
    reasons: List[str] = []
    metrics: Dict[str, float] = {}


class CustomerMergePreviewOrderItem(BaseModel):
    """One aggregated item entry inside a preview order row."""
    item_name: str
    quantity: int = 0


class CustomerMergePreviewOrder(BaseModel):
    """Recent order summary shown during merge preview."""
    order_id: str
    order_number: str
    created_on: Optional[str] = None
    total_amount: float = 0.0
    items: List[CustomerMergePreviewOrderItem] = []
    items_summary: str = ""


class CustomerMergePreviewTopItem(BaseModel):
    """Top/favorite item summary for merge preview."""
    item_name: str
    order_count: int = 0
    total_quantity: int = 0


class CustomerMergePreviewCustomerOrders(BaseModel):
    """Customer order snapshot for side-by-side merge review."""
    recent_orders: List[CustomerMergePreviewOrder] = []
    top_items: List[CustomerMergePreviewTopItem] = []


class CustomerMergePreviewResponse(BaseModel):
    """Preview payload for a customer merge."""
    source_customer: CustomerSimilarityCandidatePerson
    target_customer: CustomerSimilarityCandidatePerson
    source_order_snapshot: CustomerMergePreviewCustomerOrders
    target_order_snapshot: CustomerMergePreviewCustomerOrders
    orders_to_move: int
    source_address_count: int
    target_address_count: int
    reasons: List[str] = []
    score: Optional[float] = None
    model_name: Optional[str] = None
    merge_rule: str = "into_verified_target"
    requires_verification_selection: bool = False
    can_mark_target_verified: bool = False


class CustomerMergeRequest(BaseModel):
    """Request to merge a source customer into a target customer."""
    source_customer_id: str
    target_customer_id: str
    similarity_score: Optional[float] = None
    model_name: Optional[str] = None
    reasons: Optional[List[str]] = None
    mark_target_verified: Optional[bool] = None


class CustomerMergeHistoryEntry(BaseModel):
    """One customer merge history row."""
    merge_id: int
    source_customer_id: str
    source_name: Optional[str] = None
    target_customer_id: str
    target_name: Optional[str] = None
    similarity_score: Optional[float] = None
    model_name: Optional[str] = None
    orders_moved: int = 0
    copied_address_count: int = 0
    merged_at: str
    undone_at: Optional[str] = None


class CustomerMergeResult(BaseModel):
    """Response returned after merge or undo."""
    status: str
    message: str
    merge_id: Optional[int] = None
    source_customer_id: Optional[str] = None
    target_customer_id: Optional[str] = None
    orders_moved: Optional[int] = None
    target_is_verified: Optional[bool] = None


class CustomerUndoMergeRequest(BaseModel):
    """Request to undo a previous customer merge."""
    merge_id: int
