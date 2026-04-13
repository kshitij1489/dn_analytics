import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { endpoints } from '../api';
import type {
    CustomerRetentionRateResponse,
    CustomerRetentionRateRow,
    CustomerRetentionRateTrendResponse,
    CustomerRetentionRateTrendRow,
} from '../types/api';
import { exportToCSV } from '../utils/csv';
import {
    type LookbackFilters,
    type SortState,
    ORDER_SOURCE_OPTIONS,
    RETENTION_MIN_ORDER_OPTIONS,
    buildLookbackParams,
    buildReturnRetentionTrendParams,
    formatCurrency,
    formatOptionalDate,
    formatPercentage,
    getErrorMessage,
    updateEvaluationEndDate,
    updateEvaluationStartDate,
    updateLookbackEndDate,
    updateLookbackStartDate,
} from './customerAnalyticsShared';
import { CustomerAnalyticsViewToolbar, type CustomerAnalyticsTableViewMode } from './CustomerAnalyticsViewToolbar';
import { CustomerLink } from './CustomerLink';
import { DateSelector } from './DateSelector';
import { KPICard } from './KPICard';
import { LoadingSpinner } from './LoadingSpinner';
import { ResizableTableWrapper } from './ResizableTableWrapper';
import { Select } from './Select';

interface RetentionRateViewProps {
    lastDbSync?: number;
    sort: SortState;
    filters: LookbackFilters;
    setFilters: Dispatch<SetStateAction<LookbackFilters>>;
}

export function RetentionRateView({ lastDbSync, sort, filters, setFilters }: RetentionRateViewProps) {
    const [data, setData] = useState<CustomerRetentionRateResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [tableView, setTableView] = useState<CustomerAnalyticsTableViewMode>('summary');
    const [trend, setTrend] = useState<CustomerRetentionRateTrendResponse | null>(null);
    const [trendLoading, setTrendLoading] = useState(false);
    const [trendError, setTrendError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await endpoints.insights.customerRetentionRateAnalysis(
                    buildLookbackParams(filters, lastDbSync),
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
                const res = await endpoints.insights.customerRetentionRateTrend(
                    buildReturnRetentionTrendParams(filters, lastDbSync),
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
    const rows = sort.sortRows<CustomerRetentionRateRow>(data?.rows || []);
    const trendRows = sort.sortRows<CustomerRetentionRateTrendRow>(trend?.rows || []);
    const updateFilters = (partial: Partial<LookbackFilters>) =>
        setFilters((c) => ({ ...c, ...partial }));

    const handleExport = () => {
        if (tableView === 'summary') {
            exportToCSV(trendRows, 'customer_retention_rate_trend');
            return;
        }
        exportToCSV(rows, 'customer_retention_rate_details');
    };

    return (
        <div className="customers-analytics-view">
            <div className="customers-analytics-header">
                <div>
                    <h3 className="customers-analytics-title">Customer Retention Rate</h3>
                    <p className="customers-analytics-hint">
                        Customers from the selected lookback cohort count as retained only when they place at least the selected number of orders in the evaluation window.
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
                    <span className="customers-analytics-filter-label">Lookback Start</span>
                    <DateSelector value={filters.lookbackStartDate} displayValue={filters.lookbackStartDate} onChange={(d) => updateLookbackStartDate(setFilters, d)} maxDate={filters.lookbackEndDate} suffix="" />
                </div>
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Lookback End</span>
                    <DateSelector value={filters.lookbackEndDate} displayValue={filters.lookbackEndDate} onChange={(d) => updateLookbackEndDate(setFilters, d)} minDate={filters.lookbackStartDate} suffix="" />
                </div>
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Retention Condition</span>
                    <Select value={filters.minOrdersPerCustomer} onChange={(v) => updateFilters({ minOrdersPerCustomer: v })} options={RETENTION_MIN_ORDER_OPTIONS} />
                </div>
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Order Source</span>
                    <Select value={filters.orderSource} onChange={(v) => updateFilters({ orderSource: v })} options={ORDER_SOURCE_OPTIONS} />
                </div>
            </div>

            <div className="customers-analytics-kpi-grid">
                <KPICard title="Retention Rate" value={formatPercentage(summary?.retention_rate)} hint="Share of the lookback cohort that met the selected evaluation-window threshold." />
                <KPICard title="Retained Customers" value={summary?.retained_customers?.toLocaleString() || 0} hint="Customers from the prior cohort who met the selected evaluation-window threshold." />
                <KPICard title="Prior Cohort Size" value={summary?.prior_cohort_size?.toLocaleString() || 0} hint="Unique verified customers active in the selected lookback window." />
                <KPICard title="Not Retained" value={summary?.not_retained_customers?.toLocaleString() || 0} hint="Prior-cohort customers who did not meet the selected evaluation-window threshold." />
            </div>

            <div className="customers-analytics-summary">
                Evaluation: <strong>{summary?.evaluation_start_date}</strong> to <strong>{summary?.evaluation_end_date}</strong>
                {' | '}Lookback: <strong>{summary?.lookback_start_date}</strong> to <strong>{summary?.lookback_end_date}</strong>
                {' | '}Source: <strong>{summary?.order_source_label || 'All'}</strong>
                {' | '}Condition: <strong>at least {summary?.min_orders_per_customer || 2} orders in evaluation</strong>
            </div>
            <div className="customers-analytics-subnote">
                The detail table only lists customers who were part of the selected lookback cohort.
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
                                <th onClick={() => sort.handleSort('retained_flag')}>Status{sort.renderSortIcon('retained_flag')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('lookback_order_count')}>Lookback Orders{sort.renderSortIcon('lookback_order_count')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('evaluation_order_count')}>Eval Orders{sort.renderSortIcon('evaluation_order_count')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('evaluation_total_spend')}>Eval Spend{sort.renderSortIcon('evaluation_total_spend')}</th>
                                <th onClick={() => sort.handleSort('first_evaluation_order_date')}>First Eval Order{sort.renderSortIcon('first_evaluation_order_date')}</th>
                                <th onClick={() => sort.handleSort('last_evaluation_order_date')}>Last Eval Order{sort.renderSortIcon('last_evaluation_order_date')}</th>
                                <th onClick={() => sort.handleSort('retention_reason')}>Reason{sort.renderSortIcon('retention_reason')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.length > 0 ? rows.map((row) => (
                                <tr key={String(row.customer_id)}>
                                    <td><CustomerLink customerId={row.customer_id} name={row.customer_name} /></td>
                                    <td><span className={row.retained_flag ? 'status-returning' : 'status-new'}>{row.retention_status}</span></td>
                                    <td className="text-right">{row.lookback_order_count}</td>
                                    <td className="text-right">{row.evaluation_order_count}</td>
                                    <td className="text-right">{formatCurrency(row.evaluation_total_spend)}</td>
                                    <td>{formatOptionalDate(row.first_evaluation_order_date)}</td>
                                    <td>{formatOptionalDate(row.last_evaluation_order_date)}</td>
                                    <td>{row.retention_reason}</td>
                                </tr>
                            )) : (
                                <tr><td colSpan={8} className="text-center customers-analytics-empty">No prior-cohort customers found for the selected filters.</td></tr>
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
                                <th className="text-right" onClick={() => sort.handleSort('retention_rate_30d')}>Retention 30d{sort.renderSortIcon('retention_rate_30d')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('retention_rate_60d')}>Retention 60d{sort.renderSortIcon('retention_rate_60d')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('retention_rate_lifetime')}>Retention lifetime{sort.renderSortIcon('retention_rate_lifetime')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('prior_cohort_size_30d')}>Prior cohort{sort.renderSortIcon('prior_cohort_size_30d')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {trendRows.length > 0 ? trendRows.map((row) => (
                                <tr key={row.month}>
                                    <td>{row.month}</td>
                                    <td className="text-right">{formatPercentage(row.retention_rate_30d)}</td>
                                    <td className="text-right">{formatPercentage(row.retention_rate_60d)}</td>
                                    <td className="text-right">{formatPercentage(row.retention_rate_lifetime)}</td>
                                    <td className="text-right">{row.prior_cohort_size_30d}</td>
                                </tr>
                            )) : (
                                <tr><td colSpan={5} className="text-center customers-analytics-empty">No trend rows for this range.</td></tr>
                            )}
                        </tbody>
                    </table>
                )}
            </ResizableTableWrapper>
        </div>
    );
}
