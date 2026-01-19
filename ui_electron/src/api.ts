import axios from 'axios';

const API_BASE_URL = 'http://127.0.0.1:8000/api';

const api = axios.create({
    baseURL: API_BASE_URL,
    headers: {
        'Content-Type': 'application/json',
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
        kpis: () => api.get('/insights/kpis'),
        dailySales: () => api.get('/insights/daily_sales'),
        salesTrend: () => api.get('/insights/sales_trend'),
        categoryTrend: () => api.get('/insights/category_trend'),
        topItems: () => api.get('/insights/top_items'),
        revenueByCategory: () => api.get('/insights/revenue_by_category'),
        hourlyRevenue: () => api.get('/insights/hourly_revenue'),
        orderSource: () => api.get('/insights/order_source'),
        customerReorderRate: () => api.get('/insights/customer/reorder_rate'),
        customerLoyalty: () => api.get('/insights/customer/loyalty'),
        topCustomers: () => api.get('/insights/customer/top'),
    },

    menu: {
        items: (params?: any) => api.get('/menu/items', { params }), // params: name_search, type_choice, start_date...
        types: () => api.get('/menu/types'),
    },

    sync: {
        run: () => api.post<JobResponse>('/sync/run'),
        status: (jobId: string) => api.get<JobResponse>(`/sync/status/${jobId}`),
    },

    resolutions: {
        unclustered: () => api.get('/resolutions/unclustered'),
        merge: (data: { menu_item_id: string, target_menu_item_id: string }) => api.post('/resolutions/merge', data),
        rename: (data: { menu_item_id: string, new_name: string, new_type: string }) => api.post('/resolutions/rename', data),
        verify: (data: { menu_item_id: string }) => api.post('/resolutions/verify', data),
    },

    sql: {
        query: (query: string) => api.post('/sql/query', { query }),
    }
};

export default api;
