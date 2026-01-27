/**
 * Shared chart styling constants
 * 
 * Use these constants across all chart components for consistent theming.
 */

/**
 * Theme-aware tooltip content style for Recharts Tooltip component
 * Usage: <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
 */
export const CHART_TOOLTIP_STYLE: React.CSSProperties = {
    backgroundColor: 'var(--card-bg)',
    border: '1px solid var(--border-color)',
    color: 'var(--text-color)',
    borderRadius: '6px',
    boxShadow: '0 2px 8px rgba(0,0,0,0.15)'
};
