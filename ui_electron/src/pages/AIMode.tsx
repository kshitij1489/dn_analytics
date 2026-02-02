import { useState, useRef, useEffect, useCallback } from 'react';
import { endpoints, type DebugLogEntry } from '../api';
import { LoadingSpinner, ActionButton } from '../components';
import { ClientSideDataTable } from '../components/ClientSideDataTable';
import {
    BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
    XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';

interface Message {
    role: 'user' | 'ai';
    content: any; // Text string, Data object, or array of parts when type='multi'
    type?: 'text' | 'table' | 'chart' | 'multi';
    sql_query?: string;
    explanation?: string;
    query_id?: string;
    feedback?: 'positive' | 'negative';
    corrected_prompt?: string; // Phase 5.1: spelling-corrected question (when shown)
    query_status?: 'complete' | 'incomplete' | 'ignored'; // Phase 8
    pending_clarification_question?: string; // Phase 8: when incomplete
    previous_query_ignored?: boolean; // Phase 8: true when backend says user sent new query after clarification
    message_id?: string; // Phase 5: for persistence and undo
}

/** One part of a multi-part AI response (Phase 3) */
interface ResponsePart {
    type: 'text' | 'table' | 'chart';
    content: any;
    explanation?: string;
    sql_query?: string;
}

const SUGGESTED_QUERIES = [
    "What was the total revenue yesterday?",
    "Top 5 selling items this month",
    "Which category has the highest revenue?",
    "Show me orders from Swiggy in the last 7 days",
    "Average order value for dine-in orders"
];

const CHART_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16'];

interface Conversation {
    conversation_id: string;
    title: string;
    started_at: string;
    updated_at: string;
    message_count: number;
}

export default function AIMode() {
    const [messages, setMessages] = useState<Message[]>([]);
    const [prompt, setPrompt] = useState('');
    const [loading, setLoading] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    // Conversation persistence state
    const [conversationId, setConversationId] = useState<string | null>(null);
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [showHistory, setShowHistory] = useState(false);
    const [lastFailedPrompt, setLastFailedPrompt] = useState<string | null>(null);
    const [showDebug, setShowDebug] = useState(false);
    const [debugLogEntries, setDebugLogEntries] = useState<DebugLogEntry[]>([]);

    const loadDebugLogs = useCallback(async () => {
        try {
            const res = await endpoints.ai.getDebugLogs();
            setDebugLogEntries(res.data?.entries ?? []);
        } catch (err) {
            console.error('Failed to load debug logs:', err);
            setDebugLogEntries([]);
        }
    }, []);

    const handleDebug = async () => {
        const next = !showDebug;
        setShowDebug(next);
        if (next) {
            try {
                await endpoints.ai.initDebug();
                await loadDebugLogs();
            } catch (error) {
                console.error('Failed to init debug:', error);
            }
        }
    };

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    const [showSuggestions, setShowSuggestions] = useState(false);
    const [suggestions, setSuggestions] = useState<{ query: string; frequency: number }[]>([]);

    const historyTriggerRef = useRef<HTMLButtonElement>(null);
    const historyPanelRef = useRef<HTMLDivElement>(null);
    const suggestionsContainerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        scrollToBottom();
    }, [messages, loading]);

    // Close History and Suggestions when clicking outside
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            const target = e.target as Node;
            if (showHistory
                && historyTriggerRef.current && !historyTriggerRef.current.contains(target)
                && historyPanelRef.current && !historyPanelRef.current.contains(target)) {
                setShowHistory(false);
            }
            if (showSuggestions && suggestionsContainerRef.current && !suggestionsContainerRef.current.contains(target)) {
                setShowSuggestions(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showHistory, showSuggestions]);

    // Load conversation list and active conversation on mount
    useEffect(() => {
        loadConversations();

        // Auto-load last active conversation
        const lastConvId = localStorage.getItem('ai_active_conversation');
        if (lastConvId) {
            loadConversation(lastConvId);
        }
    }, []);

    // Persist active conversation ID
    useEffect(() => {
        if (conversationId) {
            localStorage.setItem('ai_active_conversation', conversationId);
        } else {
            localStorage.removeItem('ai_active_conversation');
        }
    }, [conversationId]);

    const loadConversations = async () => {
        try {
            const res = await endpoints.conversations.list({ limit: 20 });
            setConversations(res.data || []);
        } catch (err) {
            console.error('Failed to load conversations:', err);
        }
    };

    const loadSuggestions = async () => {
        try {
            const res = await endpoints.ai.suggestions(10);
            setSuggestions(res.data || []);
        } catch (err) {
            console.error('Failed to load suggestions:', err);
        }
    };

    const startNewConversation = async () => {
        setMessages([]);
        setConversationId(null);
        setLastFailedPrompt(null);
        localStorage.removeItem('ai_active_conversation');
    };

    const loadConversation = async (id: string) => {
        try {
            const res = await endpoints.conversations.getMessages(id);
            const msgs: Message[] = (res.data || []).map((m: any) => ({
                role: m.role,
                content: m.content,
                type: m.type || 'text',
                sql_query: m.sql_query,
                explanation: m.explanation,
                query_id: m.query_id,
                query_status: m.query_status,
                message_id: m.message_id
            }));
            setMessages(msgs);
            setConversationId(id);
            setShowHistory(false);
            setLastFailedPrompt(null);
        } catch (err) {
            console.error('Failed to load conversation:', err);
            // If 404, clear invalid local storage
            localStorage.removeItem('ai_active_conversation');
        }
    };

    const deleteConversation = async (id: string, e: React.MouseEvent) => {
        e.stopPropagation(); // Prevent triggering loadConversation
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
    };



    const handleRetry = () => {
        if (lastFailedPrompt) {
            handleAsk(lastFailedPrompt);
        }
    };

    const handleUndo = async () => {
        if (messages.length < 2) return;

        // Remove from server if persisted
        const lastAiMsg = messages[messages.length - 1];
        const lastUserMsg = messages[messages.length - 2];

        // Optimistically remove from UI
        setMessages(prev => prev.slice(0, -2));
        setLastFailedPrompt(null);

        if (conversationId) {
            try {
                // Best effort deletion
                if (lastAiMsg.message_id) {
                    await endpoints.conversations.deleteMessage(conversationId, lastAiMsg.message_id);
                }
                if (lastUserMsg.message_id) {
                    await endpoints.conversations.deleteMessage(conversationId, lastUserMsg.message_id);
                }
            } catch (err) {
                console.error('Failed to delete undone messages', err);
            }
        }
    };

    const handleAsk = async (customPrompt?: string) => {
        const queryPrompt = customPrompt || prompt;
        if (!queryPrompt.trim()) return;

        // Create conversation if this is the first message
        let currentConvId = conversationId;
        if (!currentConvId && messages.length === 0) {
            try {
                const res = await endpoints.conversations.create({ title: queryPrompt.slice(0, 50) });
                currentConvId = res.data.conversation_id;
                setConversationId(currentConvId);
            } catch (err) {
                console.error('Failed to create conversation:', err);
            }
        }

        // Add User Message
        const userMsg: Message = {
            role: 'user',
            content: queryPrompt,
            message_id: currentConvId ? crypto.randomUUID() : undefined // Temp ID until confirmed? Or just rely on server response if we sync properly. 
            // Actually, for better persistence tracking we should await the server response or just trust the append order.
            // Simplified: We'll persist async and not block UI.
        };
        setMessages(prev => [...prev, userMsg]);
        setPrompt('');
        setLoading(true);
        setLastFailedPrompt(null);

        // Persist user message
        if (currentConvId) {
            // We need the ID back from server to support Undo properly later?
            // For now, let's assume valid deletion requires IDs which we only get if we wait. 
            // But we want optimistic UI. 
            // Better strategy: Fire and forget for now, but for Undo to work perfectly we'd need to store the returned IDs.
            // Let's await to get the ID.
            endpoints.conversations.addMessage(currentConvId, { ...userMsg, type: 'text' })
                .then(res => {
                    // Update the message in state with the real ID
                    setMessages(prev => prev.map(m => m === userMsg ? { ...m, message_id: res.data.message_id } : m));
                })
                .catch(err => console.error('Failed to persist user msg', err));
        }

        const isStreamingRequest = queryPrompt.toLowerCase().includes('report') || queryPrompt.toLowerCase().includes('summary');

        try {
            const history = messages.slice(-10).map(m => ({
                role: m.role,
                content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content)
            }));

            const lastMsg = messages[messages.length - 1];
            const lastAiWasClarification = lastMsg?.role === 'ai' && lastMsg?.query_status === 'incomplete';

            if (isStreamingRequest) {
                // HANDLE STREAMING
                const aiMsgPlaceholder: Message = { role: 'ai', content: '', type: 'text' };
                setMessages(prev => [...prev, aiMsgPlaceholder]);

                const response = await endpoints.ai.chatStream({
                    prompt: queryPrompt,
                    history,
                    last_ai_was_clarification: lastAiWasClarification
                });

                if (!response.body) throw new Error("No response body");

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let accumulatedContent = '';
                let finalResponseData: any = null;
                const parts: ResponsePart[] = []; // accumulate parts for multi-part display during stream
                let sseBuffer = ''; // buffer for SSE so we don't lose events split across reads

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    sseBuffer += decoder.decode(value, { stream: true });
                    const eventBlocks = sseBuffer.split('\n\n');
                    sseBuffer = eventBlocks.pop() ?? ''; // keep incomplete block for next read

                    for (const line of eventBlocks) {
                        if (!line.startsWith('data: ')) continue;
                        try {
                            const data = JSON.parse(line.substring(6));

                            if (data.type === 'status') {
                                // Status events could drive a status line or toast later
                            } else if (data.type === 'chunk') {
                                accumulatedContent += data.content;
                                setMessages(prev => {
                                    const newMsgs = [...prev];
                                    const last = newMsgs[newMsgs.length - 1];
                                    const currentParts = [...parts];
                                    if (accumulatedContent) {
                                        currentParts.push({ type: 'text', content: accumulatedContent });
                                    }
                                    newMsgs[newMsgs.length - 1] = {
                                        ...last,
                                        type: 'multi',
                                        content: currentParts
                                    };
                                    return newMsgs;
                                });
                            } else if (data.type === 'part') {
                                parts.push(data.part);
                                if (data.part.type === 'text' && accumulatedContent) {
                                    accumulatedContent = '';
                                }
                                setMessages(prev => {
                                    const newMsgs = [...prev];
                                    const last = newMsgs[newMsgs.length - 1];
                                    const currentParts = [...parts];
                                    if (accumulatedContent) {
                                        currentParts.push({ type: 'text', content: accumulatedContent });
                                    }
                                    newMsgs[newMsgs.length - 1] = {
                                        ...last,
                                        type: 'multi',
                                        content: currentParts
                                    };
                                    return newMsgs;
                                });
                            } else if (data.type === 'debug' && Array.isArray(data.entries)) {
                                setDebugLogEntries(data.entries);
                            } else if (data.type === 'complete') {
                                finalResponseData = data.response;
                            } else if (data.type === 'error') {
                                throw new Error(data.message);
                            }
                        } catch (e) {
                            // skip malformed or incomplete JSON (e.g. chunk boundary)
                        }
                    }
                }

                // Finalize AI message persistence
                const finalAiMsg = {
                    ...aiMsgPlaceholder,
                    // Use final response content if available, else whatever we built
                    content: finalResponseData ? finalResponseData.content : (parts.length > 0 ? parts : accumulatedContent),
                    type: finalResponseData ? finalResponseData.type : (parts.length > 0 ? 'multi' : 'text'),
                    ...(finalResponseData || {})
                };

                if (currentConvId) {
                    endpoints.conversations.addMessage(currentConvId, {
                        ...finalAiMsg,
                        type: finalAiMsg.type ?? 'text',
                        query_status: 'complete'
                    } as any).then(res => {
                        setMessages(prev => prev.map(m => m === aiMsgPlaceholder ? { ...finalAiMsg, message_id: res.data.message_id } : m));
                    });

                    loadConversations(); // Refresh list to update timestamps
                }

            } else {
                // HANDLE STANDARD REQUEST
                const res = await endpoints.ai.chat({
                    prompt: queryPrompt,
                    history,
                    last_ai_was_clarification: lastAiWasClarification
                });

                const aiMsg: Message = {
                    role: 'ai',
                    content: res.data.content,
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    type: res.data.type as any,
                    sql_query: res.data.sql_query,
                    explanation: res.data.explanation,
                    query_id: res.data.query_id,
                    corrected_prompt: res.data.corrected_prompt,
                    query_status: res.data.query_status,
                    pending_clarification_question: res.data.pending_clarification_question,
                    previous_query_ignored: res.data.previous_query_ignored
                };

                setMessages(prev => [...prev, aiMsg]);

                if (showDebug) {
                    loadDebugLogs();
                }

                // Persist AI message
                if (currentConvId) {
                    endpoints.conversations.addMessage(currentConvId, aiMsg)
                        .then(res => {
                            setMessages(prev => prev.map(m => m === aiMsg ? { ...m, message_id: res.data.message_id } : m));
                        });

                    loadConversations();
                }
            }
        } catch (err: any) {
            const errorMsg: Message = {
                role: 'ai',
                content: "Sorry, I encountered an error processing your request. Click 'Retry' to try again.",
                type: 'text'
            };
            setMessages(prev => [...prev, errorMsg]);
            setLastFailedPrompt(queryPrompt);
        } finally {
            setLoading(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleAsk();
        }
    };

    const handleFeedback = async (index: number, isPositive: boolean) => {
        const msg = messages[index];
        if (!msg.query_id || msg.feedback) return;

        try {
            await endpoints.ai.feedback({
                query_id: msg.query_id,
                is_positive: isPositive
            });

            setMessages(prev => prev.map((m, i) =>
                i === index ? { ...m, feedback: isPositive ? 'positive' : 'negative' } : m
            ));
        } catch (err) {
            console.error("Feedback failed", err);
        }
    };

    return (
        <div style={{
            maxWidth: '1200px',
            margin: '0 auto',
            height: 'calc(100vh - 40px)',
            display: 'flex',
            flexDirection: 'column'
        }}>
            {/* Header */}
            <div style={{
                padding: '20px 0',
                borderBottom: '1px solid var(--border-color)',
                marginBottom: '20px',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center'
            }}>
                <div>
                    <h1 style={{ fontSize: '1.8rem', margin: 0 }}>✨ AI Assistant</h1>
                    <p style={{ margin: '5px 0 0', color: 'var(--text-secondary)' }}>
                        Ask questions, generate reports, and analyze your data.
                    </p>
                </div>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    {/* History Toggle */}
                    <button
                        ref={historyTriggerRef}
                        onClick={() => setShowHistory(!showHistory)}
                        style={{
                            padding: '8px 16px',
                            background: showHistory ? 'var(--primary-color)' : 'var(--card-bg)',
                            border: '1px solid var(--border-color)',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            color: showHistory ? 'white' : 'var(--text-color)',
                            fontSize: '0.85rem'
                        }}
                    >
                        📜 History
                    </button>
                    {/* New Chat */}
                    <button
                        onClick={startNewConversation}
                        style={{
                            padding: '8px 16px',
                            background: 'var(--card-bg)',
                            border: '1px solid var(--border-color)',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            color: 'var(--text-color)',
                            fontSize: '0.85rem'
                        }}
                    >
                        ➕ New Chat
                    </button>
                    {/* Suggestions */}
                    <div ref={suggestionsContainerRef} style={{ position: 'relative' }}>
                        <button
                            onClick={() => {
                                setShowSuggestions(!showSuggestions);
                                if (!showSuggestions) loadSuggestions();
                            }}
                            style={{
                                padding: '8px 16px',
                                background: showSuggestions ? 'var(--primary-color)' : 'var(--card-bg)',
                                border: '1px solid var(--border-color)',
                                borderRadius: '8px',
                                cursor: 'pointer',
                                color: showSuggestions ? 'white' : 'var(--text-color)',
                                fontSize: '0.85rem'
                            }}
                        >
                            💡 Suggestions
                        </button>

                        {/* Suggestions Dropdown */}
                        {showSuggestions && (
                            <div style={{
                                position: 'absolute',
                                right: 0,
                                top: '45px',
                                width: '300px',
                                background: 'var(--card-bg)',
                                border: '1px solid var(--border-color)',
                                borderRadius: '12px',
                                padding: '16px',
                                boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
                                zIndex: 1000
                            }}>
                                <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>Popular Queries</h3>
                                {suggestions.length === 0 ? (
                                    <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No suggestions available</p>
                                ) : (
                                    suggestions.map((s, i) => (
                                        <div
                                            key={i}
                                            onClick={() => {
                                                handleAsk(s.query);
                                                setShowSuggestions(false);
                                            }}
                                            style={{
                                                padding: '8px 10px',
                                                marginBottom: '6px',
                                                background: 'var(--bg-color)',
                                                borderRadius: '6px',
                                                cursor: 'pointer',
                                                fontSize: '0.85rem',
                                                color: 'var(--text-color)',
                                                display: 'flex',
                                                justifyContent: 'space-between',
                                                alignItems: 'center'
                                            }}
                                        >
                                            <span style={{ flex: 1, marginRight: '10px' }}>{s.query}</span>
                                            <span style={{ fontSize: '0.75rem', opacity: 0.7, background: 'rgba(0,0,0,0.1)', padding: '2px 6px', borderRadius: '10px' }}>
                                                {s.frequency}x
                                            </span>
                                        </div>
                                    ))
                                )}
                            </div>
                        )}
                    </div>

                    {/* Undo */}
                    {messages.length >= 2 && (
                        <button
                            onClick={handleUndo}
                            style={{
                                padding: '8px 16px',
                                background: 'var(--card-bg)',
                                border: '1px solid var(--border-color)',
                                borderRadius: '8px',
                                cursor: 'pointer',
                                color: 'var(--text-color)',
                                fontSize: '0.85rem'
                            }}
                        >
                            ↩️ Undo
                        </button>
                    )}
                    {/* Debug Button */}
                    <button
                        onClick={handleDebug}
                        style={{
                            padding: '8px 16px',
                            background: showDebug ? 'var(--primary-color)' : 'var(--card-bg)',
                            border: '1px solid var(--border-color)',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            color: showDebug ? 'white' : 'var(--text-color)',
                            fontSize: '0.85rem'
                        }}
                    >
                        🐞 Debug
                    </button>
                    {/* Retry */}
                    {lastFailedPrompt && (
                        <button
                            onClick={handleRetry}
                            style={{
                                padding: '8px 16px',
                                background: '#ef4444',
                                border: 'none',
                                borderRadius: '8px',
                                cursor: 'pointer',
                                color: 'white',
                                fontSize: '0.85rem'
                            }}
                        >
                            🔄 Retry
                        </button>
                    )}
                </div>
            </div>

            {/* History Sidebar */}
            {showHistory && (
                <div
                    ref={historyPanelRef}
                    style={{
                        position: 'fixed',
                        right: '20px',
                        top: '80px',
                        width: '300px',
                        maxHeight: '400px',
                        overflowY: 'auto',
                        background: 'var(--card-bg)',
                        border: '1px solid var(--border-color)',
                        borderRadius: '12px',
                        padding: '16px',
                        boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
                        zIndex: 1000
                    }}>
                    <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>Recent Conversations</h3>
                    {conversations.length === 0 ? (
                        <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No conversations yet</p>
                    ) : (
                        conversations.map(conv => (
                            <div
                                key={conv.conversation_id}
                                onClick={() => loadConversation(conv.conversation_id)}
                                style={{
                                    padding: '10px',
                                    marginBottom: '8px',
                                    background: conv.conversation_id === conversationId ? 'var(--primary-color)' : 'var(--bg-color)',
                                    borderRadius: '8px',
                                    cursor: 'pointer',
                                    color: conv.conversation_id === conversationId ? 'white' : 'var(--text-color)',
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    position: 'relative'
                                }}
                            >
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontWeight: 500, fontSize: '0.9rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                        {conv.title || 'Untitled'}
                                    </div>
                                    <div style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '4px' }}>
                                        {conv.message_count} messages
                                    </div>
                                </div>
                                <button
                                    onClick={(e) => deleteConversation(conv.conversation_id, e)}
                                    style={{
                                        background: 'transparent',
                                        border: 'none',
                                        cursor: 'pointer',
                                        fontSize: '1rem',
                                        padding: '4px',
                                        marginLeft: '8px',
                                        opacity: 0.6,
                                        color: 'inherit'
                                    }}
                                    title="Delete conversation"
                                >
                                    🗑️
                                </button>
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* Chat Area */}
            <div style={{
                flex: 1,
                overflowY: 'auto',
                padding: '0 10px',
                display: 'flex',
                flexDirection: 'column',
                gap: '20px'
            }}>
                {messages.length === 0 && (
                    <div style={{
                        textAlign: 'center',
                        marginTop: '100px',
                        color: 'var(--text-secondary)'
                    }}>
                        <h3>How can I help you today?</h3>
                        <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap', marginTop: '20px' }}>
                            {SUGGESTED_QUERIES.map((q, idx) => (
                                <button
                                    key={idx}
                                    onClick={() => handleAsk(q)}
                                    style={{
                                        padding: '8px 16px',
                                        background: 'var(--card-bg)',
                                        border: '1px solid var(--border-color)',
                                        borderRadius: '20px',
                                        cursor: 'pointer',
                                        color: 'var(--text-color)',
                                        fontSize: '0.9rem'
                                    }}
                                >
                                    {q}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {messages.map((msg, idx) => (
                    <div
                        key={idx}
                        style={{
                            display: 'flex',
                            justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                        }}
                    >
                        <div style={{
                            maxWidth: '85%',
                            alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                        }}>
                            {/* Phase 8: notice when user moved on from a clarification */}
                            {msg.role === 'ai' && msg.previous_query_ignored && (
                                <div style={{
                                    fontSize: '0.8rem',
                                    color: 'var(--text-secondary)',
                                    fontStyle: 'italic',
                                    marginBottom: '8px',
                                }}>
                                    Previous question was left unanswered.
                                </div>
                            )}
                            {/* Message Bubble */}
                            <div className="card" style={{
                                background: msg.role === 'user' ? 'var(--accent-color)' : 'var(--card-bg)',
                                color: msg.role === 'user' ? 'white' : 'var(--text-color)',
                                padding: '15px 20px',
                                borderRadius: msg.role === 'user' ? '20px 20px 0 20px' : '20px 20px 20px 0',
                                marginBottom: '5px',
                                boxShadow: '0 2px 10px rgba(0,0,0,0.05)'
                            }}>
                                {/* Phase 5.1: corrected question (subtle) */}
                                {msg.role === 'ai' && msg.corrected_prompt && (
                                    <div style={{
                                        fontSize: '0.85rem',
                                        color: 'var(--text-secondary)',
                                        marginBottom: '10px',
                                        fontStyle: 'italic'
                                    }}>
                                        You asked: {msg.corrected_prompt}
                                    </div>
                                )}
                                {/* Content Renderer */}
                                {msg.role === 'user' && (
                                    <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
                                        {msg.content}
                                    </div>
                                )}

                                {/* Multi-part response (Phase 3) */}
                                {msg.role === 'ai' && msg.type === 'multi' && Array.isArray(msg.content) && (
                                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                                        {(msg.content as ResponsePart[]).map((part, partIdx) => (
                                            <div key={partIdx}>
                                                {part.type === 'text' && (
                                                    <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>{part.content}</div>
                                                )}
                                                {part.type === 'table' && (() => {
                                                    const rows = part.content || [];
                                                    const columns = rows.length > 0 ? Object.keys(rows[0]) : [];
                                                    const isSingleValue = rows.length === 1 && columns.length <= 2;
                                                    const isSmallTable = rows.length <= 10;
                                                    if (isSingleValue) {
                                                        const value = rows[0][columns[columns.length - 1]];
                                                        const label = columns.length === 2 ? rows[0][columns[0]] : columns[0];
                                                        return (
                                                            <div>
                                                                {part.explanation && <div style={{ marginBottom: '10px' }}>{part.explanation}</div>}
                                                                <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--accent-color)', padding: '10px 0' }}>
                                                                    {typeof value === 'number' ? value.toLocaleString() : value}
                                                                </div>
                                                                {columns.length === 2 && <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>{label}</div>}
                                                            </div>
                                                        );
                                                    }
                                                    if (isSmallTable) {
                                                        return (
                                                            <div>
                                                                {part.explanation && <div style={{ marginBottom: '15px' }}>{part.explanation}</div>}
                                                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
                                                                    <thead>
                                                                        <tr>
                                                                            {columns.map(col => (
                                                                                <th key={col} style={{ textAlign: 'left', padding: '8px 12px', borderBottom: '2px solid var(--border-color)', fontWeight: 600 }}>
                                                                                    {col.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())}
                                                                                </th>
                                                                            ))}
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody>
                                                                        {rows.map((row: Record<string, unknown>, i: number) => (
                                                                            <tr key={i}>
                                                                                {columns.map(col => (
                                                                                    <td key={col} style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-color)' }}>
                                                                                        {typeof row[col] === 'number' ? Number(row[col]).toLocaleString() : String(row[col] ?? '—')}
                                                                                    </td>
                                                                                ))}
                                                                            </tr>
                                                                        ))}
                                                                    </tbody>
                                                                </table>
                                                                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>{rows.length} row{rows.length !== 1 ? 's' : ''}</div>
                                                            </div>
                                                        );
                                                    }
                                                    return (
                                                        <div>
                                                            {part.explanation && <div style={{ marginBottom: '15px' }}>{part.explanation}</div>}
                                                            <ClientSideDataTable data={rows} columns={columns} filenamePrefix="ai_data" />
                                                        </div>
                                                    );
                                                })()}
                                                {part.type === 'chart' && (() => {
                                                    const config = part.content || {};
                                                    const chartData = config.data || [];
                                                    const chartType = config.chart_type || 'bar';
                                                    const xKey = config.x_key || 'label';
                                                    const yKey = config.y_key || 'value';
                                                    const title = config.title || 'Chart';
                                                    const tooltipStyle = { backgroundColor: 'rgba(0,0,0,0.85)', border: '1px solid #444', borderRadius: '8px', color: '#fff' };
                                                    return (
                                                        <div>
                                                            <div style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: '15px' }}>{title}</div>
                                                            <div style={{ height: '300px', width: '100%' }}>
                                                                <ResponsiveContainer width="100%" height="100%">
                                                                    {chartType === 'bar' ? (
                                                                        <BarChart data={chartData}>
                                                                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                                                            <XAxis dataKey={xKey} stroke="#aaa" />
                                                                            <YAxis stroke="#aaa" />
                                                                            <Tooltip contentStyle={tooltipStyle} />
                                                                            <Legend />
                                                                            <Bar dataKey={yKey} fill="#3B82F6" name={yKey} />
                                                                        </BarChart>
                                                                    ) : chartType === 'line' ? (
                                                                        <LineChart data={chartData}>
                                                                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                                                            <XAxis dataKey={xKey} stroke="#aaa" />
                                                                            <YAxis stroke="#aaa" />
                                                                            <Tooltip contentStyle={tooltipStyle} />
                                                                            <Legend />
                                                                            <Line type="monotone" dataKey={yKey} stroke="#3B82F6" strokeWidth={2} dot={{ r: 4 }} name={yKey} />
                                                                        </LineChart>
                                                                    ) : (
                                                                        <PieChart>
                                                                            <Pie data={chartData} dataKey={yKey} nameKey={xKey} cx="50%" cy="50%" outerRadius={100}
                                                                                label={({ name, percent }: { name?: string; percent?: number }) => `${name ?? 'Unknown'}: ${((percent ?? 0) * 100).toFixed(0)}%`}>
                                                                                {chartData.map((_: unknown, index: number) => (
                                                                                    <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                                                                                ))}
                                                                            </Pie>
                                                                            <Tooltip contentStyle={tooltipStyle} />
                                                                            <Legend />
                                                                        </PieChart>
                                                                    )}
                                                                </ResponsiveContainer>
                                                            </div>
                                                            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '10px' }}>{chartData.length} data point{chartData.length !== 1 ? 's' : ''}</div>
                                                        </div>
                                                    );
                                                })()}
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {msg.role === 'ai' && msg.type !== 'multi' && (msg.type === 'text' || !msg.type) && (
                                    <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
                                        {msg.content}
                                    </div>
                                )}

                                {msg.role === 'ai' && msg.type !== 'multi' && msg.type === 'table' && (() => {
                                    const rows = msg.content || [];
                                    const columns = rows.length > 0 ? Object.keys(rows[0]) : [];
                                    const isSingleValue = rows.length === 1 && columns.length <= 2;
                                    const isSmallTable = rows.length <= 10;

                                    // Single value display (e.g., "What's the total revenue?")
                                    if (isSingleValue) {
                                        const value = rows[0][columns[columns.length - 1]]; // Get the value column (usually last)
                                        const label = columns.length === 2 ? rows[0][columns[0]] : columns[0];
                                        return (
                                            <div>
                                                {msg.explanation && (
                                                    <div style={{ marginBottom: '10px' }}>
                                                        {msg.explanation}
                                                    </div>
                                                )}
                                                <div style={{
                                                    fontSize: '2rem',
                                                    fontWeight: 'bold',
                                                    color: 'var(--accent-color)',
                                                    padding: '10px 0'
                                                }}>
                                                    {typeof value === 'number' ? value.toLocaleString() : value}
                                                </div>
                                                {columns.length === 2 && (
                                                    <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>
                                                        {label}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    }

                                    // Small table - inline display without pagination
                                    if (isSmallTable) {
                                        return (
                                            <div>
                                                {msg.explanation && (
                                                    <div style={{ marginBottom: '15px' }}>
                                                        {msg.explanation}
                                                    </div>
                                                )}
                                                <table style={{
                                                    width: '100%',
                                                    borderCollapse: 'collapse',
                                                    fontSize: '0.9rem'
                                                }}>
                                                    <thead>
                                                        <tr>
                                                            {columns.map(col => (
                                                                <th key={col} style={{
                                                                    textAlign: 'left',
                                                                    padding: '8px 12px',
                                                                    borderBottom: '2px solid var(--border-color)',
                                                                    fontWeight: 600
                                                                }}>
                                                                    {col.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                                                                </th>
                                                            ))}
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {rows.map((row: Record<string, any>, i: number) => (
                                                            <tr key={i}>
                                                                {columns.map(col => (
                                                                    <td key={col} style={{
                                                                        padding: '8px 12px',
                                                                        borderBottom: '1px solid var(--border-color)'
                                                                    }}>
                                                                        {typeof row[col] === 'number'
                                                                            ? row[col].toLocaleString()
                                                                            : row[col] ?? '—'}
                                                                    </td>
                                                                ))}
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                                <div style={{
                                                    fontSize: '0.8rem',
                                                    color: 'var(--text-secondary)',
                                                    marginTop: '8px'
                                                }}>
                                                    {rows.length} row{rows.length !== 1 ? 's' : ''}
                                                </div>
                                            </div>
                                        );
                                    }

                                    // Large table - use full component with pagination
                                    return (
                                        <div>
                                            {msg.explanation && (
                                                <div style={{ marginBottom: '15px' }}>
                                                    {msg.explanation}
                                                </div>
                                            )}
                                            <ClientSideDataTable
                                                data={rows}
                                                columns={columns}
                                                filenamePrefix="ai_data"
                                            />
                                        </div>
                                    );
                                })()}

                                {/* Chart Renderer */}
                                {msg.role === 'ai' && msg.type !== 'multi' && msg.type === 'chart' && (() => {
                                    const config = msg.content || {};
                                    const chartData = config.data || [];
                                    const chartType = config.chart_type || 'bar';
                                    const xKey = config.x_key || 'label';
                                    const yKey = config.y_key || 'value';
                                    const title = config.title || 'Chart';

                                    // Color palette for charts
                                    const COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16'];

                                    const tooltipStyle = {
                                        backgroundColor: 'rgba(0, 0, 0, 0.85)',
                                        border: '1px solid #444',
                                        borderRadius: '8px',
                                        color: '#fff'
                                    };

                                    return (
                                        <div>
                                            <div style={{
                                                fontSize: '1.1rem',
                                                fontWeight: 600,
                                                marginBottom: '15px'
                                            }}>
                                                {title}
                                            </div>

                                            <div style={{ height: '300px', width: '100%' }}>
                                                <ResponsiveContainer width="100%" height="100%">
                                                    {chartType === 'bar' ? (
                                                        <BarChart data={chartData}>
                                                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                                            <XAxis dataKey={xKey} stroke="#aaa" />
                                                            <YAxis stroke="#aaa" />
                                                            <Tooltip contentStyle={tooltipStyle} />
                                                            <Legend />
                                                            <Bar dataKey={yKey} fill="#3B82F6" name={yKey} />
                                                        </BarChart>
                                                    ) : chartType === 'line' ? (
                                                        <LineChart data={chartData}>
                                                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                                            <XAxis dataKey={xKey} stroke="#aaa" />
                                                            <YAxis stroke="#aaa" />
                                                            <Tooltip contentStyle={tooltipStyle} />
                                                            <Legend />
                                                            <Line
                                                                type="monotone"
                                                                dataKey={yKey}
                                                                stroke="#3B82F6"
                                                                strokeWidth={2}
                                                                dot={{ r: 4 }}
                                                                name={yKey}
                                                            />
                                                        </LineChart>
                                                    ) : (
                                                        <PieChart>
                                                            <Pie
                                                                data={chartData}
                                                                dataKey={yKey}
                                                                nameKey={xKey}
                                                                cx="50%"
                                                                cy="50%"
                                                                outerRadius={100}
                                                                label={({ name, percent }: { name?: string; percent?: number }) => `${name ?? 'Unknown'}: ${((percent ?? 0) * 100).toFixed(0)}%`}
                                                            >
                                                                {chartData.map((_: any, index: number) => (
                                                                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                                                ))}
                                                            </Pie>
                                                            <Tooltip contentStyle={tooltipStyle} />
                                                            <Legend />
                                                        </PieChart>
                                                    )}
                                                </ResponsiveContainer>
                                            </div>

                                            <div style={{
                                                fontSize: '0.8rem',
                                                color: 'var(--text-secondary)',
                                                marginTop: '10px'
                                            }}>
                                                {chartData.length} data point{chartData.length !== 1 ? 's' : ''}
                                            </div>
                                        </div>
                                    );
                                })()}

                                {/* Technical Details Toggle */}
                                {(msg.sql_query || msg.role === 'ai') && msg.type !== 'text' && (
                                    <details style={{ marginTop: '10px', fontSize: '0.8rem', opacity: 0.8 }}>
                                        <summary style={{ cursor: 'pointer' }}>View SQL Query</summary>
                                        <pre style={{
                                            background: 'rgba(0,0,0,0.3)',
                                            padding: '10px',
                                            borderRadius: '6px',
                                            overflowX: 'auto',
                                            marginTop: '5px'
                                        }}>
                                            {msg.sql_query || "No SQL generated"}
                                        </pre>
                                    </details>
                                )}
                            </div>

                            {/* Timestamp or Feedback (Future) */}
                            {msg.role === 'ai' && (
                                <div style={{
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    marginTop: '5px'
                                }}>
                                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginLeft: '10px' }}>
                                        AI Assistant
                                    </div>

                                    {msg.query_id && (
                                        <div style={{ display: 'flex', gap: '8px', opacity: msg.feedback ? 0.6 : 1 }}>
                                            <button
                                                onClick={() => handleFeedback(idx, true)}
                                                disabled={!!msg.feedback}
                                                style={{
                                                    background: 'none',
                                                    border: 'none',
                                                    cursor: msg.feedback ? 'default' : 'pointer',
                                                    fontSize: '1rem',
                                                    filter: msg.feedback === 'negative' ? 'grayscale(100%) opacity(0.3)' : 'none',
                                                    opacity: msg.feedback === 'negative' ? 0.3 : 1,
                                                    padding: '2px',
                                                    transition: 'all 0.2s'
                                                }}
                                                title="Helpful"
                                            >
                                                👍
                                            </button>
                                            <button
                                                onClick={() => handleFeedback(idx, false)}
                                                disabled={!!msg.feedback}
                                                style={{
                                                    background: 'none',
                                                    border: 'none',
                                                    cursor: msg.feedback ? 'default' : 'pointer',
                                                    fontSize: '1rem',
                                                    filter: msg.feedback === 'positive' ? 'grayscale(100%)' : 'none',
                                                    opacity: msg.feedback === 'positive' ? 0.3 : 1,
                                                    padding: '2px',
                                                    transition: 'all 0.2s'
                                                }}
                                                title="Not Helpful"
                                            >
                                                👎
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                ))}

                {loading && (
                    <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                        <div className="card" style={{
                            padding: '15px 20px',
                            borderRadius: '20px 20px 20px 0',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '10px'
                        }}>
                            <LoadingSpinner size="small" />
                            <span style={{ color: 'var(--text-secondary)' }}>Thinking...</span>
                        </div>
                    </div>
                )}
                <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            <div style={{
                padding: '20px 0',
                marginTop: '10px'
            }}>
                <div style={{ position: 'relative' }}>
                    <textarea
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Type your question..."
                        rows={1}
                        style={{
                            width: '100%',
                            padding: '16px 60px 16px 20px',
                            fontSize: '1rem',
                            borderRadius: '30px',
                            border: '1px solid var(--border-color)',
                            background: 'var(--input-bg)',
                            color: 'var(--text-color)',
                            resize: 'none',
                            outline: 'none',
                            boxSizing: 'border-box',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.1)'
                        }}
                    />
                    <div style={{
                        position: 'absolute',
                        right: '8px',
                        top: '50%',
                        transform: 'translateY(-50%)'
                    }}>
                        <ActionButton
                            variant="primary"
                            onClick={() => handleAsk()}
                            disabled={!prompt.trim() || loading}
                            style={{
                                borderRadius: '50%',
                                width: '40px',
                                height: '40px',
                                padding: '0',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center'
                            }}
                        >
                            ➤
                        </ActionButton>
                    </div>
                </div>
            </div>
            {/* Debug View Overlay */}
            {showDebug && (
                <div style={{
                    position: 'fixed',
                    top: '80px',
                    left: '50%',
                    transform: 'translateX(-50%)',
                    width: '80%',
                    maxWidth: '800px',
                    height: '600px',
                    background: 'var(--card-bg)',
                    border: '1px solid var(--border-color)',
                    borderRadius: '12px',
                    boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
                    zIndex: 2000,
                    padding: '20px',
                    display: 'flex',
                    flexDirection: 'column'
                }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', flexWrap: 'wrap', gap: '8px' }}>
                        <h2 style={{ margin: 0, fontSize: '1.2rem' }}>🐞 AI Debug Logs</h2>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                            <button
                                onClick={async () => {
                                    try {
                                        await endpoints.ai.clearCache();
                                        setDebugLogEntries([]);
                                    } catch (e) {
                                        console.error('Failed to clear cache:', e);
                                    }
                                }}
                                style={{
                                    padding: '6px 12px',
                                    background: 'var(--card-bg)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: '8px',
                                    cursor: 'pointer',
                                    color: 'var(--text-color)',
                                    fontSize: '0.85rem'
                                }}
                                title="Clear LLM cache so next requests hit the LLM with updated prompts"
                            >
                                🧹 Clear LLM cache
                            </button>
                            <button onClick={() => setShowDebug(false)} style={{ background: 'transparent', border: 'none', fontSize: '1.2rem', cursor: 'pointer' }}>✕</button>
                        </div>
                    </div>
                    <div style={{
                        flex: 1,
                        background: '#1e1e1e',
                        color: '#a9b7c6',
                        fontFamily: 'monospace',
                        padding: '15px',
                        borderRadius: '8px',
                        overflowY: 'auto',
                        fontSize: '0.85rem'
                    }}>
                        {debugLogEntries.length === 0 ? (
                            <>
                                <p style={{ color: '#808080' }}>// Debug logs for the last chat request</p>
                                <p style={{ color: '#6a8759' }}>Send a message to see: user question, cache hit/miss, and LLM or cache response per step.</p>
                            </>
                        ) : (
                            debugLogEntries.map((entry, i) => (
                                <div key={i} style={{ marginBottom: '12px', borderLeft: '3px solid ' + (entry.source === 'user' ? '#569cd6' : entry.source === 'cache' ? '#4ec9b0' : '#dcdcaa'), paddingLeft: '10px' }}>
                                    <div style={{ color: '#808080', marginBottom: '4px' }}>
                                        [{i + 1}] <strong style={{ color: '#9cdcfe' }}>{entry.step}</strong>
                                        <span style={{ marginLeft: '8px', color: entry.source === 'cache' ? '#4ec9b0' : entry.source === 'llm' ? '#dcdcaa' : '#569cd6' }}>
                                            ← {entry.source}
                                        </span>
                                    </div>
                                    {entry.input_preview && (
                                        <div style={{ color: '#ce9178', marginBottom: '4px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                            in: {entry.input_preview}
                                        </div>
                                    )}
                                    {entry.output_preview && (
                                        <div style={{ color: '#9cdcfe', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                            out: {entry.output_preview}
                                        </div>
                                    )}
                                </div>
                            ))
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
