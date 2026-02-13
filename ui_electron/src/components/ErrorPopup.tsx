/**
 * ErrorPopup — Shared in-app popup for success/error/info messages.
 *
 * Replaces browser `alert()` across the app. Constrained to the window,
 * scrollable when content is long, auto-dismisses after a timeout.
 *
 * Usage:
 *   const [popup, setPopup] = useState<PopupMessage | null>(null);
 *   ...
 *   setPopup({ type: 'error', message: 'Something failed' });
 *   ...
 *   <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
 */
import { useEffect, useRef } from 'react';
import './ErrorPopup.css';

export interface PopupMessage {
    type: 'success' | 'error' | 'info';
    message: string;
}

interface ErrorPopupProps {
    popup: PopupMessage | null;
    onClose: () => void;
    /** Auto-dismiss after ms. Default 6000. Set 0 to disable. */
    autoDismissMs?: number;
}

const ICONS: Record<string, string> = {
    success: '✅',
    error: '❌',
    info: 'ℹ️',
};

export default function ErrorPopup({ popup, onClose, autoDismissMs = 6000 }: ErrorPopupProps) {
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        if (!popup) return;
        if (autoDismissMs > 0) {
            timerRef.current = setTimeout(onClose, autoDismissMs);
        }
        return () => {
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [popup, autoDismissMs, onClose]);

    if (!popup) return null;

    return (
        <div className="error-popup-backdrop" onClick={onClose}>
            <div
                className={`error-popup-card error-popup-${popup.type}`}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="error-popup-header">
                    <span className="error-popup-icon">{ICONS[popup.type]}</span>
                    <span className="error-popup-title">
                        {popup.type === 'success' ? 'Success' : popup.type === 'error' ? 'Error' : 'Info'}
                    </span>
                    <button className="error-popup-close" onClick={onClose}>✕</button>
                </div>
                <div className="error-popup-body">
                    {popup.message}
                </div>
            </div>
        </div>
    );
}
