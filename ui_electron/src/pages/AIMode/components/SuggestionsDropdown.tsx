import type { Suggestion } from '../types';

interface SuggestionsDropdownProps {
    suggestions: Suggestion[];
    onSelect: (query: string) => void;
    onClose: () => void;
}

export function SuggestionsDropdown({ suggestions, onSelect, onClose }: SuggestionsDropdownProps) {
    return (
        <div
            style={{
                position: 'absolute',
                right: 0,
                top: '40px',
                width: '300px',
                background: 'var(--card-bg)',
                border: '1px solid var(--border-color)',
                borderRadius: '12px',
                padding: '16px',
                boxShadow: '0 8px 32px rgba(0,0,0,0.2)',
                zIndex: 1000,
                textAlign: 'left'
            }}
        >
            <h3 style={{ margin: '0 0 12px', fontSize: '1rem' }}>Popular Queries</h3>
            {suggestions.length === 0 ? (
                <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>No suggestions available</p>
            ) : (
                suggestions.map((s, i) => (
                    <div
                        key={i}
                        role="button"
                        tabIndex={0}
                        onClick={() => {
                            onSelect(s.query);
                            onClose();
                        }}
                        onKeyDown={e => e.key === 'Enter' && (onSelect(s.query), onClose())}
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
                        <span
                            style={{
                                fontSize: '0.75rem',
                                opacity: 0.7,
                                background: 'rgba(0,0,0,0.1)',
                                padding: '2px 6px',
                                borderRadius: '10px'
                            }}
                        >
                            {s.frequency}x
                        </span>
                    </div>
                ))
            )}
        </div>
    );
}
