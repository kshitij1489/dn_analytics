/** Hover text for KPI cards showing the customer estimate range. */
export const CUSTOMERS_ESTIMATE_HINT =
    'Estimated customer range.\nLeft = lower estimate, right = higher estimate.';

export function formatCustomerEstimateRange(kpis: {
    total_customers_estimate_low?: number | null;
    total_customers_estimate_high?: number | null;
} | null | undefined): string {
    const lo = kpis?.total_customers_estimate_low;
    const hi = kpis?.total_customers_estimate_high;
    if (lo == null && hi == null) return '—';
    const a = Number(lo ?? hi ?? 0);
    const b = Number(hi ?? lo ?? 0);
    return `${a.toLocaleString()} – ${b.toLocaleString()}`;
}
