import axios from 'axios';
import type { AppUser } from './types/api';

const API_BASE_URL = 'http://127.0.0.1:8000/api';

const api = axios.create({
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
        customerReorderRate: (params?: any) => api.get('/insights/customer/reorder_rate', { params }),
        customerLoyalty: (params?: any) => api.get('/insights/customer/loyalty', { params }),
        topCustomers: (params?: any) => api.get('/insights/customer/top', { params }),
        avgRevenueByDay: (params?: any) => api.get('/insights/avg_revenue_by_day', { params }),
        brandAwareness: (params?: any) => api.get('/insights/brand_awareness', { params }),
    },

    menu: {
        items: (params?: any) => api.get('/menu/items', { params }),
        types: () => api.get('/menu/types'),

        // New Endpoints
        itemsView: (params?: any) => api.get('/menu/items-view', { params }),
        variantsView: (params?: any) => api.get('/menu/variants-view', { params }),
        matrix: () => api.get('/menu/matrix'),
        list: () => api.get('/menu/list'),
        variantsList: () => api.get('/menu/variants/list'),

        mergeHistory: () => api.get('/menu/merge/history'),
        merge: (data: { source_id: string, target_id: string }) => api.post('/menu/merge', data),
        undoMerge: (data: { merge_id: number }) => api.post('/menu/merge/undo', data),

        remapCheck: (oid: string) => api.get(`/menu/remap/check/${oid}`),
        remap: (data: { order_item_id: string, new_menu_item_id: string, new_variant_id: string }) => api.post('/menu/remap', data),

        unverified: () => api.get('/menu/resolutions/unverified'),
        verify: (data: { menu_item_id: string, new_name?: string, new_type?: string }) => api.post('/menu/resolutions/verify', data),
    },

    orders: {
        orders: (params?: any) => api.get('/orders/view', { params }),
        items: (params?: any) => api.get('/orders/items-view', { params }),
        customers: (params?: any) => api.get('/orders/customers-view', { params }),
        restaurants: (params?: any) => api.get('/orders/restaurants-view', { params }),
        taxes: (params?: any) => api.get('/orders/taxes-view', { params }),
        discounts: (params?: any) => api.get('/orders/discounts-view', { params }),
    },

    sync: {
        run: () => api.post('/sync/run'),
        status: (jobId: string) => api.get(`/sync/status/${jobId}`),
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
            fetch(`${API_BASE_URL}/ai/chat/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            }),
        suggestions: (limit?: number) => api.get('/ai/suggestions', { params: { limit } }),
        feedback: (data: { query_id: string, is_positive: boolean, comment?: string }) => api.post('/ai/feedback', data),
        initDebug: () => api.post('/ai/debug/init'),
        getDebugLogs: () => api.get<{ entries: DebugLogEntry[] }>('/ai/debug/logs'),
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
    },

    today: {
        getSummary: () => api.get('/today/summary'),
        getMenuItems: () => api.get('/today/menu-items'),
        getCustomers: () => api.get('/today/customers'),
    },
    forecast: {
        get: () => api.get('/forecast'),
        replay: (run_date: string) => api.get('/forecast/replay', { params: { run_date } }),
    }
};

export default api;
