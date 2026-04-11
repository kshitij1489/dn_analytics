import { useCallback, useEffect, useState } from 'react';
import { endpoints } from '../api';
import {
    ActionButton,
    Card,
    CustomerAnalyticsSection,
    ErrorPopup,
    PaginatedDataTable,
    TabButton,
    type PopupMessage,
} from '../components';
import { CustomerProfile } from '../components/CustomerProfile';
import { useNavigation } from '../contexts/NavigationContext';

type CustomerSection = 'overview' | 'profiles' | 'analytics' | 'similar' | 'merge';

interface CustomerSimilarityCandidatePerson {
    customer_id: string;
    name: string;
    phone?: string | null;
    address?: string | null;
    total_orders: number;
    total_spent: number;
    last_order_date?: string | null;
    is_verified: boolean;
}

interface CustomerSimilarityCandidate {
    source_customer: CustomerSimilarityCandidatePerson;
    target_customer: CustomerSimilarityCandidatePerson;
    score: number;
    model_name: string;
    reasons: string[];
    metrics: Record<string, number>;
}

interface CustomerMergePreview {
    source_customer: CustomerSimilarityCandidatePerson;
    target_customer: CustomerSimilarityCandidatePerson;
    orders_to_move: number;
    source_address_count: number;
    target_address_count: number;
    reasons: string[];
    score?: number | null;
    model_name?: string | null;
}

interface CustomerMergeRequestPayload {
    source_customer_id: string;
    target_customer_id: string;
    similarity_score?: number;
    model_name?: string;
    reasons?: string[];
}

interface CustomerMergeHistoryEntry {
    merge_id: number;
    source_customer_id: string;
    source_name?: string | null;
    target_customer_id: string;
    target_name?: string | null;
    similarity_score?: number | null;
    model_name?: string | null;
    orders_moved: number;
    copied_address_count: number;
    merged_at: string;
    undone_at?: string | null;
}

function getApiErrorMessage(error: any): string {
    return error?.response?.data?.detail || error?.message || 'Request failed.';
}

function formatDateTime(value?: string | null): string {
    if (!value) return 'Unknown';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
}

function formatCurrency(value?: number | null): string {
    return `₹${Math.round(value || 0).toLocaleString()}`;
}

function buildSuggestionMergeRequest(candidate: CustomerSimilarityCandidate): CustomerMergeRequestPayload {
    return {
        source_customer_id: candidate.source_customer.customer_id,
        target_customer_id: candidate.target_customer.customer_id,
        similarity_score: candidate.score,
        model_name: candidate.model_name,
        reasons: candidate.reasons,
    };
}

function renderCustomerMeta(customer: CustomerSimilarityCandidatePerson) {
    return (
        <div style={{ display: 'grid', gap: '8px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                <div style={{ fontWeight: 700, fontSize: '16px' }}>{customer.name}</div>
                <span
                    style={{
                        fontSize: '11px',
                        padding: '3px 8px',
                        borderRadius: '999px',
                        background: customer.is_verified ? 'rgba(16, 185, 129, 0.12)' : 'rgba(107, 114, 128, 0.12)',
                        color: customer.is_verified ? '#10B981' : '#6B7280',
                        border: customer.is_verified ? '1px solid rgba(16, 185, 129, 0.2)' : '1px solid rgba(107, 114, 128, 0.2)',
                        fontWeight: 700,
                    }}
                >
                    {customer.is_verified ? 'Verified' : 'Unverified'}
                </span>
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                Customer ID: {customer.customer_id}
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text-color)' }}>
                Phone: {customer.phone || 'No phone on file'}
            </div>
            <div style={{ fontSize: '13px', color: customer.address ? 'var(--text-color)' : 'var(--text-secondary)', lineHeight: 1.5 }}>
                Address: {customer.address || 'No saved address'}
            </div>
            <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px', color: 'var(--text-secondary)' }}>
                <span>Orders: {customer.total_orders}</span>
                <span>Spent: {formatCurrency(customer.total_spent)}</span>
                <span>Last: {customer.last_order_date ? customer.last_order_date.split('T')[0] : 'Unknown'}</span>
            </div>
        </div>
    );
}

export default function Customers({ lastDbSync }: { lastDbSync?: number }) {
    const [activeSection, setActiveSection] = useState<CustomerSection>('overview');
    const [linkedCustomerId, setLinkedCustomerId] = useState<string | number | undefined>(undefined);
    const [similarSuggestions, setSimilarSuggestions] = useState<CustomerSimilarityCandidate[]>([]);
    const [selectedSuggestion, setSelectedSuggestion] = useState<CustomerSimilarityCandidate | null>(null);
    const [mergePreview, setMergePreview] = useState<CustomerMergePreview | null>(null);
    const [activeMergeRequest, setActiveMergeRequest] = useState<CustomerMergeRequestPayload | null>(null);
    const [mergeHistory, setMergeHistory] = useState<CustomerMergeHistoryEntry[]>([]);
    const [manualSourceId, setManualSourceId] = useState('');
    const [manualTargetId, setManualTargetId] = useState('');
    const [loadingSimilar, setLoadingSimilar] = useState(false);
    const [loadingPreview, setLoadingPreview] = useState(false);
    const [loadingHistory, setLoadingHistory] = useState(false);
    const [executingMerge, setExecutingMerge] = useState(false);
    const [undoingMergeId, setUndoingMergeId] = useState<number | null>(null);
    const [popup, setPopup] = useState<PopupMessage | null>(null);
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

    const loadSimilarSuggestions = useCallback(async () => {
        setLoadingSimilar(true);
        try {
            const res = await endpoints.customers.similar({ limit: 20, min_score: 0.72 });
            const suggestions = Array.isArray(res.data) ? res.data : [];
            setSimilarSuggestions(suggestions);
            setSelectedSuggestion((current) => {
                if (!suggestions.length) return null;
                if (activeMergeRequest && !current) return null;
                if (!current) return suggestions[0];
                return suggestions.find((item) =>
                    item.source_customer.customer_id === current.source_customer.customer_id &&
                    item.target_customer.customer_id === current.target_customer.customer_id
                ) || suggestions[0];
            });
        } catch (error: any) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setLoadingSimilar(false);
        }
    }, [activeMergeRequest]);

    const loadMergeHistory = useCallback(async () => {
        setLoadingHistory(true);
        try {
            const res = await endpoints.customers.mergeHistory({ limit: 30 });
            setMergeHistory(Array.isArray(res.data) ? res.data : []);
        } catch (error: any) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setLoadingHistory(false);
        }
    }, []);

    const loadMergePreview = useCallback(async (request: CustomerMergeRequestPayload | null) => {
        if (!request) {
            setActiveMergeRequest(null);
            setMergePreview(null);
            return;
        }

        setActiveMergeRequest(request);
        setLoadingPreview(true);
        try {
            const res = await endpoints.customers.mergePreview({
                source_customer_id: request.source_customer_id,
                target_customer_id: request.target_customer_id,
            });
            setMergePreview({
                ...res.data,
                score: request.similarity_score ?? res.data?.score,
                model_name: request.model_name ?? res.data?.model_name,
                reasons: Array.isArray(res.data?.reasons) && res.data.reasons.length > 0
                    ? res.data.reasons
                    : (request.reasons || []),
            });
        } catch (error: any) {
            setActiveMergeRequest(null);
            setMergePreview(null);
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setLoadingPreview(false);
        }
    }, []);

    useEffect(() => {
        if (activeSection === 'similar') {
            loadSimilarSuggestions();
        }
        if (activeSection === 'merge') {
            loadMergeHistory();
        }
    }, [activeSection, loadMergeHistory, loadSimilarSuggestions, lastDbSync]);

    useEffect(() => {
        if (activeSection !== 'similar') {
            return;
        }
        if (selectedSuggestion) {
            loadMergePreview(buildSuggestionMergeRequest(selectedSuggestion));
            return;
        }
        if (!activeMergeRequest) {
            loadMergePreview(null);
        }
    }, [activeMergeRequest, activeSection, loadMergePreview, selectedSuggestion]);

    const handleManualPreview = useCallback(async () => {
        const sourceCustomerId = manualSourceId.trim();
        const targetCustomerId = manualTargetId.trim();
        if (!sourceCustomerId || !targetCustomerId) {
            setPopup({ type: 'error', message: 'Enter both source and target customer IDs.' });
            return;
        }
        if (sourceCustomerId === targetCustomerId) {
            setPopup({ type: 'error', message: 'Source and target customer IDs must be different.' });
            return;
        }

        setSelectedSuggestion(null);
        await loadMergePreview({
            source_customer_id: sourceCustomerId,
            target_customer_id: targetCustomerId,
            model_name: 'manual_review_v1',
            reasons: [`Manual operator-selected review for source #${sourceCustomerId} into target #${targetCustomerId}.`],
        });
    }, [loadMergePreview, manualSourceId, manualTargetId]);

    const handleMerge = useCallback(async () => {
        if (!activeMergeRequest || !mergePreview) {
            return;
        }

        const mergePayload = {
            source_customer_id: activeMergeRequest.source_customer_id,
            target_customer_id: activeMergeRequest.target_customer_id,
            similarity_score: activeMergeRequest.similarity_score ?? mergePreview.score ?? undefined,
            model_name: activeMergeRequest.model_name ?? mergePreview.model_name ?? undefined,
            reasons: activeMergeRequest.reasons?.length ? activeMergeRequest.reasons : mergePreview.reasons,
        };

        setExecutingMerge(true);
        try {
            await endpoints.customers.merge(mergePayload);
            setSelectedSuggestion(null);
            setActiveMergeRequest(null);
            setMergePreview(null);
            setManualSourceId('');
            setManualTargetId('');
            await Promise.all([loadSimilarSuggestions(), loadMergeHistory()]);
            setPopup({
                type: 'success',
                message: `Merged ${mergePreview.source_customer.name} into ${mergePreview.target_customer.name}.`,
            });
        } catch (error: any) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setExecutingMerge(false);
        }
    }, [activeMergeRequest, loadMergeHistory, loadSimilarSuggestions, mergePreview]);

    const handleUndoMerge = useCallback(async (mergeId: number) => {
        setUndoingMergeId(mergeId);
        try {
            await endpoints.customers.undoMerge({ merge_id: mergeId });
            await Promise.all([loadSimilarSuggestions(), loadMergeHistory()]);
            setPopup({ type: 'success', message: `Undo complete for merge #${mergeId}.` });
        } catch (error: any) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUndoingMergeId(null);
        }
    }, [loadMergeHistory, loadSimilarSuggestions]);

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
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />

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
                    Phase 4 Live
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
                    This section now consolidates customer overview, profile search, customer analytics, address-book
                    visibility, basic duplicate suggestions, and merge audit history into one top-level workspace.
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
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px', alignItems: 'start' }}>
                    <Card
                        title="Similarity Queue"
                        headerAction={
                            <ActionButton variant="secondary" size="small" onClick={() => loadSimilarSuggestions()} disabled={loadingSimilar}>
                                Refresh
                            </ActionButton>
                        }
                        style={{ marginBottom: 0 }}
                    >
                        <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '14px' }}>
                            Basic ML suggestions rank likely duplicate customers by name, phone, address, and order profile similarity.
                        </div>
                        <div
                            style={{
                                display: 'grid',
                                gap: '10px',
                                padding: '14px',
                                marginBottom: '16px',
                                borderRadius: '14px',
                                border: '1px solid var(--border-color)',
                                background: 'var(--bg-secondary)',
                            }}
                        >
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                                Manual Pair Review
                            </div>
                            <div style={{ color: 'var(--text-secondary)', fontSize: '13px', lineHeight: 1.5 }}>
                                Enter a source customer ID and a surviving target customer ID to preview a merge outside the suggestion queue.
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px' }}>
                                <input
                                    value={manualSourceId}
                                    onChange={(event) => setManualSourceId(event.target.value)}
                                    placeholder="Source customer ID"
                                    style={{
                                        width: '100%',
                                        padding: '10px 12px',
                                        borderRadius: '10px',
                                        border: '1px solid var(--border-color)',
                                        background: 'var(--bg-primary)',
                                        color: 'var(--text-color)',
                                    }}
                                />
                                <input
                                    value={manualTargetId}
                                    onChange={(event) => setManualTargetId(event.target.value)}
                                    placeholder="Target customer ID"
                                    style={{
                                        width: '100%',
                                        padding: '10px 12px',
                                        borderRadius: '10px',
                                        border: '1px solid var(--border-color)',
                                        background: 'var(--bg-primary)',
                                        color: 'var(--text-color)',
                                    }}
                                />
                            </div>
                            <div>
                                <ActionButton
                                    variant="secondary"
                                    size="small"
                                    onClick={() => handleManualPreview()}
                                    disabled={loadingPreview || executingMerge}
                                >
                                    {loadingPreview && !selectedSuggestion && !!activeMergeRequest ? 'Loading Preview...' : 'Preview Manual Pair'}
                                </ActionButton>
                            </div>
                        </div>
                        {loadingSimilar ? (
                            <div style={{ color: 'var(--text-secondary)' }}>Loading suggestions...</div>
                        ) : similarSuggestions.length === 0 ? (
                            <div style={{ color: 'var(--text-secondary)' }}>
                                No strong duplicate suggestions are available right now.
                            </div>
                        ) : (
                            <div style={{ display: 'grid', gap: '12px' }}>
                                {similarSuggestions.map((candidate) => {
                                    const isSelected =
                                        selectedSuggestion?.source_customer.customer_id === candidate.source_customer.customer_id &&
                                        selectedSuggestion?.target_customer.customer_id === candidate.target_customer.customer_id;

                                    return (
                                        <button
                                            key={`${candidate.source_customer.customer_id}-${candidate.target_customer.customer_id}`}
                                            type="button"
                                            onClick={() => setSelectedSuggestion(candidate)}
                                            style={{
                                                textAlign: 'left',
                                                width: '100%',
                                                border: isSelected ? '1px solid var(--accent-color)' : '1px solid var(--border-color)',
                                                borderRadius: '14px',
                                                padding: '14px',
                                                background: isSelected ? 'rgba(0, 122, 255, 0.08)' : 'var(--bg-secondary)',
                                                cursor: 'pointer',
                                                display: 'grid',
                                                gap: '8px'
                                            }}
                                        >
                                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center' }}>
                                                <div style={{ fontWeight: 700, color: 'var(--text-color)' }}>
                                                    {candidate.source_customer.name} → {candidate.target_customer.name}
                                                </div>
                                                <span
                                                    style={{
                                                        fontSize: '12px',
                                                        padding: '4px 8px',
                                                        borderRadius: '999px',
                                                        background: 'rgba(0, 122, 255, 0.12)',
                                                        color: 'var(--accent-color)',
                                                        fontWeight: 700
                                                    }}
                                                >
                                                    {(candidate.score * 100).toFixed(0)}%
                                                </span>
                                            </div>
                                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                                                Source #{candidate.source_customer.customer_id} into target #{candidate.target_customer.customer_id}
                                            </div>
                                            <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                                                {candidate.reasons.slice(0, 2).join(' · ')}
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </Card>

                    <Card
                        title="Comparison View"
                        headerAction={
                            activeMergeRequest && mergePreview ? (
                                <ActionButton
                                    variant="primary"
                                    size="small"
                                    onClick={() => handleMerge()}
                                    disabled={executingMerge || loadingPreview}
                                >
                                    {executingMerge ? 'Merging...' : selectedSuggestion ? 'Merge Selected Pair' : 'Merge Manual Pair'}
                                </ActionButton>
                            ) : null
                        }
                        style={{ marginBottom: 0 }}
                    >
                        {!activeMergeRequest ? (
                            <div style={{ color: 'var(--text-secondary)' }}>
                                Select a suggestion from the queue or preview a manual pair to review it side by side.
                            </div>
                        ) : loadingPreview || !mergePreview ? (
                            <div style={{ color: 'var(--text-secondary)' }}>Loading merge preview...</div>
                        ) : (
                            <div style={{ display: 'grid', gap: '18px' }}>
                                <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                                    <div
                                        style={{
                                            padding: '6px 10px',
                                            borderRadius: '999px',
                                            background: 'rgba(0, 122, 255, 0.12)',
                                            color: 'var(--accent-color)',
                                            fontWeight: 700,
                                            fontSize: '12px'
                                        }}
                                    >
                                        Score {(mergePreview.score || 0) * 100 >= 1 ? `${((mergePreview.score || 0) * 100).toFixed(0)}%` : 'N/A'}
                                    </div>
                                    {!selectedSuggestion && (
                                        <div
                                            style={{
                                                padding: '6px 10px',
                                                borderRadius: '999px',
                                                background: 'rgba(245, 158, 11, 0.12)',
                                                color: '#B45309',
                                                fontWeight: 700,
                                                fontSize: '12px'
                                            }}
                                        >
                                            Manual Review
                                        </div>
                                    )}
                                    <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
                                        Model: {mergePreview.model_name || 'basic_duplicate_knn_v1'}
                                    </div>
                                </div>

                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '16px' }}>
                                    <div style={{ border: '1px solid var(--border-color)', borderRadius: '14px', padding: '16px', background: 'var(--bg-secondary)' }}>
                                        <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)', marginBottom: '10px' }}>
                                            Source Customer
                                        </div>
                                        {renderCustomerMeta(mergePreview.source_customer)}
                                    </div>
                                    <div style={{ border: '1px solid var(--border-color)', borderRadius: '14px', padding: '16px', background: 'var(--bg-secondary)' }}>
                                        <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)', marginBottom: '10px' }}>
                                            Target Customer
                                        </div>
                                        {renderCustomerMeta(mergePreview.target_customer)}
                                    </div>
                                </div>

                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '12px' }}>
                                    <div style={{ border: '1px solid var(--border-color)', borderRadius: '12px', padding: '12px 14px', background: 'var(--bg-secondary)' }}>
                                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '6px' }}>Orders To Move</div>
                                        <div style={{ fontSize: '20px', fontWeight: 700 }}>{mergePreview.orders_to_move}</div>
                                    </div>
                                    <div style={{ border: '1px solid var(--border-color)', borderRadius: '12px', padding: '12px 14px', background: 'var(--bg-secondary)' }}>
                                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '6px' }}>Source Addresses</div>
                                        <div style={{ fontSize: '20px', fontWeight: 700 }}>{mergePreview.source_address_count}</div>
                                    </div>
                                    <div style={{ border: '1px solid var(--border-color)', borderRadius: '12px', padding: '12px 14px', background: 'var(--bg-secondary)' }}>
                                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', textTransform: 'uppercase', marginBottom: '6px' }}>Target Addresses</div>
                                        <div style={{ fontSize: '20px', fontWeight: 700 }}>{mergePreview.target_address_count}</div>
                                    </div>
                                </div>

                                <div>
                                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
                                        Why This Pair Was Suggested
                                    </div>
                                    {mergePreview.reasons.length > 0 ? (
                                        <div style={{ display: 'grid', gap: '8px' }}>
                                            {mergePreview.reasons.map((reason) => (
                                                <div
                                                    key={reason}
                                                    style={{
                                                        padding: '10px 12px',
                                                        borderRadius: '10px',
                                                        background: 'var(--bg-secondary)',
                                                        border: '1px solid var(--border-color)',
                                                        color: 'var(--text-color)'
                                                    }}
                                                >
                                                    {reason}
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <div style={{ color: 'var(--text-secondary)' }}>
                                            No model-based reasons were attached to this pair.
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                    </Card>
                </div>
            )}

            {activeSection === 'merge' && (
                <Card
                    title="Merge Audit Trail"
                    headerAction={
                        <ActionButton variant="secondary" size="small" onClick={() => loadMergeHistory()} disabled={loadingHistory}>
                            Refresh
                        </ActionButton>
                    }
                    style={{ marginBottom: 0 }}
                >
                    {loadingHistory ? (
                        <div style={{ color: 'var(--text-secondary)' }}>Loading merge history...</div>
                    ) : mergeHistory.length === 0 ? (
                        <div style={{ color: 'var(--text-secondary)' }}>
                            No customer merges have been recorded yet.
                        </div>
                    ) : (
                        <div style={{ display: 'grid', gap: '12px' }}>
                            {mergeHistory.map((entry) => {
                                const activeMerge = !entry.undone_at;
                                return (
                                    <div
                                        key={entry.merge_id}
                                        style={{
                                            border: '1px solid var(--border-color)',
                                            borderRadius: '14px',
                                            padding: '16px',
                                            background: 'var(--bg-secondary)',
                                            display: 'grid',
                                            gap: '10px'
                                        }}
                                    >
                                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                                            <div style={{ fontWeight: 700, color: 'var(--text-color)' }}>
                                                #{entry.merge_id}: {entry.source_name || entry.source_customer_id} → {entry.target_name || entry.target_customer_id}
                                            </div>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                                <span
                                                    style={{
                                                        fontSize: '11px',
                                                        padding: '4px 8px',
                                                        borderRadius: '999px',
                                                        background: activeMerge ? 'rgba(16, 185, 129, 0.12)' : 'rgba(107, 114, 128, 0.12)',
                                                        color: activeMerge ? '#10B981' : '#6B7280',
                                                        border: activeMerge ? '1px solid rgba(16, 185, 129, 0.2)' : '1px solid rgba(107, 114, 128, 0.2)',
                                                        fontWeight: 700
                                                    }}
                                                >
                                                    {activeMerge ? 'Active Merge' : 'Undone'}
                                                </span>
                                                {activeMerge && (
                                                    <ActionButton
                                                        variant="danger"
                                                        size="small"
                                                        onClick={() => handleUndoMerge(entry.merge_id)}
                                                        disabled={undoingMergeId === entry.merge_id}
                                                    >
                                                        {undoingMergeId === entry.merge_id ? 'Undoing...' : 'Undo'}
                                                    </ActionButton>
                                                )}
                                            </div>
                                        </div>
                                        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px', color: 'var(--text-secondary)' }}>
                                            <span>Merged: {formatDateTime(entry.merged_at)}</span>
                                            <span>Orders moved: {entry.orders_moved}</span>
                                            <span>Addresses copied: {entry.copied_address_count}</span>
                                            <span>Model: {entry.model_name || 'manual/basic'}</span>
                                            {entry.similarity_score != null && <span>Score: {(entry.similarity_score * 100).toFixed(0)}%</span>}
                                            {entry.undone_at && <span>Undone: {formatDateTime(entry.undone_at)}</span>}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </Card>
            )}
        </div>
    );
}
