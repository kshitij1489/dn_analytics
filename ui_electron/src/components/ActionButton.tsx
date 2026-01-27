/**
 * Action Button Component
 * 
 * Consistent button styling with variants for different actions.
 */

interface ActionButtonProps {
    children: React.ReactNode;
    onClick?: () => void;
    variant?: 'primary' | 'secondary' | 'danger' | 'success' | 'ghost';
    size?: 'small' | 'medium' | 'large';
    disabled?: boolean;
    type?: 'button' | 'submit' | 'reset';
    className?: string;
    style?: React.CSSProperties;
}

export function ActionButton({
    children,
    onClick,
    variant = 'primary',
    size = 'medium',
    disabled = false,
    type = 'button',
    className = '',
    style = {}
}: ActionButtonProps) {
    const classes = [
        'action-button',
        `action-button-${variant}`,
        `action-button-${size}`,
        disabled ? 'action-button-disabled' : '',
        className
    ].filter(Boolean).join(' ');

    return (
        <button
            type={type}
            onClick={onClick}
            disabled={disabled}
            className={classes}
            style={style}
        >
            {children}
        </button>
    );
}
