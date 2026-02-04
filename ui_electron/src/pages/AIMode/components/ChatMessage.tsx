import type { Message } from '../types';
import { AIMessageContent } from './AIMessageContent';

interface ChatMessageProps {
    msg: Message;
    index: number;
    onFeedback: (index: number, isPositive: boolean) => void;
}

export function ChatMessage({ msg, index, onFeedback }: ChatMessageProps) {
    const isUser = msg.role === 'user';

    return (
        <div
            style={{
                display: 'flex',
                justifyContent: isUser ? 'flex-end' : 'flex-start'
            }}
        >
            <div
                style={{
                    maxWidth: '85%',
                    alignSelf: isUser ? 'flex-end' : 'flex-start'
                }}
            >
                {msg.role === 'ai' && msg.previous_query_ignored && (
                    <div
                        style={{
                            fontSize: '0.8rem',
                            color: 'var(--text-secondary)',
                            fontStyle: 'italic',
                            marginBottom: '8px'
                        }}
                    >
                        Previous question was left unanswered.
                    </div>
                )}
                <div
                    className="card"
                    style={{
                        background: isUser ? 'var(--accent-color)' : 'var(--card-bg)',
                        color: isUser ? 'white' : 'var(--text-color)',
                        padding: '15px 20px',
                        borderRadius: isUser ? '20px 20px 0 20px' : '20px 20px 20px 0',
                        marginBottom: '5px',
                        boxShadow: '0 2px 10px rgba(0,0,0,0.05)'
                    }}
                >
                    {msg.role === 'ai' && msg.corrected_prompt && (
                        <div
                            style={{
                                fontSize: '0.85rem',
                                color: 'var(--text-secondary)',
                                marginBottom: '10px',
                                fontStyle: 'italic'
                            }}
                        >
                            You asked: {msg.corrected_prompt}
                        </div>
                    )}
                    {isUser ? (
                        <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>{String(msg.content)}</div>
                    ) : (
                        <AIMessageContent msg={msg} />
                    )}
                    {msg.role === 'ai' && (msg.sql_query || msg.type !== 'text') && (
                        <details style={{ marginTop: '10px', fontSize: '0.8rem', opacity: 0.8 }}>
                            <summary style={{ cursor: 'pointer' }}>View SQL Query</summary>
                            <pre
                                style={{
                                    background: 'rgba(0,0,0,0.3)',
                                    padding: '10px',
                                    borderRadius: '6px',
                                    overflowX: 'auto',
                                    marginTop: '5px'
                                }}
                            >
                                {msg.sql_query ?? 'No SQL generated'}
                            </pre>
                        </details>
                    )}
                </div>
                {msg.role === 'ai' && (
                    <div
                        style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center',
                            marginTop: '5px'
                        }}
                    >
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginLeft: '10px' }}>
                            AI Assistant
                        </div>
                        {msg.query_id && (
                            <div style={{ display: 'flex', gap: '8px', opacity: msg.feedback ? 0.6 : 1 }}>
                                <button
                                    type="button"
                                    onClick={() => onFeedback(index, true)}
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
                                    type="button"
                                    onClick={() => onFeedback(index, false)}
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
    );
}
