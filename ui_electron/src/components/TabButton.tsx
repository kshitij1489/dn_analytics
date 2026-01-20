/**
 * Tab Button Component
 * 
 * Styled button for tab navigation with active state.
 */

interface TabButtonProps {
    children: React.ReactNode;
    active: boolean;
    onClick: () => void;
}

export function TabButton({ children, active, onClick }: TabButtonProps) {
    return (
        <button
            onClick={onClick}
            style={{
                marginRight: '10px',
                background: active ? 'var(--accent-color)' : 'var(--button-bg)',
                color: active ? 'white' : 'var(--button-text)',
                border: active ? 'none' : '1px solid var(--border-color)',
                padding: '8px 16px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontWeight: '500',
                transition: 'all 0.2s ease'
            }}
        >
            {children}
        </button>
    );
}
