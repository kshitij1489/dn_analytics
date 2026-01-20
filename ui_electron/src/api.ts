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
        hourlyRevenue: (params?: any) => api.get('/insights/hourly_revenue', { params }),
        orderSource: (params?: any) => api.get('/insights/order_source', { params }),
        customerReorderRate: (params?: any) => api.get('/insights/customer/reorder_rate', { params }),
        customerLoyalty: (params?: any) => api.get('/insights/customer/loyalty', { params }),
        topCustomers: (params?: any) => api.get('/insights/customer/top', { params }),
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
    }
};

export default api;
