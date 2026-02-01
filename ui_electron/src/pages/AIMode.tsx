import { useState, useRef, useEffect } from 'react';
import { endpoints } from '../api';
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
    log_id?: string;
    feedback?: 'positive' | 'negative';
    corrected_prompt?: string; // Phase 5.1: spelling-corrected question (when shown)
    query_status?: 'complete' | 'incomplete' | 'ignored'; // Phase 8
    pending_clarification_question?: string; // Phase 8: when incomplete
    previous_query_ignored?: boolean; // Phase 8: true when backend says user sent new query after clarification
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

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, loading]);

    // Load conversation list on mount
    useEffect(() => {
        loadConversations();
    }, []);

    const loadConversations = async () => {
        try {
            const res = await endpoints.conversations.list({ limit: 20 });
            setConversations(res.data || []);
        } catch (err) {
            console.error('Failed to load conversations:', err);
        }
    };

    const startNewConversation = async () => {
        setMessages([]);
        setConversationId(null);
        setLastFailedPrompt(null);
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
                log_id: m.log_id,
                query_status: m.query_status,
            }));
            setMessages(msgs);
            setConversationId(id);
            setShowHistory(false);
            setLastFailedPrompt(null);
        } catch (err) {
            console.error('Failed to load conversation:', err);
        }
    };

    const persistMessage = async (msg: Message) => {
        if (!conversationId) return;
        try {
            await endpoints.conversations.addMessage(conversationId, {
                role: msg.role,
                content: msg.content,
                type: msg.type,
                sql_query: msg.sql_query,
                explanation: msg.explanation,
                log_id: msg.log_id,
                query_status: msg.query_status,
            });
        } catch (err) {
            console.error('Failed to persist message:', err);
        }
    };

    const handleRetry = () => {
        if (lastFailedPrompt) {
            handleAsk(lastFailedPrompt);
        }
    };

    const handleUndo = () => {
        if (messages.length < 2) return;
        // Remove last AI + user pair
        setMessages(prev => prev.slice(0, -2));
        setLastFailedPrompt(null);
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
        const userMsg: Message = { role: 'user', content: queryPrompt };
        setMessages(prev => [...prev, userMsg]);
        setPrompt('');
        setLoading(true);
        setLastFailedPrompt(null);

        // Persist user message
        if (currentConvId) {
            persistMessage({ ...userMsg, type: 'text' });
        }

        try {
            // Prepare History for Context
            const history = messages.slice(-10).map(m => ({
                role: m.role,
                content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content)
            }));

            const lastMsg = messages[messages.length - 1];
            const lastAiWasClarification = lastMsg?.role === 'ai' && lastMsg?.query_status === 'incomplete';

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
                log_id: res.data.log_id,
                corrected_prompt: res.data.corrected_prompt,
                query_status: res.data.query_status,
                pending_clarification_question: res.data.pending_clarification_question,
                previous_query_ignored: res.data.previous_query_ignored
            };

            setMessages(prev => [...prev, aiMsg]);

            // Persist AI message
            if (currentConvId) {
                persistMessage(aiMsg);
            }

            // Refresh conversation list
            loadConversations();
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
        if (!msg.log_id || msg.feedback) return;

        try {
            await endpoints.ai.feedback({
                log_id: msg.log_id,
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
                <div style={{
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
                                    color: conv.conversation_id === conversationId ? 'white' : 'var(--text-color)'
                                }}
                            >
                                <div style={{ fontWeight: 500, fontSize: '0.9rem' }}>{conv.title || 'Untitled'}</div>
                                <div style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '4px' }}>
                                    {conv.message_count} messages
                                </div>
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

                                    {msg.log_id && (
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
        </div>
    );
}
