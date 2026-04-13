import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { endpoints } from '../api';
import type { CustomerAffinityResponse, CustomerAffinityRow } from '../types/api';
import { exportToCSV } from '../utils/csv';
import {
    type MetricFilters,
    type SortState,
    ORDER_SOURCE_OPTIONS,
    buildAffinityParams,
    formatCurrency,
    formatOptionalDate,
    formatPercentage,
    getErrorMessage,
    updateEvaluationEndDate,
    updateEvaluationStartDate,
} from './customerAnalyticsShared';
import { CustomerLink } from './CustomerLink';
import { DateSelector } from './DateSelector';
import { KPICard } from './KPICard';
import { LoadingSpinner } from './LoadingSpinner';
import { ResizableTableWrapper } from './ResizableTableWrapper';
import { Select } from './Select';

interface AffinityViewProps {
    lastDbSync?: number;
    sort: SortState;
    filters: MetricFilters;
    setFilters: Dispatch<SetStateAction<MetricFilters>>;
}

function segmentClass(segment: string): string {
    if (segment === 'Repeat') return 'status-returning';
    if (segment === 'Lapsed') return 'status-lapsed';
    return 'status-new';
}

export function AffinityView({ lastDbSync, sort, filters, setFilters }: AffinityViewProps) {
    const [data, setData] = useState<CustomerAffinityResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await endpoints.insights.customerAffinityAnalysis(
                    buildAffinityParams(filters, lastDbSync),
                );
                if (!cancelled) setData(res.data);
            } catch (e: any) {
                if (!cancelled) setError(getErrorMessage(e));
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => { cancelled = true; };
    }, [filters, lastDbSync]);

    if (loading) return <LoadingSpinner />;
    if (error) return <div className="customers-analytics-error">{error}</div>;

    const summary = data?.summary;
    const rows = sort.sortRows<CustomerAffinityRow>(data?.rows || []);
    const updateFilters = (partial: Partial<MetricFilters>) =>
        setFilters((c) => ({ ...c, ...partial }));

    const recent = summary?.recent_recency_days ?? 60;
    const dormant = summary?.dormant_recency_days ?? 365;

    return (
        <div className="customers-analytics-view">
            <div className="customers-analytics-header">
                <div>
                    <h3 className="customers-analytics-title">Customer affinity</h3>
                    <p className="customers-analytics-hint">
                        {`Among verified customers who ordered in the evaluation window, each is classified from their last order before that window: Repeat if it falls within ${recent} days before the start; New if there was no prior order or the gap is ≥${dormant} days; Lapsed in between. Aligned with common partner-dashboard “new / repeat / lapsed” splits.`}
                    </p>
                </div>
            </div>

            <div className="customers-analytics-controls">
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Evaluation Start</span>
                    <DateSelector value={filters.evaluationStartDate} displayValue={filters.evaluationStartDate} onChange={(d) => updateEvaluationStartDate(setFilters, d)} maxDate={filters.evaluationEndDate} suffix="" />
                </div>
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Evaluation End</span>
                    <DateSelector value={filters.evaluationEndDate} displayValue={filters.evaluationEndDate} onChange={(d) => updateEvaluationEndDate(setFilters, d)} minDate={filters.evaluationStartDate} suffix="" />
                </div>
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Order Source</span>
                    <Select value={filters.orderSource} onChange={(v) => updateFilters({ orderSource: v })} options={ORDER_SOURCE_OPTIONS} />
                </div>
            </div>

            <div className="customers-analytics-kpi-grid">
                <KPICard
                    title="Customers in window"
                    value={summary?.total_customers?.toLocaleString() || 0}
                    hint="Unique verified customers with at least one order in the evaluation range."
                />
                <KPICard
                    title="New"
                    value={formatPercentage(summary?.new_pct)}
                    hint={`${summary?.new_customers?.toLocaleString() ?? 0} customers — no prior order, or last prior ≥${dormant}d before evaluation start.`}
                />
                <KPICard
                    title="Repeat"
                    value={formatPercentage(summary?.repeat_pct)}
                    hint={`${summary?.repeat_customers?.toLocaleString() ?? 0} customers — last prior order within ${recent}d before evaluation start.`}
                />
                <KPICard
                    title="Lapsed"
                    value={formatPercentage(summary?.lapsed_pct)}
                    hint={`${summary?.lapsed_customers?.toLocaleString() ?? 0} customers — prior order ${recent + 1}–${dormant - 1}d before evaluation start.`}
                />
            </div>

            <div className="customers-analytics-summary">
                Evaluation: <strong>{summary?.evaluation_start_date}</strong> to <strong>{summary?.evaluation_end_date}</strong>
                {' | '}Source: <strong>{summary?.order_source_label || 'All'}</strong>
                {' | '}Rules: <strong>{recent}d / {dormant}d</strong> recency vs evaluation start
            </div>

            <ResizableTableWrapper defaultHeight={520} onExportCSV={() => exportToCSV(rows, 'customer_affinity_details')}>
                <table className="standard-table">
                    <thead>
                        <tr>
                            <th onClick={() => sort.handleSort('customer_name')}>Customer{sort.renderSortIcon('customer_name')}</th>
                            <th onClick={() => sort.handleSort('affinity_segment')}>Segment{sort.renderSortIcon('affinity_segment')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('evaluation_order_count')}>Eval orders{sort.renderSortIcon('evaluation_order_count')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('evaluation_total_spend')}>Eval spend{sort.renderSortIcon('evaluation_total_spend')}</th>
                            <th onClick={() => sort.handleSort('first_order_date')}>First eval order{sort.renderSortIcon('first_order_date')}</th>
                            <th onClick={() => sort.handleSort('last_order_date')}>Last eval order{sort.renderSortIcon('last_order_date')}</th>
                            <th onClick={() => sort.handleSort('prior_last_order_date')}>Prior last order{sort.renderSortIcon('prior_last_order_date')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('gap_days_before_eval')}>Gap (days){sort.renderSortIcon('gap_days_before_eval')}</th>
                            <th onClick={() => sort.handleSort('affinity_reason')}>Reason{sort.renderSortIcon('affinity_reason')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.length > 0 ? rows.map((row) => (
                            <tr key={String(row.customer_id)}>
                                <td><CustomerLink customerId={row.customer_id} name={row.customer_name} /></td>
                                <td><span className={segmentClass(row.affinity_segment)}>{row.affinity_segment}</span></td>
                                <td className="text-right">{row.evaluation_order_count}</td>
                                <td className="text-right">{formatCurrency(row.evaluation_total_spend)}</td>
                                <td>{row.first_order_date}</td>
                                <td>{row.last_order_date}</td>
                                <td>{formatOptionalDate(row.prior_last_order_date)}</td>
                                <td className="text-right">{row.gap_days_before_eval ?? '—'}</td>
                                <td>{row.affinity_reason}</td>
                            </tr>
                        )) : (
                            <tr><td colSpan={9} className="text-center customers-analytics-empty">No customers found for the selected filters.</td></tr>
                        )}
                    </tbody>
                </table>
            </ResizableTableWrapper>
        </div>
    );
}
