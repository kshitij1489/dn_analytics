/**
 * Shared styles for AIMode components.
 * Extracted for reuse across Header, ChatInput, and other UI elements.
 */

import type React from 'react';

/**
 * Generates button styles for segmented controls.
 * Used in AIModeHeader toolbar buttons.
 */
export const segmentButtonStyle = (active: boolean): React.CSSProperties => ({
    padding: '8px 12px',
    fontSize: '0.85em',
    background: active ? 'var(--card-bg)' : 'transparent',
    color: active ? 'var(--text-color)' : 'var(--text-secondary)',
    border: 'none',
    borderRadius: '6px',
    cursor: 'pointer',
    fontWeight: active ? '600' : '500',
    transition: 'all 0.2s',
    boxShadow: active ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
    whiteSpace: 'nowrap',
    display: 'flex',
    alignItems: 'center',
    gap: '6px'
});

/**
 * Pill-style segmented control (used for AI Assistant / Telemetry toggle).
 */
export const pillButtonStyle = (active: boolean): React.CSSProperties => ({
    flex: 1,
    padding: '14px',
    background: active ? '#3B82F6' : 'transparent',
    color: active ? 'white' : 'black',
    border: 'none',
    borderRadius: '25px',
    cursor: 'pointer',
    fontWeight: active ? 600 : 500,
    transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
    boxShadow: active ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
});

/**
 * Container styles for segmented control groups.
 */
export const segmentedControlContainerStyle: React.CSSProperties = {
    display: 'flex',
    background: 'var(--input-bg)',
    padding: '4px',
    borderRadius: '8px',
    alignItems: 'center',
    gap: '2px'
};

export const pillContainerStyle: React.CSSProperties = {
    display: 'flex',
    gap: '5px',
    background: 'white',
    padding: '5px',
    borderRadius: '30px',
    boxShadow: '0 2px 10px rgba(0,0,0,0.1)',
    minWidth: '300px',
    fontFamily: 'Inter, sans-serif'
};

/**
 * Base button style for action buttons (Refresh, Export, etc.).
 * Used in TelemetryView and other components.
 */
export const actionButtonStyle: React.CSSProperties = {
    border: '1px solid transparent',
    borderRadius: '8px',
    padding: '10px 20px',
    boxSizing: 'border-box',
    color: 'white',
    cursor: 'pointer',
    fontSize: '14px',
    fontWeight: 'bold'
};
