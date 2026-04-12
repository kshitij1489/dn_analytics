import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import type { CustomerReturnRateResponse, CustomerReturnRateRow } from '../types/api';
import { exportToCSV } from '../utils/csv';
import { CustomerLink } from './CustomerLink';
import { DateSelector } from './DateSelector';
import { KPICard } from './KPICard';
import { ResizableTableWrapper } from './ResizableTableWrapper';
import { Select } from './Select';
import { TabButton } from './TabButton';

type CustomerAnalyticsView = 'loyalty' | 'top' | 'returnRate' | 'retentionRate' | 'repeatOrderRate';
type SortableValue = string | number | null | undefined;

interface LoyaltyRow extends Record<string, SortableValue> {
    Month: string;
    'Repeat Orders': number;
    'Total Orders': number;
    'Order Repeat%': number;
    'Repeat Customer Count': number;
    'Total Verified Customers': number;
    'Repeat Customer %': number;
    'Repeat Revenue': number;
    'Total Revenue': number;
    'Revenue Repeat %': number;
}

interface TopCustomerRow extends Record<string, SortableValue> {
    customer_id: string | number;
    name: string;
    total_orders: number;
    total_spent: number;
    last_order_date: string;
    status: string;
    favorite_item: string;
    fav_item_qty: number;
}

interface ReturnRateFilters {
    evaluationStartDate: string;
    evaluationEndDate: string;
    lookbackStartDate: string;
    lookbackEndDate: string;
    minOrdersPerCustomer: string;
    orderSource: string;
}

const ORDER_SOURCE_OPTIONS = [
    { value: 'All', label: 'All Sources' },
    { value: 'POS', label: 'POS' },
    { value: 'Swiggy', label: 'Swiggy' },
    { value: 'Zomato', label: 'Zomato' },
    { value: 'Home Website', label: 'Home Website' },
];

const MIN_ORDER_OPTIONS = [
    { value: '2', label: '>= 2 Orders' },
    { value: '3', label: '>= 3 Orders' },
    { value: '4', label: '>= 4 Orders' },
    { value: '5', label: '>= 5 Orders' },
];

function formatDateUTC(date: Date) {
    return date.toISOString().slice(0, 10);
}

function getCurrentDateISO() {
    const now = new Date();
    return [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, '0'),
        String(now.getDate()).padStart(2, '0'),
    ].join('-');
}

function getMonthStartISO(dateISO: string) {
    const [year, month] = dateISO.split('-').map(Number);
    return formatDateUTC(new Date(Date.UTC(year, month - 1, 1)));
}

function shiftMonthStartISO(dateISO: string, offset: number) {
    const [year, month] = dateISO.split('-').map(Number);
    return formatDateUTC(new Date(Date.UTC(year, month - 1 + offset, 1)));
}

function getMonthEndISO(monthStartISO: string) {
    const [year, month] = monthStartISO.split('-').map(Number);
    return formatDateUTC(new Date(Date.UTC(year, month, 0)));
}

function buildDefaultReturnRateFilters(): ReturnRateFilters {
    const currentDate = getCurrentDateISO();
    const currentMonthStart = getMonthStartISO(currentDate);
    const previousMonthStart = shiftMonthStartISO(currentMonthStart, -1);

    return {
        evaluationStartDate: currentMonthStart,
        evaluationEndDate: currentDate,
        lookbackStartDate: previousMonthStart,
        lookbackEndDate: getMonthEndISO(previousMonthStart),
        minOrdersPerCustomer: '2',
        orderSource: 'All',
    };
}

function getErrorMessage(error: any) {
    return error?.response?.data?.detail || error?.message || 'Failed to load customer analytics.';
}

function formatPercentage(value?: number | null) {
    if (value == null || Number.isNaN(value)) return '0.00%';
    return `${value.toFixed(2)}%`;
}

function formatCurrency(value?: number | null) {
    return `₹${Number(value || 0).toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
    })}`;
}

export function CustomerAnalyticsSection({ lastDbSync }: { lastDbSync?: number }) {
    const [customerView, setCustomerView] = useState<CustomerAnalyticsView>('loyalty');
    const [loyaltyData, setLoyaltyData] = useState<LoyaltyRow[]>([]);
    const [topCustomers, setTopCustomers] = useState<TopCustomerRow[]>([]);
    const [returnRateData, setReturnRateData] = useState<CustomerReturnRateResponse | null>(null);
    const [returnRateFilters, setReturnRateFilters] = useState<ReturnRateFilters>(() => buildDefaultReturnRateFilters());
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [sortKey, setSortKey] = useState<string | null>(null);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

    useEffect(() => {
        setSortKey(null);
        setSortDirection('asc');
    }, [customerView]);

    useEffect(() => {
        let isCancelled = false;

        const loadData = async () => {
            if (customerView === 'retentionRate' || customerView === 'repeatOrderRate') {
                setLoading(false);
                setError(null);
                return;
            }

            setLoading(true);
            setError(null);
            try {
                if (customerView === 'loyalty') {
                    const loyaltyRes = await endpoints.insights.customerLoyalty({ _t: lastDbSync });
                    if (!isCancelled) {
                        setLoyaltyData(Array.isArray(loyaltyRes?.data) ? loyaltyRes.data : []);
                    }
                    return;
                }

                if (customerView === 'top') {
                    const topRes = await endpoints.insights.topCustomers({ _t: lastDbSync });
                    if (!isCancelled) {
                        setTopCustomers(Array.isArray(topRes?.data) ? topRes.data : []);
                    }
                    return;
                }

                const params = {
                    _t: lastDbSync,
                    evaluation_start_date: returnRateFilters.evaluationStartDate,
                    evaluation_end_date: returnRateFilters.evaluationEndDate,
                    lookback_start_date: returnRateFilters.lookbackStartDate,
                    lookback_end_date: returnRateFilters.lookbackEndDate,
                    min_orders_per_customer: Number(returnRateFilters.minOrdersPerCustomer),
                    order_sources: returnRateFilters.orderSource === 'All' ? undefined : [returnRateFilters.orderSource],
                };
                const returnRateRes = await endpoints.insights.customerReturnRateAnalysis(params);
                if (!isCancelled) {
                    setReturnRateData(returnRateRes.data);
                }
            } catch (loadError: any) {
                if (!isCancelled) {
                    setError(getErrorMessage(loadError));
                }
            } finally {
                if (!isCancelled) {
                    setLoading(false);
                }
            }
        };

        void loadData();
        return () => {
            isCancelled = true;
        };
    }, [customerView, lastDbSync, returnRateFilters]);

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
        } else {
            setSortKey(key);
            setSortDirection('asc');
        }
    };

    const sortRows = <T extends Record<string, SortableValue>>(rows: T[]) => {
        if (!sortKey) return rows;

        return [...rows].sort((a, b) => {
            let aVal = a[sortKey];
            let bVal = b[sortKey];

            if (aVal == null) aVal = 0;
            if (bVal == null) bVal = 0;

            if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
            return 0;
        });
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return ' ⇅';
        return sortDirection === 'asc' ? ' ↑' : ' ↓';
    };

    const updateReturnRateFilters = (partial: Partial<ReturnRateFilters>) => {
        setReturnRateFilters((current) => ({ ...current, ...partial }));
    };

    const handleEvaluationStartDateChange = (date: string) => {
        setReturnRateFilters((current) => ({
            ...current,
            evaluationStartDate: date,
            evaluationEndDate: current.evaluationEndDate < date ? date : current.evaluationEndDate,
        }));
    };

    const handleEvaluationEndDateChange = (date: string) => {
        setReturnRateFilters((current) => ({
            ...current,
            evaluationStartDate: current.evaluationStartDate > date ? date : current.evaluationStartDate,
            evaluationEndDate: date,
        }));
    };

    const handleLookbackStartDateChange = (date: string) => {
        setReturnRateFilters((current) => ({
            ...current,
            lookbackStartDate: date,
            lookbackEndDate: current.lookbackEndDate < date ? date : current.lookbackEndDate,
        }));
    };

    const handleLookbackEndDateChange = (date: string) => {
        setReturnRateFilters((current) => ({
            ...current,
            lookbackStartDate: current.lookbackStartDate > date ? date : current.lookbackStartDate,
            lookbackEndDate: date,
        }));
    };

    const sortedLoyaltyData = sortRows(loyaltyData);
    const sortedTopCustomers = sortRows(topCustomers);
    const sortedReturnRateRows = sortRows<CustomerReturnRateRow>(returnRateData?.rows || []);
    const returnRateSummary = returnRateData?.summary;
    const isPlaceholderView = customerView === 'retentionRate' || customerView === 'repeatOrderRate';

    return (
        <div>
            <div className="segmented-control" style={{ marginBottom: '8px', width: 'fit-content', maxWidth: '100%', flexWrap: 'wrap' }}>
                <TabButton
                    active={customerView === 'loyalty'}
                    onClick={() => setCustomerView('loyalty')}
                    variant="segmented"
                >
                    Summary
                </TabButton>
                <TabButton
                    active={customerView === 'returnRate'}
                    onClick={() => setCustomerView('returnRate')}
                    variant="segmented"
                >
                    Customer Return Rate
                </TabButton>
                <TabButton
                    active={customerView === 'retentionRate'}
                    onClick={() => setCustomerView('retentionRate')}
                    variant="segmented"
                >
                    Customer Retention Rate
                </TabButton>
                <TabButton
                    active={customerView === 'repeatOrderRate'}
                    onClick={() => setCustomerView('repeatOrderRate')}
                    variant="segmented"
                >
                    Repeat Order Rate
                </TabButton>
                <TabButton
                    active={customerView === 'top'}
                    onClick={() => setCustomerView('top')}
                    variant="segmented"
                >
                    Top Verified Customers
                </TabButton>
            </div>

            {loading ? <div>Loading...</div> : error ? (
                <div className="customers-analytics-error">{error}</div>
            ) : customerView === 'loyalty' ? (
                <ResizableTableWrapper onExportCSV={() => exportToCSV(sortedLoyaltyData, 'customer_retention')}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th onClick={() => handleSort('Month')}>Month{renderSortIcon('Month')}</th>
                                <th className="text-right" onClick={() => handleSort('Repeat Customer %')}>Repeat Customer %{renderSortIcon('Repeat Customer %')}</th>
                                <th className="text-right" onClick={() => handleSort('Order Repeat%')}>Order Repeat%{renderSortIcon('Order Repeat%')}</th>
                                <th className="text-right" onClick={() => handleSort('Revenue Repeat %')}>Revenue Repeat %{renderSortIcon('Revenue Repeat %')}</th>
                                <th className="text-right" onClick={() => handleSort('Repeat Orders')}>Repeat Orders{renderSortIcon('Repeat Orders')}</th>
                                <th className="text-right" onClick={() => handleSort('Total Orders')}>Total Orders{renderSortIcon('Total Orders')}</th>
                                <th className="text-right" onClick={() => handleSort('Repeat Revenue')}>Repeat Revenue{renderSortIcon('Repeat Revenue')}</th>
                                <th className="text-right" onClick={() => handleSort('Total Revenue')}>Total Revenue{renderSortIcon('Total Revenue')}</th>
                                <th className="text-right" onClick={() => handleSort('Repeat Customer Count')}>Repeat Customer Count{renderSortIcon('Repeat Customer Count')}</th>
                                <th className="text-right" onClick={() => handleSort('Total Verified Customers')}>Total Verified Customers{renderSortIcon('Total Verified Customers')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedLoyaltyData.map((row, idx) => (
                                <tr key={idx}>
                                    <td>{row.Month}</td>
                                    <td className="text-right">{row['Repeat Customer %']}%</td>
                                    <td className="text-right">{row['Order Repeat%']}%</td>
                                    <td className="text-right">{row['Revenue Repeat %']}%</td>
                                    <td className="text-right">{row['Repeat Orders']}</td>
                                    <td className="text-right">{row['Total Orders']}</td>
                                    <td className="text-right">₹{Math.round(row['Repeat Revenue'] || 0).toLocaleString()}</td>
                                    <td className="text-right">₹{Math.round(row['Total Revenue'] || 0).toLocaleString()}</td>
                                    <td className="text-right">{row['Repeat Customer Count']}</td>
                                    <td className="text-right">{row['Total Verified Customers']}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            ) : customerView === 'returnRate' ? (
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
                            <DateSelector
                                value={returnRateFilters.evaluationStartDate}
                                displayValue={returnRateFilters.evaluationStartDate}
                                onChange={handleEvaluationStartDateChange}
                                maxDate={returnRateFilters.evaluationEndDate}
                                suffix=""
                            />
                        </div>
                        <div className="customers-analytics-filter-group">
                            <span className="customers-analytics-filter-label">Evaluation End</span>
                            <DateSelector
                                value={returnRateFilters.evaluationEndDate}
                                displayValue={returnRateFilters.evaluationEndDate}
                                onChange={handleEvaluationEndDateChange}
                                minDate={returnRateFilters.evaluationStartDate}
                                suffix=""
                            />
                        </div>
                        <div className="customers-analytics-filter-group">
                            <span className="customers-analytics-filter-label">Lookback Start</span>
                            <DateSelector
                                value={returnRateFilters.lookbackStartDate}
                                displayValue={returnRateFilters.lookbackStartDate}
                                onChange={handleLookbackStartDateChange}
                                maxDate={returnRateFilters.lookbackEndDate}
                                suffix=""
                            />
                        </div>
                        <div className="customers-analytics-filter-group">
                            <span className="customers-analytics-filter-label">Lookback End</span>
                            <DateSelector
                                value={returnRateFilters.lookbackEndDate}
                                displayValue={returnRateFilters.lookbackEndDate}
                                onChange={handleLookbackEndDateChange}
                                minDate={returnRateFilters.lookbackStartDate}
                                suffix=""
                            />
                        </div>
                        <div className="customers-analytics-filter-group">
                            <span className="customers-analytics-filter-label">Return Condition</span>
                            <Select
                                value={returnRateFilters.minOrdersPerCustomer}
                                onChange={(value) => updateReturnRateFilters({ minOrdersPerCustomer: value })}
                                options={MIN_ORDER_OPTIONS}
                            />
                        </div>
                        <div className="customers-analytics-filter-group">
                            <span className="customers-analytics-filter-label">Order Source</span>
                            <Select
                                value={returnRateFilters.orderSource}
                                onChange={(value) => updateReturnRateFilters({ orderSource: value })}
                                options={ORDER_SOURCE_OPTIONS}
                            />
                        </div>
                    </div>

                    <div className="customers-analytics-kpi-grid">
                        <KPICard
                            title="Return Rate"
                            value={formatPercentage(returnRateSummary?.return_rate)}
                            hint="Share of evaluation-window customers who qualified as returning."
                        />
                        <KPICard
                            title="Returning Customers"
                            value={returnRateSummary?.returning_customers?.toLocaleString() || 0}
                            hint="Numerator: customers counted as returning under the selected filters."
                        />
                        <KPICard
                            title="Evaluation Customers"
                            value={returnRateSummary?.total_customers?.toLocaleString() || 0}
                            hint="Denominator: unique customers active in the selected evaluation range."
                        />
                        <KPICard
                            title="Qualified by Repeat Orders"
                            value={returnRateSummary?.returning_by_repeat_orders?.toLocaleString() || 0}
                            hint="Customers who met the selected repeat-order threshold inside the evaluation window."
                        />
                        <KPICard
                            title="Qualified by Lookback"
                            value={returnRateSummary?.returning_from_lookback?.toLocaleString() || 0}
                            hint="Customers who had at least one order in the selected lookback window."
                        />
                    </div>

                    <div className="customers-analytics-summary">
                        Evaluation: <strong>{returnRateSummary?.evaluation_start_date}</strong> to <strong>{returnRateSummary?.evaluation_end_date}</strong>
                        {' • '}
                        Lookback: <strong>{returnRateSummary?.lookback_start_date}</strong> to <strong>{returnRateSummary?.lookback_end_date}</strong>
                        {' • '}
                        Source: <strong>{returnRateSummary?.order_source_label || 'All'}</strong>
                        {' • '}
                        Condition: <strong>at least {returnRateSummary?.min_orders_per_customer || 2} orders</strong>
                    </div>
                    <div className="customers-analytics-subnote">
                        Customers counted by both repeat-order and lookback conditions: {returnRateSummary?.returning_by_both_conditions?.toLocaleString() || 0}
                    </div>

                    <ResizableTableWrapper
                        defaultHeight={520}
                        onExportCSV={() => exportToCSV(sortedReturnRateRows, 'customer_return_rate_details')}
                    >
                        <table className="standard-table">
                            <thead>
                                <tr>
                                    <th onClick={() => handleSort('customer_name')}>Customer{renderSortIcon('customer_name')}</th>
                                    <th onClick={() => handleSort('returning_flag')}>Status{renderSortIcon('returning_flag')}</th>
                                    <th className="text-right" onClick={() => handleSort('evaluation_order_count')}>Eval Orders{renderSortIcon('evaluation_order_count')}</th>
                                    <th className="text-right" onClick={() => handleSort('lookback_order_count')}>Lookback Orders{renderSortIcon('lookback_order_count')}</th>
                                    <th className="text-right" onClick={() => handleSort('evaluation_total_spend')}>Eval Spend{renderSortIcon('evaluation_total_spend')}</th>
                                    <th onClick={() => handleSort('first_order_date')}>First Eval Order{renderSortIcon('first_order_date')}</th>
                                    <th onClick={() => handleSort('last_order_date')}>Last Eval Order{renderSortIcon('last_order_date')}</th>
                                    <th onClick={() => handleSort('return_reason')}>Reason{renderSortIcon('return_reason')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {sortedReturnRateRows.length > 0 ? sortedReturnRateRows.map((row) => (
                                    <tr key={String(row.customer_id)}>
                                        <td>
                                            <CustomerLink customerId={row.customer_id} name={row.customer_name} />
                                        </td>
                                        <td>
                                            <span className={row.returning_flag ? 'status-returning' : 'status-new'}>
                                                {row.returning_status}
                                            </span>
                                        </td>
                                        <td className="text-right">{row.evaluation_order_count}</td>
                                        <td className="text-right">{row.lookback_order_count}</td>
                                        <td className="text-right">{formatCurrency(row.evaluation_total_spend)}</td>
                                        <td>{row.first_order_date}</td>
                                        <td>{row.last_order_date}</td>
                                        <td>{row.return_reason}</td>
                                    </tr>
                                )) : (
                                    <tr>
                                        <td colSpan={8} className="text-center customers-analytics-empty">
                                            No customers found for the selected filters.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </ResizableTableWrapper>
                </div>
            ) : isPlaceholderView ? (
                <div style={{ minHeight: '320px' }} />
            ) : (
                <ResizableTableWrapper onExportCSV={() => exportToCSV(sortedTopCustomers, 'top_customers')}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th onClick={() => handleSort('name')}>Customer{renderSortIcon('name')}</th>
                                <th className="text-right" onClick={() => handleSort('total_orders')}>Total Orders{renderSortIcon('total_orders')}</th>
                                <th className="text-right" onClick={() => handleSort('total_spent')}>Total Spent{renderSortIcon('total_spent')}</th>
                                <th onClick={() => handleSort('last_order_date')}>Last Order Date{renderSortIcon('last_order_date')}</th>
                                <th onClick={() => handleSort('status')}>Status{renderSortIcon('status')}</th>
                                <th onClick={() => handleSort('favorite_item')}>Favorite Item{renderSortIcon('favorite_item')}</th>
                                <th className="text-right" onClick={() => handleSort('fav_item_qty')}>Fav Item Qty{renderSortIcon('fav_item_qty')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedTopCustomers.map((row, idx) => (
                                <tr key={idx}>
                                    <td>
                                        <CustomerLink customerId={row.customer_id} name={row.name} />
                                    </td>
                                    <td className="text-right">{row.total_orders}</td>
                                    <td className="text-right">₹{Math.round(row.total_spent || 0).toLocaleString()}</td>
                                    <td>{row.last_order_date}</td>
                                    <td>
                                        <span className={row.status === 'Returning' ? 'status-returning' : 'status-new'}>
                                            {row.status}
                                        </span>
                                    </td>
                                    <td>{row.favorite_item}</td>
                                    <td className="text-right">{row.fav_item_qty}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            )}
        </div>
    );
}
