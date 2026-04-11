export interface CustomerSimilarityCandidatePerson {
    customer_id: string;
    name: string;
    phone?: string | null;
    address?: string | null;
    total_orders: number;
    total_spent: number;
    last_order_date?: string | null;
    is_verified: boolean;
}

export interface CustomerSimilarityCandidate {
    source_customer: CustomerSimilarityCandidatePerson;
    target_customer: CustomerSimilarityCandidatePerson;
    score: number;
    model_name: string;
    reasons: string[];
    metrics: Record<string, number>;
}

export interface CustomerMergePreview {
    source_customer: CustomerSimilarityCandidatePerson;
    target_customer: CustomerSimilarityCandidatePerson;
    orders_to_move: number;
    source_address_count: number;
    target_address_count: number;
    reasons: string[];
    score?: number | null;
    model_name?: string | null;
}

export interface CustomerMergeRequestPayload {
    source_customer_id: string;
    target_customer_id: string;
    similarity_score?: number;
    model_name?: string;
    reasons?: string[];
}

export interface CustomerMergeHistoryEntry {
    merge_id: number;
    source_customer_id: string;
    source_name?: string | null;
    target_customer_id: string;
    target_name?: string | null;
    similarity_score?: number | null;
    model_name?: string | null;
    orders_moved: number;
    copied_address_count: number;
    merged_at: string;
    undone_at?: string | null;
}

export function getApiErrorMessage(error: any): string {
    return error?.response?.data?.detail || error?.message || 'Request failed.';
}

export function formatDateTime(value?: string | null): string {
    if (!value) return 'Unknown';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
}

export function formatCurrency(value?: number | null): string {
    return `₹${Math.round(value || 0).toLocaleString()}`;
}

export function buildSuggestionMergeRequest(candidate: CustomerSimilarityCandidate): CustomerMergeRequestPayload {
    return {
        source_customer_id: candidate.source_customer.customer_id,
        target_customer_id: candidate.target_customer.customer_id,
        similarity_score: candidate.score,
        model_name: candidate.model_name,
        reasons: candidate.reasons,
    };
}
