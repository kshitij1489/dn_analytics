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
    /** Previous calendar month; verified customers with ≥1 order in that month. */
    affinity_period_start?: string;
    affinity_period_end?: string;
    affinity_period_label?: string;
    affinity_customers_total?: number;
    affinity_new_customers?: number;
    affinity_repeat_customers?: number;
    affinity_lapsed_customers?: number;
    affinity_new_pct?: number;
    affinity_repeat_pct?: number;
    affinity_lapsed_pct?: number;
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

export interface CustomerAffinitySummary {
    evaluation_start_date: string;
    evaluation_end_date: string;
    order_source_label: string;
    recent_recency_days: number;
    dormant_recency_days: number;
    total_customers: number;
    new_customers: number;
    repeat_customers: number;
    lapsed_customers: number;
    new_pct: number;
    repeat_pct: number;
    lapsed_pct: number;
}

export interface CustomerAffinityRow extends Record<string, string | number | null | undefined> {
    customer_id: string | number;
    customer_name: string;
    affinity_segment: string;
    evaluation_order_count: number;
    evaluation_total_spend: number;
    first_order_date: string;
    last_order_date: string;
    prior_last_order_date: string | null;
    gap_days_before_eval: number | null;
    affinity_reason: string;
}

export interface CustomerAffinityResponse {
    summary: CustomerAffinitySummary;
    rows: CustomerAffinityRow[];
}

/** Month-level trend row (Summary table) for Customer affinity. */
export interface CustomerAffinityTrendRow extends Record<string, string | number> {
    month: string;
    evaluation_start_date: string;
    evaluation_end_date: string;
    customers_in_window: number;
    new_customers: number;
    repeat_customers: number;
    lapsed_customers: number;
    new_pct: number;
    repeat_pct: number;
    lapsed_pct: number;
}

export interface CustomerMetricTrendDefaults {
    num_months: number;
    business_date: string;
    order_source_label?: string;
    min_orders_per_customer?: number;
    horizon_note?: string;
}

export interface CustomerAffinityTrendResponse {
    rows: CustomerAffinityTrendRow[];
    defaults: CustomerMetricTrendDefaults;
}

export interface CustomerReturnRateTrendRow extends Record<string, string | number | null | undefined> {
    month: string;
    evaluation_start_date: string;
    evaluation_end_date: string;
    return_rate_30d: number;
    return_rate_60d: number;
    return_rate_lifetime: number;
    returning_customers_30d: number;
    returning_customers_60d: number;
    returning_customers_lifetime: number;
    evaluation_customers_30d: number;
    evaluation_customers_60d: number;
    evaluation_customers_lifetime: number;
    lookback_start_30d: string | null;
    lookback_end_30d: string | null;
    lookback_start_60d: string | null;
    lookback_end_60d: string | null;
    lookback_start_lifetime: string | null;
    lookback_end_lifetime: string | null;
}

export interface CustomerReturnRateTrendResponse {
    rows: CustomerReturnRateTrendRow[];
    defaults: CustomerMetricTrendDefaults;
}

export interface CustomerRetentionRateTrendRow extends Record<string, string | number | null | undefined> {
    month: string;
    evaluation_start_date: string;
    evaluation_end_date: string;
    retention_rate_30d: number;
    retention_rate_60d: number;
    retention_rate_lifetime: number;
    retained_customers_30d: number;
    retained_customers_60d: number;
    retained_customers_lifetime: number;
    prior_cohort_size_30d: number;
    prior_cohort_size_60d: number;
    prior_cohort_size_lifetime: number;
    lookback_start_30d: string | null;
    lookback_end_30d: string | null;
    lookback_start_60d: string | null;
    lookback_end_60d: string | null;
    lookback_start_lifetime: string | null;
    lookback_end_lifetime: string | null;
}

export interface CustomerRetentionRateTrendResponse {
    rows: CustomerRetentionRateTrendRow[];
    defaults: CustomerMetricTrendDefaults;
}

export interface CustomerRepeatOrderRateTrendRow extends Record<string, string | number> {
    month: string;
    evaluation_start_date: string;
    evaluation_end_date: string;
    repeat_order_rate: number;
    repeat_order_customers: number;
    evaluation_customers: number;
}

export interface CustomerRepeatOrderRateTrendResponse {
    rows: CustomerRepeatOrderRateTrendRow[];
    defaults: CustomerMetricTrendDefaults;
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

export interface Store {
    restaurant_id: string;
    display_name: string;
    timezone: string;
    database_path: string;
    authorization_state: 'authorized' | 'unauthorized';
    local_address?: string | null;
    is_bound: boolean;
    menu_group_id?: string | null;
    menu_capabilities?: string[];
    clean_rebuild_status?: 'required' | 'rebuilding' | 'complete' | null;
    last_archive_path?: string | null;
}

export interface GlobalMenuDiagnostics {
    menu_group_id?: string | null;
    bootstrap_state: 'not_started' | 'in_progress' | 'complete' | 'error';
    catalog_revision: number;
    mapping_count: number;
    price_count: number;
    assignment_coverage: { linked: number; total: number; complete: boolean };
    history_count: number;
    history_cursor?: string | null;
    quarantine_count: number;
    catalog_digest: string;
    matrix_digest: string;
    history_digest: string;
}

export interface GlobalMenuStatus {
    mode: 'legacy_restaurant_v1' | 'global_menu_v1';
    restaurant_id?: string | null;
    menu_group_id?: string | null;
    schema_version: number;
    active: boolean;
    server_advertised: boolean;
    capabilities: string[];
    aggregation_advertised: boolean;
    resolution_advertised: boolean;
    mutation_advertised: boolean;
    resolution_ready: boolean;
    mutation_ready: boolean;
    aggregation_ready: boolean;
    coverage_complete: boolean;
    coverage_linked: number;
    coverage_total: number;
    quarantine_count: number;
    catalog_revision: number;
    mutation_revision: number;
    event_cursor?: string | null;
    assignment_cursor?: string | null;
    history_cursor?: string | null;
    bootstrap_status: 'not_started' | 'in_progress' | 'complete' | 'error';
    reason: string;
    mapping_count?: number;
    price_count?: number;
    assignment_coverage?: { linked: number; total: number; complete: boolean };
    history_count?: number;
    catalog_digest?: string;
    matrix_digest?: string;
    history_digest?: string;
    diagnostics?: GlobalMenuDiagnostics;
}

export interface GlobalMenuCatalogItem {
    global_menu_item_id: string;
    canonical_name: string;
    canonical_type: string;
    active_pos_rules: number;
    is_verified: boolean;
    server_revision: number;
    updated_at: string;
}

export interface GlobalMenuCatalogVariant {
    global_variant_id: string;
    canonical_name: string;
    description?: string | null;
    unit?: string | null;
    value?: string | number | null;
    is_verified: boolean;
    server_revision: number;
    updated_at: string;
}

export interface GlobalMenuCatalogResponse {
    menu_group_id: string;
    catalog_revision: number;
    items: GlobalMenuCatalogItem[];
    variants: GlobalMenuCatalogVariant[];
}

export interface GlobalMenuPreview {
    status: 'preview';
    mutation_id: string;
    preview_digest: string;
    menu_group_id: string;
    menu_group_revision: number;
    mutation_type: string;
    payload: Record<string, unknown>;
    coverage_complete: boolean;
    conflicts: Array<Record<string, unknown>>;
    commit_allowed: boolean;
    affects_entire_menu_group: true;
    impact?: {
        restaurants?: Array<Record<string, unknown>>;
        totals?: {
            assignments?: number;
            mapping_rules?: number;
            redirects?: number;
        };
        catalog?: Record<string, unknown>;
        affected_restaurants?: number;
        existing_assignments?: number;
        mapping_rule_changes?: number;
        variant_reconciliation?: unknown;
    };
}

export interface GlobalMenuPreviewReference {
    global_mutation_id?: string;
    global_preview_digest?: string;
    global_menu_group_id?: string;
    global_preview_revision?: number;
    global_coverage_complete?: boolean;
    global_conflicts?: Array<Record<string, unknown>>;
    global_mutation_type?: string;
    global_mutation_payload?: Record<string, unknown>;
}

export interface GlobalMenuResolutionLocator {
    locator_type: 'pos_item' | 'pos_addon' | 'itemcode';
    locator_value: string;
    rule_scope: 'restaurant' | 'group';
    restaurant_id?: string | null;
    confirm_group_wide: boolean;
    price?: string;
}

export interface GlobalMenuResolutionContext {
    local_menu_item_id: string;
    local_variant_id?: string | null;
    global_item_id?: string | null;
    global_variant_id?: string | null;
    canonical_name: string;
    canonical_type: string;
    is_verified: boolean;
    variant?: {
        canonical_name: string;
        dimension: { unit: string; value?: number | null };
    } | null;
    locators: GlobalMenuResolutionLocator[];
}

/** Whether the All Stores option can be selected, and which stores it covers. */
export interface AllStoresState {
    available: boolean;
    member_count: number;
    members: string[];
}

export interface StoreSelectionResponse {
    profile: Store | null;
    selection_mode?: 'restaurant' | 'all' | null;
    all_stores?: AllStoresState;
    error?: string;
    code?: string;
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
