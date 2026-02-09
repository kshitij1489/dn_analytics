/**
 * Tab Button Component
 * 
 * Styled button for tab navigation with active state.
 */

interface TabButtonProps {
    children: React.ReactNode;
    active: boolean;
    onClick: () => void;
    variant?: 'default' | 'segmented';
    size?: 'small' | 'medium' | 'large';
}

export function TabButton({ children, active, onClick, variant = 'default', size = 'medium' }: TabButtonProps) {
    if (variant === 'segmented') {
        let className = active ? 'segmented-btn-active' : 'segmented-btn-inactive';
        if (size === 'large') className += ' segmented-btn-large';
        if (size === 'small') className += ' segmented-btn-small';

        return (
            <button onClick={onClick} className={className}>
                {children}
            </button>
        );
    }

    return (
        <button
            onClick={onClick}
            style={{
                marginRight: '10px',
                background: active ? 'var(--accent-color)' : 'var(--button-bg)',
                color: active ? 'white' : 'var(--button-text)',
                border: active ? 'none' : '1px solid var(--border-color)',
                padding: size === 'large' ? '10px 20px' : size === 'small' ? '6px 12px' : '8px 16px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontSize: size === 'large' ? '15px' : size === 'small' ? '12px' : '13px',
                fontWeight: '500',
                transition: 'all 0.2s ease'
            }}
        >
            {children}
        </button>
    );
}

