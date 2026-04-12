import { useCallback, useEffect, useRef, useState } from 'react';
import { endpoints } from '../api';
import {
    CustomerAnalyticsSection,
    ErrorPopup,
    KPICard,
    PaginatedDataTable,
    TabButton,
    type PopupMessage,
} from '../components';
import { CustomerProfile } from '../components/CustomerProfile';
import { CustomerMergeHistorySection } from '../components/customers/CustomerMergeHistorySection';
import { CustomerSimilaritySection } from '../components/customers/CustomerSimilaritySection';
import {
    type CustomerMergeHistoryEntry,
    type CustomerMergePreview,
    type CustomerMergeRequestPayload,
    type CustomerSimilarityCandidate,
    buildSuggestionMergeRequest,
    getApiErrorMessage,
} from '../components/customers/customerIdentity';
import { useNavigation } from '../contexts/NavigationContext';
import type { CustomerQuickViewData } from '../types/api';
import { CUSTOMERS_ESTIMATE_HINT, formatCustomerEstimateRange } from '../utils/customerEstimateDisplay';
import './Customers.css';

type CustomerSection = 'overview' | 'profiles' | 'analytics' | 'similar' | 'merge';

interface TodaySummarySnapshot {
    total_customers?: number;
    returning_customer_count?: number;
}

function isSameMergeRequest(
    left: CustomerMergeRequestPayload | null,
    right: CustomerMergeRequestPayload | null,
): boolean {
    if (left === right) return true;
    if (!left || !right) return false;

    const leftReasons = left.reasons || [];
    const rightReasons = right.reasons || [];

    return left.source_customer_id === right.source_customer_id &&
        left.target_customer_id === right.target_customer_id &&
        left.similarity_score === right.similarity_score &&
        left.model_name === right.model_name &&
        leftReasons.length === rightReasons.length &&
        leftReasons.every((reason, index) => reason === rightReasons[index]);
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
    const [quickView, setQuickView] = useState<CustomerQuickViewData | null>(null);
    const [todaySummary, setTodaySummary] = useState<TodaySummarySnapshot | null>(null);
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const { pageParams, clearParams } = useNavigation();
    const activeMergeRequestRef = useRef<CustomerMergeRequestPayload | null>(null);

    useEffect(() => {
        if (pageParams?.mode === 'profile' && pageParams?.customerId != null) {
            setActiveSection('profiles');
            setLinkedCustomerId(pageParams.customerId);
            clearParams();
            return;
        }

        if (pageParams?.section && ['overview', 'profiles', 'analytics', 'similar', 'merge'].includes(pageParams.section)) {
            setActiveSection(pageParams.section as CustomerSection);
            clearParams();
        }
    }, [clearParams, pageParams]);

    useEffect(() => {
        activeMergeRequestRef.current = activeMergeRequest;
    }, [activeMergeRequest]);

    useEffect(() => {
        const loadQuickView = async () => {
            try {
                const [quickViewRes, todaySummaryRes] = await Promise.allSettled([
                    endpoints.insights.customerQuickView({ _t: lastDbSync }),
                    endpoints.today.getSummary(),
                ]);

                if (quickViewRes.status === 'fulfilled') {
                    setQuickView(quickViewRes.value.data);
                } else {
                    setPopup({ type: 'error', message: getApiErrorMessage(quickViewRes.reason) });
                }

                if (todaySummaryRes.status === 'fulfilled') {
                    setTodaySummary(todaySummaryRes.value.data);
                } else {
                    setPopup({ type: 'error', message: getApiErrorMessage(todaySummaryRes.reason) });
                }
            } catch (error: any) {
                setPopup({ type: 'error', message: getApiErrorMessage(error) });
            }
        };

        void loadQuickView();
    }, [lastDbSync]);

    const formatRate = (value?: number | null) => {
        if (value == null || Number.isNaN(value)) return '0.0%';
        return `${value.toFixed(1)}%`;
    };

    const renderRateValues = (
        values: Array<number | null | undefined>,
        label: string,
    ) => (
        <div>
            <span>
                {values.map((value, index) => (
                    <span key={index}>
                        {index > 0 && <span className="customers-kpi-separator"> | </span>}
                        <span>{formatRate(value)}</span>
                    </span>
                ))}
            </span>
            <div
                style={{
                    marginTop: '8px',
                    fontSize: '0.52em',
                    fontWeight: 500,
                    color: 'var(--text-secondary)',
                    lineHeight: 1.3,
                }}
            >
                {label}
            </div>
        </div>
    );

    const loadSimilarSuggestions = useCallback(async () => {
        setLoadingSimilar(true);
        try {
            const res = await endpoints.customers.similar({ limit: 20, min_score: 0.72 });
            const suggestions = Array.isArray(res.data) ? res.data : [];
            setSimilarSuggestions(suggestions);
            setSelectedSuggestion((current) => {
                if (!suggestions.length) return null;
                if (activeMergeRequestRef.current && !current) return null;
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
    }, []);

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

        setActiveMergeRequest((current) => (isSameMergeRequest(current, request) ? current : request));
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
            void loadSimilarSuggestions();
        }
        if (activeSection === 'merge') {
            void loadMergeHistory();
        }
    }, [activeSection, lastDbSync, loadMergeHistory, loadSimilarSuggestions]);

    useEffect(() => {
        if (activeSection !== 'similar' || !selectedSuggestion) return;

        const request = buildSuggestionMergeRequest(selectedSuggestion);
        if (isSameMergeRequest(activeMergeRequest, request)) return;

        void loadMergePreview(request);
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
        <div className="page-container customers-page">
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />

            <div className="customers-kpis">
                <KPICard
                    title="Total Customers (est.)"
                    value={formatCustomerEstimateRange(quickView)}
                    hint={CUSTOMERS_ESTIMATE_HINT}
                />
                <KPICard
                    title="Customers(Today)"
                    value={todaySummary?.total_customers?.toLocaleString() || 0}
                    hint="Customers who ordered today."
                />
                <KPICard
                    title="Returning Customers(Today)"
                    value={todaySummary?.returning_customer_count?.toLocaleString() || 0}
                    hint="Today's customers who had ordered before."
                />
                <KPICard
                    title="Customer Return Rate"
                    value={renderRateValues([
                        quickView?.return_rate_one_month,
                        quickView?.return_rate_two_month,
                        quickView?.return_rate_lifetime,
                    ], 'Lookback Period: 1M | 2M | LifeTime')}
                    hint="Share of current customers who came back.\n1st = 1 month, 2nd = 2 months, 3rd = lifetime."
                />
                <KPICard
                    title="Customer Retention Rate"
                    value={renderRateValues([
                        quickView?.retention_rate_one_month,
                        quickView?.retention_rate_two_month,
                    ], 'Lookback Period: 1M | 2M')}
                    hint="Share of past customers who returned this month.\n1st = last month, 2nd = last 2 months."
                />
                <KPICard
                    title="Repeat Order Rate"
                    value={renderRateValues([
                        quickView?.repeat_order_rate_current_month,
                        quickView?.repeat_order_rate_previous_month,
                    ], 'Current Month | Previous Month')}
                    hint="Share of customers with 2+ orders.\n1st = current month, 2nd = previous month."
                />
            </div>

            <hr className="customers-divider" />

            <div className="segmented-control customers-tabs">
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
                    leftContent={<span className="customers-overview-hint">Click a customer name to open their profile.</span>}
                />
            )}

            {activeSection === 'profiles' && (
                <CustomerProfile
                    initialCustomerId={linkedCustomerId}
                    headerActions={<div className="customers-profile-actions">Search by name, phone, or customer ID.</div>}
                />
            )}

            {activeSection === 'analytics' && <CustomerAnalyticsSection lastDbSync={lastDbSync} />}

            {activeSection === 'similar' && (
                <CustomerSimilaritySection
                    activeMergeRequest={activeMergeRequest}
                    executingMerge={executingMerge}
                    loadingPreview={loadingPreview}
                    loadingSimilar={loadingSimilar}
                    manualSourceId={manualSourceId}
                    manualTargetId={manualTargetId}
                    mergePreview={mergePreview}
                    selectedSuggestion={selectedSuggestion}
                    similarSuggestions={similarSuggestions}
                    onManualSourceChange={setManualSourceId}
                    onManualTargetChange={setManualTargetId}
                    onMerge={handleMerge}
                    onPreviewManual={handleManualPreview}
                    onRefresh={loadSimilarSuggestions}
                    onSelectSuggestion={setSelectedSuggestion}
                />
            )}

            {activeSection === 'merge' && (
                <CustomerMergeHistorySection
                    loadingHistory={loadingHistory}
                    mergeHistory={mergeHistory}
                    undoingMergeId={undoingMergeId}
                    onRefresh={loadMergeHistory}
                    onUndoMerge={handleUndoMerge}
                />
            )}
        </div>
    );
}
