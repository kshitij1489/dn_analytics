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
    /** Inclusive range from verified vs unverified order split (see API tooltip copy). */
    total_customers_estimate_low?: number;
    total_customers_estimate_high?: number;
    [key: string]: any;
}

export interface CustomerQuickViewData {
    total_customers_estimate_low?: number | null;
    total_customers_estimate_high?: number | null;
    current_month?: string;
    returning_current_month_customers_one_month?: number;
    returning_current_month_customers_two_month?: number;
    total_current_month_customers?: number;
    return_rate_one_month?: number;
    return_rate_two_month?: number;
    return_rate_lifetime?: number;
    retained_customers_one_month?: number;
    total_previous_one_month_customers?: number;
    retention_rate_one_month?: number;
    retained_customers_two_month?: number;
    total_previous_two_month_customers?: number;
    retention_rate_two_month?: number;
    repeat_order_rate_current_month?: number;
    repeat_order_rate_previous_month?: number;
    return_rate_current_month?: number;
    retention_rate_current_month?: number;
}

export interface CustomerReturnRateSummary {
    evaluation_start_date: string;
    evaluation_end_date: string;
    lookback_start_date?: string | null;
    lookback_end_date?: string | null;
    lookback_days?: number | null;
    min_orders_per_customer: number;
    order_sources: string[];
    order_source_label: string;
    total_customers: number;
    returning_customers: number;
    return_rate: number;
    new_customers: number;
    returning_by_repeat_orders: number;
    returning_from_lookback: number;
    returning_by_both_conditions: number;
}

export interface CustomerReturnRateRow extends Record<string, string | number> {
    customer_id: string | number;
    customer_name: string;
    evaluation_order_count: number;
    lookback_order_count: number;
    evaluation_total_spend: number;
    first_order_date: string;
    last_order_date: string;
    qualified_by_repeat_orders: number;
    qualified_by_lookback: number;
    returning_flag: number;
    returning_status: string;
    return_reason: string;
}

export interface CustomerReturnRateResponse {
    summary: CustomerReturnRateSummary;
    rows: CustomerReturnRateRow[];
}

export interface CustomerRetentionRateSummary {
    evaluation_start_date: string;
    evaluation_end_date: string;
    lookback_start_date?: string | null;
    lookback_end_date?: string | null;
    lookback_days?: number | null;
    min_orders_per_customer: number;
    order_sources: string[];
    order_source_label: string;
    total_customers: number;
    prior_cohort_size: number;
    retained_customers: number;
    retention_rate: number;
    not_retained_customers: number;
}

export interface CustomerRetentionRateRow extends Record<string, string | number | null> {
    customer_id: string | number;
    customer_name: string;
    lookback_order_count: number;
    evaluation_order_count: number;
    evaluation_total_spend: number;
    first_evaluation_order_date: string | null;
    last_evaluation_order_date: string | null;
    retained_flag: number;
    retention_status: string;
    retention_reason: string;
}

export interface CustomerRetentionRateResponse {
    summary: CustomerRetentionRateSummary;
    rows: CustomerRetentionRateRow[];
}

export interface CustomerRepeatOrderRateSummary {
    evaluation_start_date: string;
    evaluation_end_date: string;
    min_orders_per_customer: number;
    order_sources: string[];
    order_source_label: string;
    total_customers: number;
    repeat_order_customers: number;
    repeat_order_rate: number;
    single_order_customers: number;
}

export interface CustomerRepeatOrderRateRow extends Record<string, string | number> {
    customer_id: string | number;
    customer_name: string;
    evaluation_order_count: number;
    evaluation_total_spend: number;
    first_order_date: string;
    last_order_date: string;
    repeat_order_flag: number;
    repeat_order_status: string;
    repeat_order_reason: string;
}

export interface CustomerRepeatOrderRateResponse {
    summary: CustomerRepeatOrderRateSummary;
    rows: CustomerRepeatOrderRateRow[];
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

// --- Config / User Profile Types ---

export interface AppUser {
    name: string;
    employee_id: string;
    is_active?: boolean;
    created_at?: string;
}

export interface SyncDeviceIdentity {
    device_id: string;
    install_id: string;
    device_label: string;
    platform: string;
    platform_release: string;
    machine: string;
}

export interface SyncIdentityResponse {
    employee: AppUser | null;
    device: SyncDeviceIdentity;
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
