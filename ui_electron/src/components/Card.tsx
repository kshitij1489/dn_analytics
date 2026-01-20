/**
 * Card Components
 * 
 * Reusable card containers for content grouping.
 */

import { useState } from 'react';

interface CardProps {
    children: React.ReactNode;
    title: string;
}

/**
 * Basic Card component with title
 */
export function Card({ children, title }: CardProps) {
    return (
        <div style={{
            background: 'var(--card-bg)',
            padding: '20px',
            borderRadius: '12px',
            marginBottom: '20px',
            border: '1px solid var(--border-color)',
            boxShadow: 'var(--shadow)'
        }}>
            <h3 style={{
                marginTop: 0,
                marginBottom: '15px',
                color: 'var(--accent-color)'
            }}>
                {title}
            </h3>
            {children}
        </div>
    );
}

interface CollapsibleCardProps extends CardProps {
    defaultCollapsed?: boolean;
}

/**
 * Collapsible Card component with toggle functionality
 */
export function CollapsibleCard({
    children,
    title,
    defaultCollapsed = false
}: CollapsibleCardProps) {
    const [collapsed, setCollapsed] = useState(defaultCollapsed);

    return (
        <div style={{
            background: 'var(--card-bg)',
            padding: '20px',
            borderRadius: '12px',
            marginBottom: '20px',
            border: '1px solid var(--border-color)',
            boxShadow: 'var(--shadow)'
        }}>
            <div
                onClick={() => setCollapsed(!collapsed)}
                style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    cursor: 'pointer',
                    marginBottom: collapsed ? 0 : '15px'
                }}
            >
                <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>{title}</h3>
                <span style={{ color: 'var(--text-secondary)', fontSize: '1.2em' }}>
                    {collapsed ? '+' : '−'}
                </span>
            </div>
            {!collapsed && children}
        </div>
    );
}
