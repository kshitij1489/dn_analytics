/**
 * API Type Definitions
 * 
 * TypeScript interfaces for API request/response types.
 * Improves type safety and provides better IDE support.
 */

// --- Common Types ---

export interface PaginatedResponse<T> {
    data: T[];
    total: number;
    page: number;
    page_size: number;
}

// --- Job/Sync Types ---

export interface JobResponse {
    job_id: string;
    status: 'running' | 'completed' | 'failed' | 'queued';
    message: string;
    progress: number;
    stats?: {
        count?: number;
        orders?: number;
        [key: string]: any;
    };
}

// --- Insights Types ---

export interface KPIData {
    total_revenue?: number;
    total_orders?: number;
    avg_order_value?: number;
    total_customers?: number;
    [key: string]: any;
}

export interface DailySalesRow {
    order_date: string;
    total_revenue: number;
    net_revenue: number;
    tax_collected: number;
    total_orders: number;
    website_revenue?: number;
    pos_revenue?: number;
    swiggy_revenue?: number;
    zomato_revenue?: number;
}

export interface SalesTrendRow {
    date: string;
    revenue: number;
    orders: number;
}

export interface CategoryTrendRow {
    date: string;
    category: string;
    revenue: number;
}

export interface TopItemRow {
    name: string;
    type: string;
    revenue: number;
    quantity: number;
    percentage: number;
}

export interface CustomerLoyaltyRow {
    month: string;
    repeat_orders: number;
    total_orders: number;
    repeat_percentage: number;
    repeat_customers: number;
    total_customers: number;
}

export interface TopCustomerRow {
    name: string;
    total_orders: number;
    total_spent: number;
    last_order_date: string;
    status: 'Returning' | 'New';
    favorite_item: string;
    fav_item_qty: number;
}

// --- Menu Types ---

export interface MenuItemRow {
    menu_item_id: string;
    name: string;
    type: string;
    total_revenue: number;
    total_sold: number;
    sold_as_item: number;
    sold_as_addon: number;
    is_active: boolean;
}

export interface VariantRow {
    variant_id: string;
    variant_name: string;
    description?: string;
    unit?: string;
    value?: number;
    is_verified: boolean;
    created_at: string;
    updated_at: string;
}

export interface MenuMatrixRow {
    name: string;
    type: string;
    variant_name: string;
    price: number;
    is_active: boolean;
    addon_eligible: boolean;
    delivery_eligible: boolean;
}

export interface MergeHistoryRow {
    merge_id: number;
    source_id: string;
    source_name: string;
    target_id: string;
    target_name: string;
    merged_at: string;
}

// --- Orders Types ---

export interface OrderRow {
    order_id: string;
    created_on: string;
    customer_name?: string;
    total: number;
    status: string;
    source: string;
}

export interface OrderItemRow {
    order_item_id: string;
    order_id: string;
    item_name: string;
    quantity: number;
    price: number;
    created_at: string;
}

export interface CustomerRow {
    customer_id: string;
    name: string;
    phone?: string;
    email?: string;
    total_orders: number;
    total_spent: number;
    last_order_date: string;
}

// --- API Response Wrappers ---

export interface TopItemsResponse {
    items: TopItemRow[];
    total_system_revenue: number;
}

export interface RevenueByCategoryResponse {
    categories: { category: string; revenue: number; percentage: number }[];
    total_system_revenue: number;
}
