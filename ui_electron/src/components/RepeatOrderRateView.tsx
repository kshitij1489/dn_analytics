import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { endpoints } from '../api';
import type {
    CustomerRepeatOrderRateResponse,
    CustomerRepeatOrderRateRow,
    CustomerRepeatOrderRateTrendResponse,
    CustomerRepeatOrderRateTrendRow,
} from '../types/api';
import { exportToCSV } from '../utils/csv';
import {
    type MetricFilters,
    type SortState,
    ORDER_SOURCE_OPTIONS,
    MIN_ORDER_OPTIONS,
    buildMetricParams,
    buildRepeatOrderTrendParams,
    formatCurrency,
    formatPercentage,
    getErrorMessage,
    updateEvaluationEndDate,
    updateEvaluationStartDate,
} from './customerAnalyticsShared';
import { CustomerAnalyticsViewToolbar, type CustomerAnalyticsTableViewMode } from './CustomerAnalyticsViewToolbar';
import { CustomerLink } from './CustomerLink';
import { DateSelector } from './DateSelector';
import { KPICard } from './KPICard';
import { LoadingSpinner } from './LoadingSpinner';
import { ResizableTableWrapper } from './ResizableTableWrapper';
import { Select } from './Select';

interface RepeatOrderRateViewProps {
    lastDbSync?: number;
    sort: SortState;
    filters: MetricFilters;
    setFilters: Dispatch<SetStateAction<MetricFilters>>;
}

export function RepeatOrderRateView({ lastDbSync, sort, filters, setFilters }: RepeatOrderRateViewProps) {
    const [data, setData] = useState<CustomerRepeatOrderRateResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [tableView, setTableView] = useState<CustomerAnalyticsTableViewMode>('summary');
    const [trend, setTrend] = useState<CustomerRepeatOrderRateTrendResponse | null>(null);
    const [trendLoading, setTrendLoading] = useState(false);
    const [trendError, setTrendError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await endpoints.insights.repeatOrderRateAnalysis(
                    buildMetricParams(filters, lastDbSync),
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

    useEffect(() => {
        if (tableView !== 'summary') {
            setTrend(null);
            setTrendError(null);
            return;
        }
        let cancelled = false;
        const load = async () => {
            setTrendLoading(true);
            setTrendError(null);
            try {
                const res = await endpoints.insights.customerRepeatOrderRateTrend(
                    buildRepeatOrderTrendParams(filters, lastDbSync),
                );
                if (!cancelled) setTrend(res.data);
            } catch (e: any) {
                if (!cancelled) setTrendError(getErrorMessage(e));
            } finally {
                if (!cancelled) setTrendLoading(false);
            }
        };
        void load();
        return () => { cancelled = true; };
    }, [tableView, filters.orderSource, filters.minOrdersPerCustomer, lastDbSync]);

    if (loading) return <LoadingSpinner />;
    if (error) return <div className="customers-analytics-error">{error}</div>;

    const summary = data?.summary;
    const rows = sort.sortRows<CustomerRepeatOrderRateRow>(data?.rows || []);
    const trendRows = sort.sortRows<CustomerRepeatOrderRateTrendRow>(trend?.rows || []);
    const updateFilters = (partial: Partial<MetricFilters>) =>
        setFilters((c) => ({ ...c, ...partial }));

    const handleExport = () => {
        if (tableView === 'summary') {
            exportToCSV(trendRows, 'repeat_order_rate_trend');
            return;
        }
        exportToCSV(rows, 'repeat_order_rate_details');
    };

    return (
        <div className="customers-analytics-view">
            <div className="customers-analytics-header">
                <div>
                    <h3 className="customers-analytics-title">Repeat Order Rate</h3>
                    <p className="customers-analytics-hint">
                        Customers in the evaluation window count as repeat-order customers when they meet the selected threshold within that same window.
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
                    <span className="customers-analytics-filter-label">Repeat Threshold</span>
                    <Select value={filters.minOrdersPerCustomer} onChange={(v) => updateFilters({ minOrdersPerCustomer: v })} options={MIN_ORDER_OPTIONS} />
                </div>
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Order Source</span>
                    <Select value={filters.orderSource} onChange={(v) => updateFilters({ orderSource: v })} options={ORDER_SOURCE_OPTIONS} />
                </div>
            </div>

            <div className="customers-analytics-kpi-grid">
                <KPICard title="Repeat Order Rate" value={formatPercentage(summary?.repeat_order_rate)} hint="Share of evaluation-window customers who met the selected repeat-order threshold." />
                <KPICard title="Repeat Customers" value={summary?.repeat_order_customers?.toLocaleString() || 0} hint="Customers who met the selected repeat-order threshold in the evaluation window." />
                <KPICard title="Evaluation Customers" value={summary?.total_customers?.toLocaleString() || 0} hint="Unique verified customers active in the selected evaluation range." />
                <KPICard title="Single-Order Customers" value={summary?.single_order_customers?.toLocaleString() || 0} hint="Evaluation-window customers who stayed below the selected repeat threshold." />
            </div>

            <div className="customers-analytics-summary">
                Evaluation: <strong>{summary?.evaluation_start_date}</strong> to <strong>{summary?.evaluation_end_date}</strong>
                {' | '}Source: <strong>{summary?.order_source_label || 'All'}</strong>
                {' | '}Condition: <strong>at least {summary?.min_orders_per_customer || 2} orders</strong>
            </div>
            {tableView === 'summary' && trend?.defaults?.horizon_note ? (
                <div className="customers-analytics-subnote">{trend.defaults.horizon_note}</div>
            ) : null}

            <ResizableTableWrapper
                defaultHeight={520}
                leftContent={<CustomerAnalyticsViewToolbar view={tableView} onViewChange={setTableView} />}
                onExportCSV={handleExport}
            >
                {tableView === 'customerList' ? (
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th onClick={() => sort.handleSort('customer_name')}>Customer{sort.renderSortIcon('customer_name')}</th>
                                <th onClick={() => sort.handleSort('repeat_order_flag')}>Status{sort.renderSortIcon('repeat_order_flag')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('evaluation_order_count')}>Eval Orders{sort.renderSortIcon('evaluation_order_count')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('evaluation_total_spend')}>Eval Spend{sort.renderSortIcon('evaluation_total_spend')}</th>
                                <th onClick={() => sort.handleSort('first_order_date')}>First Eval Order{sort.renderSortIcon('first_order_date')}</th>
                                <th onClick={() => sort.handleSort('last_order_date')}>Last Eval Order{sort.renderSortIcon('last_order_date')}</th>
                                <th onClick={() => sort.handleSort('repeat_order_reason')}>Reason{sort.renderSortIcon('repeat_order_reason')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.length > 0 ? rows.map((row) => (
                                <tr key={String(row.customer_id)}>
                                    <td><CustomerLink customerId={row.customer_id} name={row.customer_name} /></td>
                                    <td><span className={row.repeat_order_flag ? 'status-returning' : 'status-new'}>{row.repeat_order_status}</span></td>
                                    <td className="text-right">{row.evaluation_order_count}</td>
                                    <td className="text-right">{formatCurrency(row.evaluation_total_spend)}</td>
                                    <td>{row.first_order_date}</td>
                                    <td>{row.last_order_date}</td>
                                    <td>{row.repeat_order_reason}</td>
                                </tr>
                            )) : (
                                <tr><td colSpan={7} className="text-center customers-analytics-empty">No customers found for the selected filters.</td></tr>
                            )}
                        </tbody>
                    </table>
                ) : trendError ? (
                    <div className="customers-analytics-error">{trendError}</div>
                ) : trendLoading ? (
                    <LoadingSpinner />
                ) : (
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th onClick={() => sort.handleSort('month')}>Month{sort.renderSortIcon('month')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('repeat_order_rate')}>Repeat order rate{sort.renderSortIcon('repeat_order_rate')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('repeat_order_customers')}>Repeat customers{sort.renderSortIcon('repeat_order_customers')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('evaluation_customers')}>Evaluation customers{sort.renderSortIcon('evaluation_customers')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {trendRows.length > 0 ? trendRows.map((row) => (
                                <tr key={row.month}>
                                    <td>{row.month}</td>
                                    <td className="text-right">{formatPercentage(row.repeat_order_rate)}</td>
                                    <td className="text-right">{row.repeat_order_customers}</td>
                                    <td className="text-right">{row.evaluation_customers}</td>
                                </tr>
                            )) : (
                                <tr><td colSpan={4} className="text-center customers-analytics-empty">No trend rows for this range.</td></tr>
                            )}
                        </tbody>
                    </table>
                )}
            </ResizableTableWrapper>
        </div>
    );
}
