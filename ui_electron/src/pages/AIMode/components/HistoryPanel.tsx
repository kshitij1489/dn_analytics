import type { Conversation } from '../types';

interface HistoryPanelProps {
    conversations: Conversation[];
    conversationId: string | null;
    onSelect: (id: string) => void;
    onDelete: (id: string, e: React.MouseEvent) => void;
    panelRef: React.RefObject<HTMLDivElement | null>;
}

export function HistoryPanel({
    conversations,
    conversationId,
    onSelect,
    onDelete,
    panelRef
}: HistoryPanelProps) {
    return (
        <div
            ref={panelRef}
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
            }}
        >
            <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>Recent Conversations</h3>
            {conversations.length === 0 ? (
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No conversations yet</p>
            ) : (
                conversations.map(conv => (
                    <div
                        key={conv.conversation_id}
                        role="button"
                        tabIndex={0}
                        onClick={() => onSelect(conv.conversation_id)}
                        onKeyDown={e => e.key === 'Enter' && onSelect(conv.conversation_id)}
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
                            <div
                                style={{
                                    fontWeight: 500,
                                    fontSize: '0.9rem',
                                    whiteSpace: 'nowrap',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis'
                                }}
                            >
                                {conv.title || 'Untitled'}
                            </div>
                            <div style={{ fontSize: '0.75rem', opacity: 0.7, marginTop: '4px' }}>
                                {conv.message_count} messages
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={e => onDelete(conv.conversation_id, e)}
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
    );
}
