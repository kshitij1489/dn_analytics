/**
 * Fullscreen Modal Component
 * 
 * Provides a fullscreen overlay for displaying charts in fullscreen mode.
 */

interface FullscreenModalProps {
    isOpen: boolean;
    onClose: () => void;
    children: React.ReactNode;
}

export function FullscreenModal({ isOpen, onClose, children }: FullscreenModalProps) {
    if (!isOpen) return null;

    return (
        <div style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0, 0, 0, 0.95)',
            zIndex: 9999,
            padding: '20px',
            display: 'flex',
            flexDirection: 'column'
        }}>
            <button
                onClick={onClose}
                style={{
                    alignSelf: 'flex-end',
                    background: '#646cff',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '8px 16px',
                    color: 'white',
                    cursor: 'pointer',
                    marginBottom: '10px',
                    fontSize: '14px',
                    fontWeight: 'bold'
                }}
            >
                ✕ Close Fullscreen
            </button>
            <div style={{ flex: 1, overflow: 'auto' }}>
                {children}
            </div>
        </div>
    );
}
