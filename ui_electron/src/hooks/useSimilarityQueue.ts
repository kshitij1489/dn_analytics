import { useCallback, useEffect, useRef, useState } from 'react';
import { endpoints } from '../api';
import {
    type CustomerMergePreview,
    type CustomerMergeRequestPayload,
    type CustomerSimilarityCandidate,
    buildSuggestionMergeRequest,
    getApiErrorMessage,
} from '../components/customers/customerIdentity';
import type { PopupMessage } from '../components';

type SimilarityQueueMode = 'suggestions' | 'search';

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

export function useSimilarityQueue(
    activeSection: string,
    lastDbSync: number | undefined,
    setPopup: (popup: PopupMessage | null) => void,
    loadMergeHistory: () => Promise<void>,
    onMergeApplied?: () => void,
) {
    const [similarSuggestions, setSimilarSuggestions] = useState<CustomerSimilarityCandidate[]>([]);
    const [selectedSuggestion, setSelectedSuggestion] = useState<CustomerSimilarityCandidate | null>(null);
    const [mergePreview, setMergePreview] = useState<CustomerMergePreview | null>(null);
    const [activeMergeRequest, setActiveMergeRequest] = useState<CustomerMergeRequestPayload | null>(null);
    const [similarityQueueMode, setSimilarityQueueMode] = useState<SimilarityQueueMode>('suggestions');
    const [similarSearchQuery, setSimilarSearchQuery] = useState('');
    const [loadingSimilar, setLoadingSimilar] = useState(false);
    const [loadingPreview, setLoadingPreview] = useState(false);
    const [executingMerge, setExecutingMerge] = useState(false);
    const similarRequestIdRef = useRef(0);

    const clearSelectedSuggestion = useCallback(() => {
        setSelectedSuggestion(null);
        setActiveMergeRequest(null);
        setMergePreview(null);
    }, []);

    const clearSuggestionQueue = useCallback(() => {
        similarRequestIdRef.current += 1;
        setLoadingSimilar(false);
        setSimilarSuggestions([]);
        clearSelectedSuggestion();
    }, [clearSelectedSuggestion]);

    const loadSimilarSuggestions = useCallback(async (searchQuery?: string) => {
        const requestId = similarRequestIdRef.current + 1;
        similarRequestIdRef.current = requestId;
        setLoadingSimilar(true);
        try {
            const query = searchQuery?.trim() || undefined;
            const res = await endpoints.customers.similar({ limit: 20, min_score: 0.72, q: query });
            if (requestId !== similarRequestIdRef.current) {
                return;
            }

            const suggestions = Array.isArray(res.data) ? res.data : [];
            setSimilarSuggestions(suggestions);
            setSelectedSuggestion((current) => {
                if (!suggestions.length) return null;
                if (!current) return suggestions[0];
                return suggestions.find((item) =>
                    item.source_customer.customer_id === current.source_customer.customer_id &&
                    item.target_customer.customer_id === current.target_customer.customer_id
                ) || suggestions[0];
            });
        } catch (error: any) {
            if (requestId === similarRequestIdRef.current) {
                setPopup({ type: 'error', message: getApiErrorMessage(error) });
            }
        } finally {
            if (requestId === similarRequestIdRef.current) {
                setLoadingSimilar(false);
            }
        }
    }, [setPopup]);

    const refreshSimilarSuggestions = useCallback(async () => {
        if (similarityQueueMode === 'search') {
            const query = similarSearchQuery.trim();
            if (!query) {
                clearSuggestionQueue();
                return;
            }
            await loadSimilarSuggestions(query);
            return;
        }

        await loadSimilarSuggestions();
    }, [clearSuggestionQueue, loadSimilarSuggestions, similarSearchQuery, similarityQueueMode]);

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
    }, [setPopup]);

    useEffect(() => {
        if (activeSection !== 'similar') return;

        if (similarityQueueMode === 'search') {
            const query = similarSearchQuery.trim();
            if (!query) {
                clearSuggestionQueue();
                return;
            }

            const timeoutId = window.setTimeout(() => {
                void loadSimilarSuggestions(query);
            }, 250);
            return () => window.clearTimeout(timeoutId);
        }

        void loadSimilarSuggestions();
    }, [
        activeSection,
        clearSuggestionQueue,
        lastDbSync,
        loadSimilarSuggestions,
        similarSearchQuery,
        similarityQueueMode,
    ]);

    useEffect(() => {
        if (activeSection !== 'similar') return;
        if (!selectedSuggestion) {
            setActiveMergeRequest(null);
            setMergePreview(null);
            return;
        }

        const request = buildSuggestionMergeRequest(selectedSuggestion);
        if (isSameMergeRequest(activeMergeRequest, request)) return;

        void loadMergePreview(request);
    }, [activeMergeRequest, activeSection, loadMergePreview, selectedSuggestion]);

    const handleMerge = useCallback(async (markTargetVerified?: boolean) => {
        if (!activeMergeRequest || !mergePreview) {
            return;
        }

        const mergePayload = {
            source_customer_id: activeMergeRequest.source_customer_id,
            target_customer_id: activeMergeRequest.target_customer_id,
            similarity_score: activeMergeRequest.similarity_score ?? mergePreview.score ?? undefined,
            model_name: activeMergeRequest.model_name ?? mergePreview.model_name ?? undefined,
            reasons: activeMergeRequest.reasons?.length ? activeMergeRequest.reasons : mergePreview.reasons,
            mark_target_verified: markTargetVerified,
        };

        setExecutingMerge(true);
        try {
            await endpoints.customers.merge(mergePayload);
            clearSelectedSuggestion();
            await Promise.all([refreshSimilarSuggestions(), loadMergeHistory()]);
            onMergeApplied?.();
            setPopup({
                type: 'success',
                message: markTargetVerified
                    ? `Merged ${mergePreview.source_customer.name} into ${mergePreview.target_customer.name} and marked the target as verified.`
                    : `Merged ${mergePreview.source_customer.name} into ${mergePreview.target_customer.name}.`,
            });
        } catch (error: any) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setExecutingMerge(false);
        }
    }, [activeMergeRequest, clearSelectedSuggestion, loadMergeHistory, mergePreview, onMergeApplied, refreshSimilarSuggestions, setPopup]);

    return {
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
    };
}
