/**
 * Type definitions for AIMode components.
 */

/** Structured content part in multi-part AI responses */
export interface ResponsePart {
    type: 'text' | 'table' | 'chart';
    content: string | Record<string, unknown>[] | Record<string, unknown>;
    explanation?: string;
    sql_query?: string;
}

/** Chat message in the conversation */
export interface Message {
    role: 'user' | 'ai';
    /** Can be text string, array of ResponseParts, or structured data */
    content: string | ResponsePart[] | Record<string, unknown>[] | Record<string, unknown>;
    type?: 'text' | 'table' | 'chart' | 'multi';
    sql_query?: string;
    explanation?: string;
    query_id?: string;
    feedback?: 'positive' | 'negative';
    corrected_prompt?: string;
    query_status?: 'complete' | 'incomplete' | 'ignored';
    pending_clarification_question?: string;
    previous_query_ignored?: boolean;
    message_id?: string;
}

/** Conversation metadata */
export interface Conversation {
    conversation_id: string;
    title: string;
    started_at: string;
    updated_at: string;
    message_count: number;
}

/** Suggestion item for popular queries */
export interface Suggestion {
    query: string;
    frequency: number;
}
