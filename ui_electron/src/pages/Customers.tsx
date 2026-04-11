import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { Card, CustomerAnalyticsSection, PaginatedDataTable, TabButton } from '../components';
import { CustomerProfile } from '../components/CustomerProfile';
import { useNavigation } from '../contexts/NavigationContext';

type CustomerSection = 'overview' | 'profiles' | 'analytics' | 'similar' | 'merge';

export default function Customers({ lastDbSync }: { lastDbSync?: number }) {
    const [activeSection, setActiveSection] = useState<CustomerSection>('overview');
    const [linkedCustomerId, setLinkedCustomerId] = useState<string | number | undefined>(undefined);
    const { pageParams, clearParams } = useNavigation();

    useEffect(() => {
        if (pageParams?.mode === 'profile' && pageParams?.customerId != null) {
            setActiveSection('profiles');
            setLinkedCustomerId(pageParams.customerId);
            clearParams();
        } else if (pageParams?.section && ['overview', 'profiles', 'analytics', 'similar', 'merge'].includes(pageParams.section)) {
            setActiveSection(pageParams.section as CustomerSection);
            clearParams();
        }
    }, [clearParams, pageParams]);

    const renderPlaceholder = (title: string, description: string) => (
        <Card title={title} style={{ marginBottom: 0 }}>
            <p style={{ margin: 0, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                {description}
            </p>
        </Card>
    );

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
                    Phase 2 Active
                </div>
                <h1 style={{ marginBottom: '10px' }}>Customers</h1>
                <p
                    style={{
                        margin: 0,
                        maxWidth: '860px',
                        color: 'var(--text-secondary)',
                        fontSize: '15px',
                        lineHeight: 1.6
                    }}
                >
                    This section now consolidates customer overview, profile search, and customer analytics into one
                    top-level workspace. Similar-user suggestions, merge review, and undo-merge remain placeholders
                    for later phases.
                </p>
            </div>

            <div className="segmented-control" style={{ width: 'fit-content', flexWrap: 'wrap' }}>
                <TabButton active={activeSection === 'overview'} onClick={() => setActiveSection('overview')} variant="segmented" size="large">
                    Overview
                </TabButton>
                <TabButton active={activeSection === 'profiles'} onClick={() => setActiveSection('profiles')} variant="segmented" size="large">
                    Profiles
                </TabButton>
                <TabButton active={activeSection === 'analytics'} onClick={() => setActiveSection('analytics')} variant="segmented" size="large">
                    Analytics
                </TabButton>
                <TabButton active={activeSection === 'similar'} onClick={() => setActiveSection('similar')} variant="segmented" size="large">
                    Similar Users
                </TabButton>
                <TabButton active={activeSection === 'merge'} onClick={() => setActiveSection('merge')} variant="segmented" size="large">
                    Merge History
                </TabButton>
            </div>

            {activeSection === 'overview' && (
                <PaginatedDataTable
                    title="Customers"
                    apiCall={endpoints.orders.customers}
                    defaultSort="last_order_date"
                    lastDbSync={lastDbSync}
                    leftContent={
                        <span style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
                            Click a customer name to open their profile.
                        </span>
                    }
                />
            )}

            {activeSection === 'profiles' && (
                <CustomerProfile
                    initialCustomerId={linkedCustomerId}
                    headerActions={
                        <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
                            Search by name, phone, or customer ID.
                        </div>
                    }
                />
            )}

            {activeSection === 'analytics' && <CustomerAnalyticsSection lastDbSync={lastDbSync} />}

            {activeSection === 'similar' && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }}>
                    {renderPlaceholder(
                        'Similarity Queue',
                        'This will become the review queue for likely duplicate or related users suggested by a future model or rules engine.'
                    )}
                    {renderPlaceholder(
                        'Comparison View',
                        'Operators will eventually compare a suggested source customer against an existing verified customer before merging.'
                    )}
                </div>
            )}

            {activeSection === 'merge' && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }}>
                    {renderPlaceholder(
                        'Merge Audit Trail',
                        'Future merge history will list which customer IDs were merged, who performed the action, and when it happened.'
                    )}
                    {renderPlaceholder(
                        'Undo Merge',
                        'This placeholder reserves space for reversing incorrect merges once merge logging and restore flows are implemented.'
                    )}
                </div>
            )}
        </div>
    );
}
