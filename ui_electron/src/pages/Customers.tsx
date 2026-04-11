import { useCallback, useEffect, useState } from 'react';
import { endpoints } from '../api';
import {
    CustomerAnalyticsSection,
    ErrorPopup,
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
import './Customers.css';

type CustomerSection = 'overview' | 'profiles' | 'analytics' | 'similar' | 'merge';

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
            return;
        }

        if (pageParams?.section && ['overview', 'profiles', 'analytics', 'similar', 'merge'].includes(pageParams.section)) {
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
    }, [activeSection, lastDbSync, loadMergeHistory, loadSimilarSuggestions]);

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
        <div className="page-container customers-page">
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />

            <div className="customers-hero">
                <div className="customers-hero-badge">Phase 4 Live</div>
                <h1 className="customers-hero-title">Customers</h1>
                <p className="customers-hero-copy">
                    This section now consolidates customer overview, profile search, customer analytics, address-book
                    visibility, basic duplicate suggestions, and merge audit history into one top-level workspace.
                </p>
            </div>

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
