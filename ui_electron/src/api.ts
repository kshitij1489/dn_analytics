import axios from 'axios';

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
        hourlyRevenue: (days?: number[]) => api.get('/insights/hourly_revenue', {
            params: days && days.length < 7 ? { days: days.join(',') } : undefined
        }),
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

    ai: {
        chat: (data: { prompt: string, history?: any[]; last_ai_was_clarification?: boolean }) => api.post('/ai/chat', data),
        chatStream: (data: { prompt: string, history?: any[]; last_ai_was_clarification?: boolean }) =>
            fetch(`${API_BASE_URL}/ai/chat/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            }),
        feedback: (data: { log_id: string, is_positive: boolean, comment?: string }) => api.post('/ai/feedback', data),
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
            log_id?: string;
            query_status?: string;
        }) => api.post(`/conversations/${conversationId}/messages`, data),
        delete: (conversationId: string) => api.delete(`/conversations/${conversationId}`),
        deleteMessage: (conversationId: string, messageId: string) =>
            api.delete(`/conversations/${conversationId}/messages/${messageId}`),
    },

    config: {
        getAll: () => api.get('/config'),
        update: (settings: Record<string, string>) => api.post('/config', { settings }),
        verify: (type: string, settings: Record<string, string>) => api.post('/config/verify', { type, settings }),
    },

    today: {
        getSummary: () => api.get('/today/summary'),
        getMenuItems: () => api.get('/today/menu-items'),
        getCustomers: () => api.get('/today/customers'),
    },
    forecast: {
        get: () => api.get('/forecast'),
    }
};

export default api;
