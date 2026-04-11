import { Card } from '../components';

const plannedSections = [
    {
        title: 'Customer Profiles',
        description: 'Search customers, open a detailed profile, and review verified identity details in one place.'
    },
    {
        title: 'Address Book',
        description: 'Expose customer address data now and leave room for a richer multi-address model later.'
    },
    {
        title: 'Order History',
        description: 'Bring customer-linked orders, spend history, and order-level drill-down into the same section.'
    },
    {
        title: 'Customer Analytics',
        description: 'Move retention, top-customer, and loyalty insights into a dedicated customer analytics surface.'
    },
    {
        title: 'Similar Users',
        description: 'Placeholder for future ML or rule-based suggestions that identify likely duplicate or related users.'
    },
    {
        title: 'Merge History',
        description: 'Reserve space for merge review, undo merge, and audit history for customer identity fixes.'
    }
];

export default function Customers() {
    return (
        <div
            className="page-container"
            style={{
                padding: '20px',
                display: 'flex',
                flexDirection: 'column',
                gap: '20px'
            }}
        >
            <div
                style={{
                    background: 'linear-gradient(135deg, rgba(0, 122, 255, 0.12), rgba(0, 122, 255, 0.03))',
                    border: '1px solid rgba(0, 122, 255, 0.18)',
                    borderRadius: '18px',
                    padding: '24px'
                }}
            >
                <div
                    style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        padding: '6px 10px',
                        borderRadius: '999px',
                        background: 'rgba(0, 122, 255, 0.12)',
                        color: 'var(--accent-color)',
                        fontSize: '12px',
                        fontWeight: 700,
                        letterSpacing: '0.04em',
                        textTransform: 'uppercase',
                        marginBottom: '14px'
                    }}
                >
                    Phase 1 Placeholder
                </div>
                <h1 style={{ marginBottom: '10px' }}>Customers</h1>
                <p
                    style={{
                        margin: 0,
                        maxWidth: '820px',
                        color: 'var(--text-secondary)',
                        fontSize: '15px',
                        lineHeight: 1.6
                    }}
                >
                    This page is the future home for customer profiles, address data, customer order history,
                    analytics, similar-user suggestions, and merge workflows. Phase 1 only establishes the
                    top-level navigation and a dedicated placeholder surface.
                </p>
            </div>

            <div
                style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
                    gap: '20px'
                }}
            >
                {plannedSections.map((section) => (
                    <Card key={section.title} title={section.title} style={{ marginBottom: 0, height: '100%' }}>
                        <p
                            style={{
                                margin: 0,
                                color: 'var(--text-secondary)',
                                lineHeight: 1.6
                            }}
                        >
                            {section.description}
                        </p>
                    </Card>
                ))}
            </div>

            <Card title="Planned Merge Workflow" style={{ marginBottom: 0 }}>
                <div
                    style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                        gap: '16px'
                    }}
                >
                    <div>
                        <div style={{ fontWeight: 600, marginBottom: '6px' }}>Suggestion Engine</div>
                        <div style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                            Placeholder for a future model that proposes customers who may belong to an existing
                            verified user.
                        </div>
                    </div>
                    <div>
                        <div style={{ fontWeight: 600, marginBottom: '6px' }}>Merge Action</div>
                        <div style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                            The UI will eventually support merging duplicate users into one surviving customer ID.
                        </div>
                    </div>
                    <div>
                        <div style={{ fontWeight: 600, marginBottom: '6px' }}>Undo Merge</div>
                        <div style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                            Merge history and reversal controls will be added so mistaken merges can be undone.
                        </div>
                    </div>
                </div>
            </Card>
        </div>
    );
}
