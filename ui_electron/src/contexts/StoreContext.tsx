import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
    ALL_STORES_SCOPE,
    endpoints,
    isAnalyticsScopeLocked,
    setActiveAnalyticsScope,
    subscribeAnalyticsScopeLock,
    subscribeScopeCompleteness,
    type ScopeCompleteness,
} from '../api';
import type { AllStoresState, Store } from '../types/api';

interface StoreContextValue {
    stores: Store[];
    selectedStore: Store | null;
    /** 'all' is a local read-only scope over every authorized store, not a profile. */
    selectionMode: 'restaurant' | 'all' | null;
    isAllStores: boolean;
    allStores: AllStoresState;
    /** Stores an All Stores response could not read; never treated as zero. */
    completeness: ScopeCompleteness | null;
    loading: boolean;
    error: string | null;
    selectionGeneration: number;
    /** A mutation is writing to, or awaiting a central commit for, the selected profile. */
    scopeLocked: boolean;
    refreshStores: () => Promise<void>;
    selectStore: (restaurantId: string) => Promise<void>;
}

const StoreContext = createContext<StoreContextValue | undefined>(undefined);

const EMPTY_ALL_STORES: AllStoresState = { available: false, member_count: 0, members: [] };

function errorDetail(error: any): { message: string; code?: string } {
    const detail = error?.response?.data?.detail;
    if (detail && typeof detail === 'object') {
        return { message: detail.error || 'Restaurant profile request failed', code: detail.code };
    }
    return { message: typeof detail === 'string' ? detail : error?.message || 'Restaurant profile request failed' };
}

export function StoreProvider({ children }: { children: ReactNode }) {
    const [stores, setStores] = useState<Store[]>([]);
    const [selectedStore, setSelectedStore] = useState<Store | null>(null);
    const [selectionMode, setSelectionMode] = useState<'restaurant' | 'all' | null>(null);
    const [allStores, setAllStores] = useState<AllStoresState>(EMPTY_ALL_STORES);
    const [completeness, setCompleteness] = useState<ScopeCompleteness | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectionGeneration, setSelectionGeneration] = useState(0);
    const [scopeLocked, setScopeLocked] = useState(isAnalyticsScopeLocked);
    const selectionPending = useRef(false);

    const applySelection = useCallback((payload: {
        profile: Store | null;
        selection_mode?: 'restaurant' | 'all' | null;
        all_stores?: AllStoresState;
    }) => {
        const mode = payload.selection_mode ?? (payload.profile ? 'restaurant' : null);
        setSelectionMode(mode);
        setSelectedStore(payload.profile ?? null);
        setAllStores(payload.all_stores ?? EMPTY_ALL_STORES);
        setActiveAnalyticsScope(
            mode === 'all' ? ALL_STORES_SCOPE : payload.profile?.restaurant_id ?? null,
        );
    }, []);

    const loadCached = useCallback(async () => {
        const [storesResponse, selectionResponse] = await Promise.all([
            endpoints.config.getStores(),
            endpoints.config.getStoreSelection(),
        ]);
        setStores(storesResponse.data ?? []);
        applySelection(selectionResponse.data);
    }, [applySelection]);

    const refreshStores = useCallback(async () => {
        setError(null);
        try {
            const response = await endpoints.config.refreshStores();
            const refreshed = response.data.profiles ?? [];
            setStores(refreshed);
            setSelectedStore((current) => {
                if (!current) return current;
                const next = refreshed.find((store) => store.restaurant_id === current.restaurant_id);
                return next ?? current;
            });
            // Authorization changes can enable or disable the All Stores option.
            const selection = await endpoints.config.getStoreSelection();
            setAllStores(selection.data.all_stores ?? EMPTY_ALL_STORES);
        } catch (cause) {
            setError(errorDetail(cause).message);
            throw cause;
        }
    }, []);

    const selectStore = useCallback(async (restaurantId: string) => {
        if (!restaurantId) return;
        if (selectionPending.current) return;
        if (isAnalyticsScopeLocked()) {
            setError('Wait for the pending change to finish committing before switching restaurants.');
            return;
        }
        selectionPending.current = true;
        setLoading(true);
        setError(null);
        const perform = async (confirmed: boolean) => endpoints.config.selectStore(restaurantId, confirmed);
        try {
            let response;
            try {
                response = await perform(false);
            } catch (cause) {
                const detail = errorDetail(cause);
                if (detail.code !== 'restaurant_binding_confirmation_required') throw cause;
                if (!window.confirm(`${detail.message}. Continue?`)) return;
                response = await perform(true);
            }
            applySelection(response.data);
            const profile = response.data.profile;
            if (profile) {
                setStores((current) => current.map((store) => (
                    store.restaurant_id === profile.restaurant_id ? profile : store
                )));
            }
            setSelectionGeneration((generation) => generation + 1);
        } catch (cause) {
            setError(errorDetail(cause).message);
            throw cause;
        } finally {
            selectionPending.current = false;
            setLoading(false);
        }
    }, [applySelection]);

    useEffect(() => subscribeAnalyticsScopeLock(setScopeLocked), []);
    useEffect(() => subscribeScopeCompleteness(setCompleteness), []);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                await loadCached();
                try {
                    await refreshStores();
                } catch {
                    // Cached authorized/unauthorized profiles remain usable offline.
                }
            } catch (cause) {
                if (!cancelled) setError(errorDetail(cause).message);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [loadCached, refreshStores]);

    const value = useMemo(() => ({
        stores,
        selectedStore,
        selectionMode,
        isAllStores: selectionMode === 'all',
        allStores,
        completeness,
        loading,
        error,
        selectionGeneration,
        scopeLocked,
        refreshStores,
        selectStore,
    }), [
        stores, selectedStore, selectionMode, allStores, completeness, loading, error,
        selectionGeneration, scopeLocked, refreshStores, selectStore,
    ]);

    return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore() {
    const context = useContext(StoreContext);
    if (!context) throw new Error('useStore must be used within StoreProvider');
    return context;
}
