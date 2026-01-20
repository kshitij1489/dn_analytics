/**
 * KPI Card Component
 * 
 * Displays a key performance indicator with title and value.
 */

interface KPICardProps {
    title: string;
    value: string | number;
}

export function KPICard({ title, value }: KPICardProps) {
    return (
        <div style={{
            background: 'var(--card-bg)',
            padding: '20px',
            borderRadius: '12px',
            border: '1px solid var(--border-color)',
            boxShadow: 'var(--shadow)'
        }}>
            <h3 style={{
                margin: '0 0 10px 0',
                color: 'var(--text-secondary)',
                fontSize: '0.9em'
            }}>
                {title}
            </h3>
            <div style={{
                fontSize: '1.8em',
                fontWeight: 'bold',
                color: 'var(--accent-color)'
            }}>
                {value}
            </div>
        </div>
    );
}
