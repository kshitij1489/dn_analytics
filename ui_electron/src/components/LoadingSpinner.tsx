/**
 * Loading Spinner Component
 * 
 * Consistent loading indicator with optional message.
 */

interface LoadingSpinnerProps {
    message?: string;
    size?: 'small' | 'medium' | 'large';
}

export function LoadingSpinner({
    message = 'Loading...',
    size = 'medium'
}: LoadingSpinnerProps) {
    const sizeClass = `loading-${size}`;

    return (
        <div className={`loading-container ${sizeClass}`}>
            <div className="loading-spinner" />
            {message && <span className="loading-message">{message}</span>}
        </div>
    );
}

/**
 * Simple inline loading text for immediate replacement of "Loading..." strings
 */
export function LoadingText({ message = 'Loading...' }: { message?: string }) {
    return <div className="loading-text">{message}</div>;
}
