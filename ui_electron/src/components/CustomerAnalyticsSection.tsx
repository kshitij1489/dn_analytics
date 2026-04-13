import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { exportToCSV } from '../utils/csv';
import {
    type LookbackFilters,
    type MetricFilters,
    type SortableValue,
    type SortState,
    buildDefaultLookbackFilters,
    buildDefaultMetricFilters,
    formatCurrency,
    getErrorMessage,
} from './customerAnalyticsShared';
import { CustomerLink } from './CustomerLink';
import { LoadingSpinner } from './LoadingSpinner';
import { AffinityView } from './AffinityView';
import { RepeatOrderRateView } from './RepeatOrderRateView';
import { ResizableTableWrapper } from './ResizableTableWrapper';
import { RetentionRateView } from './RetentionRateView';
import { ReturnRateView } from './ReturnRateView';
import { TabButton } from './TabButton';

type CustomerAnalyticsView = 'loyalty' | 'top' | 'returnRate' | 'retentionRate' | 'repeatOrderRate' | 'affinity';

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

function useSortState(): SortState {
    const [sortKey, setSortKey] = useState<string | null>(null);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
        } else {
            setSortKey(key);
            setSortDirection('asc');
        }
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return ' ⇅';
        return sortDirection === 'asc' ? ' ↑' : ' ↓';
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

    const resetSort = () => {
        setSortKey(null);
        setSortDirection('asc');
    };

    return { sortKey, handleSort, renderSortIcon, sortRows, resetSort };
}

export function CustomerAnalyticsSection({ lastDbSync }: { lastDbSync?: number }) {
    const [customerView, setCustomerView] = useState<CustomerAnalyticsView>('loyalty');
    const [loyaltyData, setLoyaltyData] = useState<LoyaltyRow[]>([]);
    const [topCustomers, setTopCustomers] = useState<TopCustomerRow[]>([]);
    const [returnRateFilters, setReturnRateFilters] = useState<LookbackFilters>(() => buildDefaultLookbackFilters());
    const [retentionRateFilters, setRetentionRateFilters] = useState<LookbackFilters>(() => buildDefaultLookbackFilters());
    const [repeatOrderRateFilters, setRepeatOrderRateFilters] = useState<MetricFilters>(() => buildDefaultMetricFilters());
    const [affinityFilters, setAffinityFilters] = useState<MetricFilters>(() => buildDefaultMetricFilters());
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const sort = useSortState();

    useEffect(() => {
        sort.resetSort();
    }, [customerView]);

    useEffect(() => {
        if (customerView !== 'loyalty' && customerView !== 'top') return;

        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                if (customerView === 'loyalty') {
                    const res = await endpoints.insights.customerLoyalty({ _t: lastDbSync });
                    if (!cancelled) setLoyaltyData(Array.isArray(res?.data) ? res.data : []);
                } else {
                    const res = await endpoints.insights.topCustomers({ _t: lastDbSync });
                    if (!cancelled) setTopCustomers(Array.isArray(res?.data) ? res.data : []);
                }
            } catch (e: any) {
                if (!cancelled) setError(getErrorMessage(e));
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void load();
        return () => { cancelled = true; };
    }, [customerView, lastDbSync]);

    const sortedLoyaltyData = sort.sortRows(loyaltyData);
    const sortedTopCustomers = sort.sortRows(topCustomers);

    const renderContent = () => {
        if (customerView === 'returnRate') {
            return (
                <ReturnRateView
                    lastDbSync={lastDbSync}
                    sort={sort}
                    filters={returnRateFilters}
                    setFilters={setReturnRateFilters}
                />
            );
        }
        if (customerView === 'retentionRate') {
            return (
                <RetentionRateView
                    lastDbSync={lastDbSync}
                    sort={sort}
                    filters={retentionRateFilters}
                    setFilters={setRetentionRateFilters}
                />
            );
        }
        if (customerView === 'repeatOrderRate') {
            return (
                <RepeatOrderRateView
                    lastDbSync={lastDbSync}
                    sort={sort}
                    filters={repeatOrderRateFilters}
                    setFilters={setRepeatOrderRateFilters}
                />
            );
        }
        if (customerView === 'affinity') {
            return (
                <AffinityView
                    lastDbSync={lastDbSync}
                    sort={sort}
                    filters={affinityFilters}
                    setFilters={setAffinityFilters}
                />
            );
        }

        if (loading) return <LoadingSpinner />;
        if (error) return <div className="customers-analytics-error">{error}</div>;

        if (customerView === 'loyalty') {
            return (
                <ResizableTableWrapper onExportCSV={() => exportToCSV(sortedLoyaltyData, 'customer_retention')}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th onClick={() => sort.handleSort('Month')}>Month{sort.renderSortIcon('Month')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Repeat Customer %')}>Repeat Customer %{sort.renderSortIcon('Repeat Customer %')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Order Repeat%')}>Order Repeat%{sort.renderSortIcon('Order Repeat%')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Revenue Repeat %')}>Revenue Repeat %{sort.renderSortIcon('Revenue Repeat %')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Repeat Orders')}>Repeat Orders{sort.renderSortIcon('Repeat Orders')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Total Orders')}>Total Orders{sort.renderSortIcon('Total Orders')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Repeat Revenue')}>Repeat Revenue{sort.renderSortIcon('Repeat Revenue')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Total Revenue')}>Total Revenue{sort.renderSortIcon('Total Revenue')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Repeat Customer Count')}>Repeat Customer Count{sort.renderSortIcon('Repeat Customer Count')}</th>
                                <th className="text-right" onClick={() => sort.handleSort('Total Verified Customers')}>Total Verified Customers{sort.renderSortIcon('Total Verified Customers')}</th>
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
                                    <td className="text-right">{formatCurrency(row['Repeat Revenue'])}</td>
                                    <td className="text-right">{formatCurrency(row['Total Revenue'])}</td>
                                    <td className="text-right">{row['Repeat Customer Count']}</td>
                                    <td className="text-right">{row['Total Verified Customers']}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            );
        }

        return (
            <ResizableTableWrapper onExportCSV={() => exportToCSV(sortedTopCustomers, 'top_customers')}>
                <table className="standard-table">
                    <thead>
                        <tr>
                            <th onClick={() => sort.handleSort('name')}>Customer{sort.renderSortIcon('name')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('total_orders')}>Total Orders{sort.renderSortIcon('total_orders')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('total_spent')}>Total Spent{sort.renderSortIcon('total_spent')}</th>
                            <th onClick={() => sort.handleSort('last_order_date')}>Last Order Date{sort.renderSortIcon('last_order_date')}</th>
                            <th onClick={() => sort.handleSort('status')}>Status{sort.renderSortIcon('status')}</th>
                            <th onClick={() => sort.handleSort('favorite_item')}>Favorite Item{sort.renderSortIcon('favorite_item')}</th>
                            <th className="text-right" onClick={() => sort.handleSort('fav_item_qty')}>Fav Item Qty{sort.renderSortIcon('fav_item_qty')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sortedTopCustomers.map((row, idx) => (
                            <tr key={idx}>
                                <td><CustomerLink customerId={row.customer_id} name={row.name} /></td>
                                <td className="text-right">{row.total_orders}</td>
                                <td className="text-right">{formatCurrency(row.total_spent)}</td>
                                <td>{row.last_order_date}</td>
                                <td><span className={row.status === 'Returning' ? 'status-returning' : 'status-new'}>{row.status}</span></td>
                                <td>{row.favorite_item}</td>
                                <td className="text-right">{row.fav_item_qty}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </ResizableTableWrapper>
        );
    };

    return (
        <div>
            <div className="segmented-control customers-analytics-tabs">
                <TabButton active={customerView === 'loyalty'} onClick={() => setCustomerView('loyalty')} variant="segmented">Summary</TabButton>
                <TabButton active={customerView === 'returnRate'} onClick={() => setCustomerView('returnRate')} variant="segmented">Customer Return Rate</TabButton>
                <TabButton active={customerView === 'retentionRate'} onClick={() => setCustomerView('retentionRate')} variant="segmented">Customer Retention Rate</TabButton>
                <TabButton active={customerView === 'repeatOrderRate'} onClick={() => setCustomerView('repeatOrderRate')} variant="segmented">Repeat Order Rate</TabButton>
                <TabButton active={customerView === 'affinity'} onClick={() => setCustomerView('affinity')} variant="segmented">Customer affinity</TabButton>
                <TabButton active={customerView === 'top'} onClick={() => setCustomerView('top')} variant="segmented">Top Verified Customers</TabButton>
            </div>

            {renderContent()}
        </div>
    );
}
