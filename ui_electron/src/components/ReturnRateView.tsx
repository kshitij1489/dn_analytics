import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { endpoints } from '../api';
import type { CustomerReturnRateResponse, CustomerReturnRateRow } from '../types/api';
import { exportToCSV } from '../utils/csv';
import {
    type LookbackFilters,
    type SortState,
    ORDER_SOURCE_OPTIONS,
    MIN_ORDER_OPTIONS,
    buildLookbackParams,
    formatCurrency,
    formatPercentage,
    getErrorMessage,
    updateEvaluationEndDate,
    updateEvaluationStartDate,
    updateLookbackEndDate,
    updateLookbackStartDate,
} from './customerAnalyticsShared';
import { CustomerLink } from './CustomerLink';
import { DateSelector } from './DateSelector';
import { KPICard } from './KPICard';
import { LoadingSpinner } from './LoadingSpinner';
import { ResizableTableWrapper } from './ResizableTableWrapper';
import { Select } from './Select';

interface ReturnRateViewProps {
    lastDbSync?: number;
    sort: SortState;
    filters: LookbackFilters;
    setFilters: Dispatch<SetStateAction<LookbackFilters>>;
}

export function ReturnRateView({ lastDbSync, sort, filters, setFilters }: ReturnRateViewProps) {
    const [data, setData] = useState<CustomerReturnRateResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const res = await endpoints.insights.customerReturnRateAnalysis(
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

    if (loading) return <LoadingSpinner />;
    if (error) return <div className="customers-analytics-error">{error}</div>;

    const summary = data?.summary;
    const rows = sort.sortRows<CustomerReturnRateRow>(data?.rows || []);
    const updateFilters = (partial: Partial<LookbackFilters>) =>
        setFilters((c) => ({ ...c, ...partial }));

    return (
        <div className="customers-analytics-view">
            <div className="customers-analytics-header">
                <div>
                    <h3 className="customers-analytics-title">Customer Return Rate</h3>
                    <p className="customers-analytics-hint">
                        Customers in the evaluation window count as returning when they either meet the selected repeat-order threshold in that window or have any order in the selected lookback window.
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
                    <span className="customers-analytics-filter-label">Return Condition</span>
                    <Select value={filters.minOrdersPerCustomer} onChange={(v) => updateFilters({ minOrdersPerCustomer: v })} options={MIN_ORDER_OPTIONS} />
                </div>
                <div className="customers-analytics-filter-group">
                    <span className="customers-analytics-filter-label">Order Source</span>
                    <Select value={filters.orderSource} onChange={(v) => updateFilters({ orderSource: v })} options={ORDER_SOURCE_OPTIONS} />
                </div>
            </div>

            <div className="customers-analytics-kpi-grid">
                <KPICard title="Return Rate" value={formatPercentage(summary?.return_rate)} hint="Share of evaluation-window customers who qualified as returning." />
                <KPICard title="Returning Customers" value={summary?.returning_customers?.toLocaleString() || 0} hint="Numerator: customers counted as returning under the selected filters." />
                <KPICard title="Evaluation Customers" value={summary?.total_customers?.toLocaleString() || 0} hint="Denominator: unique customers active in the selected evaluation range." />
                <KPICard title="Qualified by Repeat Orders" value={summary?.returning_by_repeat_orders?.toLocaleString() || 0} hint="Customers who met the selected repeat-order threshold inside the evaluation window." />
                <KPICard title="Qualified by Lookback" value={summary?.returning_from_lookback?.toLocaleString() || 0} hint="Customers who had at least one order in the selected lookback window." />
            </div>

            <div className="customers-analytics-summary">
                Evaluation: <strong>{summary?.evaluation_start_date}</strong> to <strong>{summary?.evaluation_end_date}</strong>
                {' | '}Lookback: <strong>{summary?.lookback_start_date}</strong> to <strong>{summary?.lookback_end_date}</strong>
                {' | '}Source: <strong>{summary?.order_source_label || 'All'}</strong>
                {' | '}Condition: <strong>at least {summary?.min_orders_per_customer || 2} orders</strong>
            </div>
            <div className="customers-analytics-subnote">
                Customers counted by both repeat-order and lookback conditions: {summary?.returning_by_both_conditions?.toLocaleString() || 0}
            </div>

            <ResizableTableWrapper defaultHeight={520} onExportCSV={() => exportToCSV(rows, 'customer_return_rate_details')}>
                <table className="standard-table">
                    <thead>
                        <tr>
                            <th onClick={() => sort.handleSort('customer_name')}>Customer{sort.renderSortIcon('customer_name')}</th>
                            <th onClick={() => sort.handleSort('returning_flag')}>Status{sort.renderSortIcon('returning_flag')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('evaluation_order_count')}>Eval Orders{sort.renderSortIcon('evaluation_order_count')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('lookback_order_count')}>Lookback Orders{sort.renderSortIcon('lookback_order_count')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('evaluation_total_spend')}>Eval Spend{sort.renderSortIcon('evaluation_total_spend')}</th>
                            <th onClick={() => sort.handleSort('first_order_date')}>First Eval Order{sort.renderSortIcon('first_order_date')}</th>
                            <th onClick={() => sort.handleSort('last_order_date')}>Last Eval Order{sort.renderSortIcon('last_order_date')}</th>
                            <th onClick={() => sort.handleSort('return_reason')}>Reason{sort.renderSortIcon('return_reason')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.length > 0 ? rows.map((row) => (
                            <tr key={String(row.customer_id)}>
                                <td><CustomerLink customerId={row.customer_id} name={row.customer_name} /></td>
                                <td><span className={row.returning_flag ? 'status-returning' : 'status-new'}>{row.returning_status}</span></td>
                                <td className="text-right">{row.evaluation_order_count}</td>
                                <td className="text-right">{row.lookback_order_count}</td>
                                <td className="text-right">{formatCurrency(row.evaluation_total_spend)}</td>
                                <td>{row.first_order_date}</td>
                                <td>{row.last_order_date}</td>
                                <td>{row.return_reason}</td>
                            </tr>
                        )) : (
                            <tr><td colSpan={8} className="text-center customers-analytics-empty">No customers found for the selected filters.</td></tr>
                        )}
                    </tbody>
                </table>
            </ResizableTableWrapper>
        </div>
    );
}
