import { SUGGESTED_QUERIES } from '../constants';

interface EmptyStateProps {
    onAsk: (query: string) => void;
}

export function EmptyState({ onAsk }: EmptyStateProps) {
    return (
        <div
            style={{
                textAlign: 'center',
                marginTop: '100px',
                color: 'var(--text-secondary)'
            }}
        >
            <h3>How can I help you today?</h3>
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap', marginTop: '20px' }}>
                {SUGGESTED_QUERIES.map((q, idx) => (
                    <button
                        key={idx}
                        type="button"
                        onClick={() => onAsk(q)}
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
    );
}
