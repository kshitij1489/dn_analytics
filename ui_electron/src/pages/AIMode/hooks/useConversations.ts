import { useState, useEffect, useCallback } from 'react';
import { endpoints } from '../../../api';
import type { Conversation, Message } from '../types';

const STORAGE_KEY = 'ai_active_conversation';

export interface UseConversationsOptions {
    setMessages: (msgs: Message[] | ((prev: Message[]) => Message[])) => void;
    onStartNew?: () => void;
    onLoadSuccess?: () => void;
}

export function useConversations(options: UseConversationsOptions) {
    const { setMessages, onStartNew, onLoadSuccess } = options;
    const [conversationId, setConversationId] = useState<string | null>(null);
    const [conversations, setConversations] = useState<Conversation[]>([]);

    const loadConversations = useCallback(async () => {
        try {
            const res = await endpoints.conversations.list({ limit: 20 });
            setConversations(res.data || []);
        } catch (err) {
            console.error('Failed to load conversations:', err);
        }
    }, []);

    const loadConversation = useCallback(
        async (id: string) => {
            try {
                const res = await endpoints.conversations.getMessages(id);
                const msgs: Message[] = (res.data || []).map((m: Record<string, unknown>) => ({
                    role: m.role as 'user' | 'ai',
                    content: m.content,
                    type: (m.type as Message['type']) || 'text',
                    sql_query: m.sql_query as string | undefined,
                    explanation: m.explanation as string | undefined,
                    query_id: m.query_id as string | undefined,
                    query_status: m.query_status as Message['query_status'],
                    message_id: m.message_id as string | undefined
                }));
                setMessages(msgs);
                setConversationId(id);
                onLoadSuccess?.();
            } catch (err) {
                console.error('Failed to load conversation:', err);
                localStorage.removeItem(STORAGE_KEY);
            }
        },
        [setMessages, onLoadSuccess]
    );

    const startNewConversation = useCallback(() => {
        setMessages([]);
        setConversationId(null);
        localStorage.removeItem(STORAGE_KEY);
        onStartNew?.();
    }, [setMessages, onStartNew]);

    const deleteConversation = useCallback(
        async (id: string, e: React.MouseEvent) => {
            e.stopPropagation();
            if (!confirm('Are you sure you want to delete this conversation?')) return;
            try {
                await endpoints.conversations.delete(id);
                setConversations(prev => prev.filter(c => c.conversation_id !== id));
                if (conversationId === id) {
                    startNewConversation();
                }
            } catch (err) {
                console.error('Failed to delete conversation:', err);
            }
        },
        [conversationId, startNewConversation]
    );

    useEffect(() => {
        loadConversations();
        const lastConvId = localStorage.getItem(STORAGE_KEY);
        if (lastConvId) loadConversation(lastConvId);
    }, []);

    useEffect(() => {
        if (conversationId) {
            localStorage.setItem(STORAGE_KEY, conversationId);
        } else {
            localStorage.removeItem(STORAGE_KEY);
        }
    }, [conversationId]);

    return {
        conversationId,
        setConversationId,
        conversations,
        loadConversations,
        loadConversation,
        startNewConversation,
        deleteConversation
    };
}
