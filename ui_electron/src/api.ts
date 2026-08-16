import axios from 'axios';
import type {
    AppUser,
    GlobalMenuCatalogResponse,
    GlobalMenuPreview,
    GlobalMenuPreviewReference,
    GlobalMenuStatus,
    GlobalMenuResolutionContext,
    Store,
    StoreSelectionResponse,
    SyncIdentityResponse,
} from './types/api';

const API_BASE_URL = 'http://127.0.0.1:8000/api';

/** Local-only All Stores token. Never a profile identity, never sent centrally. */
export const ALL_STORES_SCOPE = '__all__';

let activeAnalyticsScope: string | null = null;
let analyticsScopeGeneration = 0;
const activeScopedFetches = new Set<AbortController>();

/** Stores an All Stores response could not read, plus stores left out of it. */
export interface ScopeCompleteness {
    scope: 'all';
    profilesRequested: number;
    profilesIncluded: number;
    incompleteProfiles: Array<{ restaurant_id: string; restaurant_name?: string; error?: string; code?: string }>;
    excludedProfiles: Array<{ restaurant_id: string; restaurant_name?: string; code?: string }>;
    stores: Array<{ restaurant_id: string; restaurant_name: string; timezone?: string }>;
    identityCoverage?: {
        global_mode_active: boolean;
        global_aggregation_active: boolean;
        linked: number;
        total: number;
        quarantine_count: number;
        linked_rows?: number;
        total_rows?: number;
        global_only?: boolean;
        omitted_unlinked_rows?: number;
    };
}

const completenessListeners = new Set<(state: ScopeCompleteness | null) => void>();

export function subscribeScopeCompleteness(listener: (state: ScopeCompleteness | null) => void) {
    completenessListeners.add(listener);
    return () => { completenessListeners.delete(listener); };
}

function publishCompleteness(state: ScopeCompleteness | null) {
    completenessListeners.forEach((listener) => listener(state));
}

/**
 * All Stores responses arrive as `{scope, profiles_*, incomplete_profiles, data}`.
 * Unwrap `data` so every page keeps reading the single-store shape, and route the
 * completeness metadata to the banner instead of dropping it: a store that could
 * not be read must never look like a store with zero sales.
 */
function unwrapFederationEnvelope(response: any) {
    const body = response?.data;
    if (!body || typeof body !== 'object' || body.scope !== 'all' || !('data' in body)) {
        return response;
    }
    publishCompleteness({
        scope: 'all',
        profilesRequested: Number(body.profiles_requested ?? 0),
        profilesIncluded: Number(body.profiles_included ?? 0),
        incompleteProfiles: body.incomplete_profiles ?? [],
        excludedProfiles: body.excluded_profiles ?? [],
        stores: body.stores ?? [],
        identityCoverage: body.identity_coverage,
    });
    response.data = body.data;
    return response;
}

export function setActiveAnalyticsScope(restaurantId: string | null) {
    if (activeAnalyticsScope !== restaurantId) {
        analyticsScopeGeneration += 1;
        activeScopedFetches.forEach((controller) => controller.abort());
        activeScopedFetches.clear();
        // Completeness belongs to the scope that produced it.
        publishCompleteness(null);
    }
    activeAnalyticsScope = restaurantId;
}

export function isAllStoresScope() {
    return activeAnalyticsScope === ALL_STORES_SCOPE;
}

export function getActiveAnalyticsScope() {
    return activeAnalyticsScope;
}

/**
 * Requests that may run while the user changes restaurants.
 *
 * Everything else that is not a GET is treated as scope-locking: it either
 * writes to the selected profile database or is awaiting a central commit for
 * it, and switching underneath would attribute the result to the wrong
 * restaurant. Defaulting to "locks" means a new mutation endpoint is covered
 * without being listed here. A Sync DB POST locks only until the backend returns
 * its captured job ID; status polling is GET/read-only, so switching is allowed
 * while the named job continues.
 */
const SCOPE_SWITCHABLE_PATHS: RegExp[] = [
    /^\/config\/stores(\/|$)/,
    /^\/ai(\/|$)/,
    /^\/conversations(\/|$)/,
    /^\/sql(\/|$)/,
    /^\/config\/?$/,
    /^\/config\/verify(\/|$)/,
    /^\/config\/users(\/|$)/,
];

// A sync status belongs to the immutable job ID captured by /sync/run, not to
// whichever restaurant is selected when a poll returns. It must remain
// deliverable after a store switch so the UI can keep naming/reporting job A
// while the user browses store B.
const SCOPE_INDEPENDENT_RESPONSE_PATHS: RegExp[] = [
    /^\/sync\/status\//,
];

function responseTracksScope(url?: string): boolean {
    const path = (url || '').split('?')[0];
    return !SCOPE_INDEPENDENT_RESPONSE_PATHS.some((pattern) => pattern.test(path));
}

let pendingScopedWrites = 0;
const scopeLockListeners = new Set<(locked: boolean) => void>();

function locksScope(method?: string, url?: string): boolean {
    if (!method || method.toLowerCase() === 'get') return false;
    const path = (url || '').split('?')[0];
    return !SCOPE_SWITCHABLE_PATHS.some((pattern) => pattern.test(path));
}

function setPendingScopedWrites(next: number) {
    const wasLocked = pendingScopedWrites > 0;
    pendingScopedWrites = Math.max(0, next);
    const isLocked = pendingScopedWrites > 0;
    if (wasLocked !== isLocked) scopeLockListeners.forEach((listener) => listener(isLocked));
}

/** True while a mutation is writing to, or awaiting a central commit for, the selected profile. */
export function isAnalyticsScopeLocked() {
    return pendingScopedWrites > 0;
}

export function subscribeAnalyticsScopeLock(listener: (locked: boolean) => void) {
    scopeLockListeners.add(listener);
    return () => { scopeLockListeners.delete(listener); };
}

/**
 * Scope-aware fetch for streaming responses.
 *
 * Axios can reject a stale response after a store switch, but the AI SSE path
 * uses fetch and keeps reading after headers arrive. Keep its AbortController
 * alive until the response body closes so switching stores stops both rendering
 * and any follow-up persistence under the newly selected profile.
 */
async function scopedStreamingFetch(input: RequestInfo | URL, init: RequestInit) {
    const requestGeneration = analyticsScopeGeneration;
    const abortController = new AbortController();
    activeScopedFetches.add(abortController);
    const cleanup = () => activeScopedFetches.delete(abortController);

    try {
        const response = await fetch(input, { ...init, signal: abortController.signal });
        if (requestGeneration !== analyticsScopeGeneration) {
            abortController.abort();
            throw new axios.CanceledError('Discarded stream from the previously selected restaurant');
        }
        if (!response.body) {
            cleanup();
            return response;
        }

        const reader = response.body.getReader();
        const guardedBody = new ReadableStream<Uint8Array>({
            async pull(controller) {
                try {
                    const { done, value } = await reader.read();
                    if (requestGeneration !== analyticsScopeGeneration) {
                        abortController.abort();
                        throw new axios.CanceledError(
                            'Discarded stream from the previously selected restaurant',
                        );
                    }
                    if (done) {
                        cleanup();
                        controller.close();
                    } else {
                        controller.enqueue(value);
                    }
                } catch (error) {
                    cleanup();
                    controller.error(error);
                }
            },
            async cancel(reason) {
                cleanup();
                abortController.abort();
                await reader.cancel(reason);
            },
        });
        return new Response(guardedBody, {
            status: response.status,
            statusText: response.statusText,
            headers: response.headers,
        });
    } catch (error) {
        cleanup();
        throw error;
    }
}

/** Exported so tests can drive the scope guards through a stub adapter. */
export const api = axios.create({
    baseURL: API_BASE_URL,
    headers: {
        'Content-Type': 'application/json',
    },
    paramsSerializer: (params) => {
        const searchParams = new URLSearchParams();
        for (const key in params) {
            const val = params[key];
            if (val !== undefined && val !== null) {
                if (Array.isArray(val)) {
                    val.forEach((v) => searchParams.append(key, v));
                } else {
                    searchParams.append(key, val);
                }
            }
        }
        return searchParams.toString();
    },
});

api.interceptors.request.use((config) => {
    (config as any).__analyticsScopeGeneration = analyticsScopeGeneration;
    if (activeAnalyticsScope) {
        config.headers.set('X-Analytics-Scope', activeAnalyticsScope);
    }
    return config;
});

api.interceptors.response.use(
    (response) => {
        if (
            responseTracksScope(response.config.url)
            && (response.config as any).__analyticsScopeGeneration !== analyticsScopeGeneration
        ) {
            throw new axios.CanceledError('Discarded response from the previously selected restaurant');
        }
        return unwrapFederationEnvelope(response);
    },
    (error) => {
        const requestGeneration = error?.config?.__analyticsScopeGeneration;
        if (
            responseTracksScope(error?.config?.url)
            && requestGeneration !== undefined
            && requestGeneration !== analyticsScopeGeneration
        ) {
            throw new axios.CanceledError('Discarded error from the previously selected restaurant');
        }
        throw error;
    },
);

// Count the lock around the whole request promise rather than in the response
// interceptors: a rejection that carries no `config` (a throw from another
// interceptor, a non-Axios error) would otherwise never decrement, and a leaked
// count disables the restaurant selector for the rest of the session.
//
// `axios.create()` returns a bound wrapper whose method helpers are pre-bound to
// an inner context, so overriding `api.request` alone would never be reached by
// `api.post(...)`. Wrap each verb instead.
function withScopeLock<A extends unknown[], R>(
    method: string,
    call: (...args: A) => Promise<R>,
): (...args: A) => Promise<R> {
    return (...args: A) => {
        if (!locksScope(method, args[0] as string)) return call(...args);
        setPendingScopedWrites(pendingScopedWrites + 1);
        return call(...args).finally(() => {
            setPendingScopedWrites(pendingScopedWrites - 1);
        });
    };
}

for (const method of ['get', 'delete', 'head', 'options', 'post', 'put', 'patch'] as const) {
    const original = (api as any)[method].bind(api);
    (api as any)[method] = withScopeLock(method, original);
}

const dispatchRequest = api.request.bind(api);
(api as any).request = (config: any) => {
    if (!locksScope(config?.method, config?.url)) return dispatchRequest(config);
    setPendingScopedWrites(pendingScopedWrites + 1);
    return dispatchRequest(config).finally(() => {
        setPendingScopedWrites(pendingScopedWrites - 1);
    });
};

/** One entry in the AI debug log (user question, cache hit/miss, LLM/cache response). */
export interface DebugLogEntry {
    step: string;
    source: 'user' | 'cache' | 'llm';
    input_preview?: string;
    output_preview?: string;
}

/** One entry in the LLM cache (for telemetry table). */
export interface LlmCacheEntry {
    key_hash: string;
    call_id: string;
    value_preview: string;
    created_at: string;
    last_used_at: string | null;
    is_incorrect: boolean;
}

/** One per-step row of the persisted AI call trace (§4 telemetry: latency + tokens per step). */
export interface CallTraceEntry {
    step: string;
    source: 'cache' | 'llm';
    model: string | null;
    latency_ms: number | null;
    prompt_tokens: number;
    completion_tokens: number;
    created_at: string;
}

/** Global LLM cache hit/miss counter per call_id (§4 telemetry: cache effectiveness). */
export interface CacheCounter {
    call_id: string;
    hits: number;
    misses: number;
}

export interface JobResponse {
    job_id: string;
    status: string;
    message: string;
    progress: number;
    stats?: any;
}

export const endpoints = {
    health: () => api.get('/health'),

    insights: {
        kpis: (params?: any) => api.get('/insights/kpis', { params }),
        dailySales: (params?: any) => api.get('/insights/daily_sales', { params }),
        salesTrend: (params?: any) => api.get('/insights/sales_trend', { params }),
        categoryTrend: (params?: any) => api.get('/insights/category_trend', { params }),
        topItems: (params?: any) => api.get('/insights/top_items', { params }),
        revenueByCategory: (params?: any) => api.get('/insights/revenue_by_category', { params }),
        hourlyRevenue: (params?: { days?: number[]; start_date?: string; end_date?: string }) => {
            const q: Record<string, string> = {};
            if (params?.days && params.days.length < 7) q.days = params.days.join(',');
            if (params?.start_date) q.start_date = params.start_date;
            if (params?.end_date) q.end_date = params.end_date;
            return api.get('/insights/hourly_revenue', { params: Object.keys(q).length ? q : undefined });
        },
        hourlyRevenueByDate: (date: string) => api.get('/insights/hourly_revenue_by_date', { params: { date } }),
        orderSource: (params?: any) => api.get('/insights/order_source', { params }),
        customerQuickView: (params?: any) => api.get('/insights/customer/quick_view', { params }),
        customerReorderRate: (params?: any) => api.get('/insights/customer/reorder_rate', { params }),
        customerReturnRateAnalysis: (params?: any) => api.get('/insights/customer/return_rate_analysis', { params }),
        customerRetentionRateAnalysis: (params?: any) => api.get('/insights/customer/retention_rate_analysis', { params }),
        repeatOrderRateAnalysis: (params?: any) => api.get('/insights/customer/repeat_order_rate_analysis', { params }),
        customerAffinityAnalysis: (params?: any) => api.get('/insights/customer/affinity_analysis', { params }),
        customerAffinityTrend: (params?: any) => api.get('/insights/customer/affinity_trend', { params }),
        customerReturnRateTrend: (params?: any) => api.get('/insights/customer/return_rate_trend', { params }),
        customerRetentionRateTrend: (params?: any) => api.get('/insights/customer/retention_rate_trend', { params }),
        customerRepeatOrderRateTrend: (params?: any) => api.get('/insights/customer/repeat_order_rate_trend', { params }),
        reorderRateTrend: (params?: any) => api.get('/insights/customer/reorder_rate_trend', { params }),
        customerLoyalty: (params?: any) => api.get('/insights/customer/loyalty', { params }),
        topCustomers: (params?: any) => api.get('/insights/customer/top', { params }),
        avgRevenueByDay: (params?: any) => api.get('/insights/avg_revenue_by_day', { params }),
        brandAwareness: (params?: any) => api.get('/insights/brand_awareness', { params }),
    },

    menu: {
        items: (params?: any) => api.get('/menu/items', { params }),
        types: () => api.get('/menu/types'),

        // New Endpoints
        summary: (params?: {
            mode?: 'volume' | 'quantity';
            as_of_date?: string;
            page?: number;
            page_size?: number;
            name_search?: string;
            sort_by?: string;
            sort_desc?: boolean;
        }) => api.get('/menu/summary', { params }),
        summaryTimeseries: (params: {
            menu_item_ids: string;
            start_date?: string;
            end_date?: string;
        }) => api.get('/menu/summary-timeseries', { params }),
        itemsView: (params?: any) => api.get('/menu/items-view', { params }),
        variantsView: (params?: any) => api.get('/menu/variants-view', { params }),
        matrix: () => api.get('/menu/matrix'),
        list: () => api.get('/menu/list'),
        variantsList: () => api.get('/menu/variants/list'),
        variantsCreate: (data: {
            variant_name: string;
            description?: string;
            unit?: string;
            value?: number;
        } & GlobalMenuPreviewReference) => api.post('/menu/variants/create', data),

        mergeHistory: (params?: {
            limit?: number;
            offset?: number;
            category?: 'all' | 'global' | 'legacy' | 'system' | 'undoable';
            restaurant_id?: string;
        }) => api.get('/menu/merge/history', { params }),
        mergePreview: (params: {
            source_id: string;
            target_id: string;
            source_variant_id?: string;
            target_variant_id?: string;
            include_global_preview?: boolean;
        }) => api.get('/menu/merge/preview', { params }),
        merge: (data: {
            source_id: string,
            target_id: string,
            variant_mappings?: Array<{
                source_variant_id: string,
                target_variant_id?: string,
                new_variant_name?: string,
            }>,
        } & GlobalMenuPreviewReference) => api.post('/menu/merge', data),
        undoMerge: (data: { merge_id: number } & GlobalMenuPreviewReference) => api.post('/menu/merge/undo', data),
        retype: (data: { menu_item_id: string, new_type: string } & GlobalMenuPreviewReference) => api.post('/menu/retype', data),
        pullMergeEventsFromCloud: (limit?: number) =>
            api.post('/menu/merge/pull-from-cloud', null, { params: { limit: limit || 100 } }),
        pullBootstrapFromCloud: (applyMode?: 'seed_only' | 'seed_and_relink_orders') =>
            api.post('/menu/bootstrap/pull-from-cloud', null, {
                params: { apply_mode: applyMode || 'seed_and_relink_orders' },
            }),

        remapCheck: (oid: string) => api.get(`/menu/remap/check/${oid}`),
        remap: (data: { order_item_id: string, new_menu_item_id: string, new_variant_id: string } & GlobalMenuPreviewReference) => api.post('/menu/remap', data),
        updateVariantMapping: (data: { menu_item_id: string, current_variant_id: string, new_variant_id: string } & GlobalMenuPreviewReference) =>
            api.post('/menu/variant-mapping/update', data),

        unverified: () => api.get('/menu/resolutions/unverified'),
        resolutionCounts: () => api.get<{
            local_unverified: number;
            globally_unlinked: number;
            mapped_verified: number;
        }>('/menu/resolutions/counts'),
        resolve: (data: {
            source_menu_item_id: string,
            source_variant_id: string,
            target_menu_item_id?: string,
            new_name?: string,
            new_type?: string,
            target_variant_id?: string,
            new_variant_name?: string,
        } & GlobalMenuPreviewReference) => api.post('/menu/resolutions/resolve', data),
        verify: (data: { menu_item_id: string, new_name?: string, new_type?: string, new_variant_id?: string } & GlobalMenuPreviewReference) => api.post('/menu/resolutions/verify', data),
        verifyAssignment: (data: {
            assignment_order_item_ids: string[];
            expected_global_menu_item_id: string;
            expected_global_variant_id: string;
            mutation_id: string;
        }) => api.post('/menu/resolutions/verify-assignment', data),
        globalStatus: () => api.get<GlobalMenuStatus>('/menu/global/status'),
        globalCatalog: () => api.get<GlobalMenuCatalogResponse>('/menu/global/catalog'),
        globalMatrix: () => api.get('/menu/global/matrix'),
        globalPreview: (data: { mutation_type: string; payload: Record<string, unknown>; mutation_id?: string }) =>
            api.post<GlobalMenuPreview>('/menu/global/mutations/preview', data),
        globalLocalPreview: (data: {
            mutation_type: string;
            source_local_menu_item_id?: string;
            source_local_variant_id?: string;
            target_local_menu_item_id?: string;
            target_local_variant_id?: string;
            details?: Record<string, unknown>;
            mutation_id?: string;
        }) => api.post<GlobalMenuPreview>('/menu/global/mutations/preview-local', data),
        globalCommit: (data: GlobalMenuPreviewReference) =>
            api.post('/menu/global/mutations/commit', data),
        globalResolutionContext: (data: {
            local_menu_item_id: string;
            local_variant_id?: string;
        }) => api.post<GlobalMenuResolutionContext>('/menu/global/resolution-context', data),
        globalMutationStatus: (mutationId: string) =>
            api.get(`/menu/global/mutations/${encodeURIComponent(mutationId)}`),
        suspectMappings: () => api.get('/menu/resolutions/suspect-mappings'),
        dismissSuspectMapping: (anomalyId: number) => api.post(`/menu/resolutions/suspect-mappings/${anomalyId}/dismiss`),
    },

    orders: {
        orders: (params?: any) => api.get('/orders/view', { params }),
        items: (params?: any) => api.get('/orders/items-view', { params }),
        addons: (params?: any) => api.get('/orders/addons-view', { params }),
        customers: (params?: any) => api.get('/orders/customers-view', { params }),
        restaurants: (params?: any) => api.get('/orders/restaurants-view', { params }),
        taxes: (params?: any) => api.get('/orders/taxes-view', { params }),
        discounts: (params?: any) => api.get('/orders/discounts-view', { params }),
    },

    customers: {
        search: (q: string) => api.get('/orders/customers/search', { params: { q } }),
        profile: (customerId: string) => api.get(`/orders/customers/${customerId}/profile`),
        similar: (params?: { limit?: number; min_score?: number; q?: string }) => api.get('/orders/customers/similar', { params }),
        mergePreview: (params: { source_customer_id: string; target_customer_id: string }) =>
            api.get('/orders/customers/merge/preview', { params }),
        mergeHistory: (params?: { limit?: number }) => api.get('/orders/customers/merge/history', { params }),
        merge: (data: {
            source_customer_id: string;
            target_customer_id: string;
            similarity_score?: number;
            model_name?: string;
            reasons?: string[];
            mark_target_verified?: boolean;
        }) => api.post('/orders/customers/merge', data),
        undoMerge: (data: { merge_id: number }) => api.post('/orders/customers/merge/undo', data),
        pullMergesFromCloud: (limit?: number) =>
            api.post('/orders/customers/merge/pull-from-cloud', null, { params: { limit: limit || 100 } }),
    },


    sync: {
        /** Pass `ALL_STORES_SCOPE` to sync every authorized store, one after another. */
        run: (restaurantId: string) => api.post('/sync/run', { restaurant_id: restaurantId }),
        status: (jobId: string) => api.get(`/sync/status/${jobId}`),
        clientLearning: () => api.post('/sync/client-learning'),
    },

    sql: {
        query: (query: string) => api.post('/sql/query', { query }),
    },

    system: {
        reset: () => api.post('/system/reset'),
    },

    resetAll: () => api.post('/system/reset'),

    ai: {
        chat: (data: { prompt: string, history?: any[]; last_ai_was_clarification?: boolean }) => api.post('/ai/chat', data),
        chatStream: (data: { prompt: string, history?: any[]; last_ai_was_clarification?: boolean }) =>
            scopedStreamingFetch(`${API_BASE_URL}/ai/chat/stream`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(activeAnalyticsScope ? { 'X-Analytics-Scope': activeAnalyticsScope } : {}),
                },
                body: JSON.stringify(data)
            }),
        suggestions: (limit?: number) => api.get('/ai/suggestions', { params: { limit } }),
        promptContext: () => api.get<{ prompt: string }>('/ai/prompt-context'),
        feedback: (data: { query_id: string, is_positive: boolean, comment?: string }) => api.post('/ai/feedback', data),
        getDebugLogs: (queryId?: string) => api.get<{ entries: DebugLogEntry[] }>('/ai/debug/logs', { params: queryId ? { query_id: queryId } : undefined }),
        getTrace: (queryId: string) => api.get<{ query_id: string; trace: CallTraceEntry[] }>(`/ai/debug/trace/${encodeURIComponent(queryId)}`),
        getCacheCounters: () => api.get<{ counters: CacheCounter[] }>('/ai/debug/cache-counters'),
        getCacheEntries: (limit?: number) => api.get<{ entries: LlmCacheEntry[] }>('/ai/debug/cache-entries', { params: limit != null ? { limit } : undefined }),
        patchCacheEntry: (keyHash: string, isIncorrect: boolean) =>
            api.patch<{ status: string; key_hash: string; is_incorrect: boolean }>(`/ai/debug/cache-entries/${encodeURIComponent(keyHash)}`, { is_incorrect: isIncorrect }),
        clearCache: () => api.post<{ status: string; message: string }>('/ai/debug/clear-cache'),
    },

    conversations: {
        create: (data?: { title?: string }) => api.post('/conversations', data || {}),
        list: (params?: { limit?: number; offset?: number }) => api.get('/conversations', { params }),
        getMessages: (conversationId: string) => api.get(`/conversations/${conversationId}`),
        addMessage: (conversationId: string, data: {
            role: string;
            content: any;
            type?: string;
            sql_query?: string;
            explanation?: string;
            query_id?: string;
            query_status?: string;
        }) => api.post(`/conversations/${conversationId}/messages`, data),
        delete: (conversationId: string) => api.delete(`/conversations/${conversationId}`),
        deleteMessage: (conversationId: string, messageId: string) =>
            api.delete(`/conversations/${conversationId}/messages/${messageId}`),
    },

    config: {
        getAll: () => api.get('/config'),
        update: (settings: Record<string, string>) => api.post('/config', { settings }),
        verify: (type: string, settings: any) => api.post('/config/verify', { type, settings }),
        resetDb: (section: string) => api.post('/config/reset-db', { section }),
        getUsers: () => api.get<AppUser[]>('/config/users'),
        saveUser: (user: AppUser) => api.post('/config/users', user),
        getSyncIdentity: () => api.get<SyncIdentityResponse>('/config/sync-identity'),
        getStores: () => api.get<Store[]>('/config/stores'),
        refreshStores: () => api.post<{ profiles: Store[] }>('/config/stores/refresh'),
        getStoreSelection: () => api.get<StoreSelectionResponse>('/config/stores/selection'),
        selectStore: (restaurantId: string, confirmExistingBinding = false) =>
            api.post<StoreSelectionResponse>('/config/stores/select', {
                restaurant_id: restaurantId,
                confirm_existing_binding: confirmExistingBinding,
            }),
    },

    today: {
        getSummary: (params?: { date?: string }) => api.get('/today/summary', { params }),
        getMenuItems: (params?: { date?: string }) => api.get('/today/menu-items', { params }),
        getCustomers: (params?: { date?: string }) => api.get('/today/customers', { params }),
        getOrders: (params?: { date?: string }) => api.get('/today/orders', { params }),
    },
    forecast: {
        get: () => api.get('/forecast'),
        items: (params?: { item_id?: string; days?: number }) => api.get('/forecast/items', { params }),
        volume: (params?: { item_id?: string; days?: number }) =>
            api.get('/forecast/volume', { params }),
    },

    petpooja: {
        backfill: (apiKey: string) => api.post('/config/petpooja-sync', { api_key: apiKey })
    }
};

export default api;
