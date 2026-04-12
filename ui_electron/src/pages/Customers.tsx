import { useCallback, useEffect, useState } from 'react';
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
    getApiErrorMessage,
} from '../components/customers/customerIdentity';
import { useNavigation } from '../contexts/NavigationContext';
import { useSimilarityQueue } from '../hooks/useSimilarityQueue';
import type { CustomerQuickViewData } from '../types/api';
import { CUSTOMERS_ESTIMATE_HINT, formatCustomerEstimateRange } from '../utils/customerEstimateDisplay';
import './Customers.css';

type CustomerSection = 'overview' | 'profiles' | 'analytics' | 'similar' | 'merge';

export default function Customers({ lastDbSync }: { lastDbSync?: number }) {
    const [activeSection, setActiveSection] = useState<CustomerSection>('overview');
    const [linkedCustomerId, setLinkedCustomerId] = useState<string | number | undefined>(undefined);
    const [mergeHistory, setMergeHistory] = useState<CustomerMergeHistoryEntry[]>([]);
    const [loadingHistory, setLoadingHistory] = useState(false);
    const [undoingMergeId, setUndoingMergeId] = useState<number | null>(null);
    const [quickView, setQuickView] = useState<CustomerQuickViewData | null>(null);
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const { pageParams, clearParams } = useNavigation();

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

    const {
        similarSuggestions,
        selectedSuggestion,
        mergePreview,
        activeMergeRequest,
        similarityQueueMode,
        similarSearchQuery,
        loadingSimilar,
        loadingPreview,
        executingMerge,
        refreshSimilarSuggestions,
        handleMerge,
        setSelectedSuggestion,
        setSimilarityQueueMode,
        setSimilarSearchQuery,
    } = useSimilarityQueue(activeSection, lastDbSync, setPopup, loadMergeHistory);

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
        const loadQuickView = async () => {
            try {
                const res = await endpoints.insights.customerQuickView({ _t: lastDbSync });
                setQuickView(res.data);
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
            <div className="customers-kpi-rate-label">
                {label}
            </div>
        </div>
    );

    useEffect(() => {
        if (activeSection === 'merge') {
            void loadMergeHistory();
        }
    }, [activeSection, lastDbSync, loadMergeHistory]);

    const handleUndoMerge = useCallback(async (mergeId: number) => {
        setUndoingMergeId(mergeId);
        try {
            await endpoints.customers.undoMerge({ merge_id: mergeId });
            await Promise.all([refreshSimilarSuggestions(), loadMergeHistory()]);
            setPopup({ type: 'success', message: `Undo complete for merge #${mergeId}.` });
        } catch (error: any) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUndoingMergeId(null);
        }
    }, [loadMergeHistory, refreshSimilarSuggestions]);

    return (
        <div className="page-container customers-page">
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />

            <div className="customers-kpis">
                <KPICard
                    title="Total Customers (est.)"
                    value={formatCustomerEstimateRange(quickView)}
                    hint={CUSTOMERS_ESTIMATE_HINT}
                    valueClassName="customers-kpi-value"
                />
                <KPICard
                    title="Customer Return Rate"
                    value={renderRateValues([
                        quickView?.return_rate_one_month,
                        quickView?.return_rate_two_month,
                        quickView?.return_rate_lifetime,
                    ], 'Lookback Period: 1M | 2M | LifeTime')}
                    hint={'Share of current customers who came back.\n1st = 1 month, 2nd = 2 months, 3rd = lifetime.'}
                    valueClassName="customers-kpi-value"
                />
                <KPICard
                    title="Customer Retention Rate"
                    value={renderRateValues([
                        quickView?.retention_rate_one_month,
                        quickView?.retention_rate_two_month,
                    ], 'Lookback Period: 1M | 2M')}
                    hint={'Share of past customers who returned this month.\n1st = last month, 2nd = last 2 months.'}
                    valueClassName="customers-kpi-value"
                />
                <KPICard
                    title="Repeat Order Rate"
                    value={renderRateValues([
                        quickView?.repeat_order_rate_current_month,
                        quickView?.repeat_order_rate_previous_month,
                    ], 'Current Month | Previous Month')}
                    hint={'Share of customers with 2+ orders.\n1st = current month, 2nd = previous month.'}
                    valueClassName="customers-kpi-value"
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
                    mergePreview={mergePreview}
                    queueMode={similarityQueueMode}
                    searchQuery={similarSearchQuery}
                    selectedSuggestion={selectedSuggestion}
                    similarSuggestions={similarSuggestions}
                    onMerge={handleMerge}
                    onModeChange={setSimilarityQueueMode}
                    onRefresh={refreshSimilarSuggestions}
                    onSearchQueryChange={setSimilarSearchQuery}
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
