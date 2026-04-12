import { useEffect, useRef, useState } from 'react';

/**
 * KPI Card Component
 * 
 * Displays a key performance indicator with title and value.
 */

interface KPICardProps {
    title: string;
    value: React.ReactNode;
    /** Shown in the delayed hover tooltip on the card. */
    hint?: string;
}

export function KPICard({ title, value, hint }: KPICardProps) {
    const [showTooltip, setShowTooltip] = useState(false);
    const hoverTimerRef = useRef<number | null>(null);

    useEffect(() => {
        return () => {
            if (hoverTimerRef.current != null) {
                window.clearTimeout(hoverTimerRef.current);
            }
        };
    }, []);

    const startTooltipTimer = () => {
        if (!hint) return;
        if (hoverTimerRef.current != null) {
            window.clearTimeout(hoverTimerRef.current);
        }
        hoverTimerRef.current = window.setTimeout(() => {
            setShowTooltip(true);
        }, 2000);
    };

    const hideTooltip = () => {
        if (hoverTimerRef.current != null) {
            window.clearTimeout(hoverTimerRef.current);
            hoverTimerRef.current = null;
        }
        setShowTooltip(false);
    };

    return (
        <div
            style={{
                position: 'relative',
                background: 'var(--card-bg)',
                padding: '20px',
                borderRadius: '12px',
                border: '1px solid var(--border-color)',
                boxShadow: 'var(--shadow)'
            }}
            onMouseEnter={startTooltipTimer}
            onMouseLeave={hideTooltip}
            onFocus={startTooltipTimer}
            onBlur={hideTooltip}
            tabIndex={hint ? 0 : undefined}
        >
            <h3 style={{
                margin: '0 0 10px 0',
                color: 'var(--text-secondary)',
                fontSize: '0.9em'
            }}>
                {title}
            </h3>
            <div style={{
                fontSize: '1.5em',
                fontWeight: 'bold',
                color: 'var(--accent-color)'
            }}>
                {value}
            </div>
            {hint && showTooltip && (
                <div
                    style={{
                        position: 'absolute',
                        top: 'calc(100% + 10px)',
                        left: 0,
                        zIndex: 20,
                        width: 'min(320px, calc(100vw - 40px))',
                        padding: '10px 12px',
                        borderRadius: '10px',
                        border: '1px solid var(--border-color)',
                        background: 'var(--card-bg)',
                        boxShadow: 'var(--shadow)',
                        color: 'var(--text-primary)',
                        fontSize: '0.85rem',
                        lineHeight: 1.4,
                        whiteSpace: 'pre-line',
                        pointerEvents: 'none',
                    }}
                >
                    {hint}
                </div>
            )}
        </div>
    );
}
