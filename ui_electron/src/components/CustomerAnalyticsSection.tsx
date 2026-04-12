import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { exportToCSV } from '../utils/csv';
import { CustomerLink } from './CustomerLink';
import { ResizableTableWrapper } from './ResizableTableWrapper';
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

export function CustomerAnalyticsSection({ lastDbSync }: { lastDbSync?: number }) {
    const [customerView, setCustomerView] = useState<CustomerAnalyticsView>('loyalty');
    const [loyaltyData, setLoyaltyData] = useState<LoyaltyRow[]>([]);
    const [topCustomers, setTopCustomers] = useState<TopCustomerRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [sortKey, setSortKey] = useState<string | null>(null);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

    useEffect(() => {
        setSortKey(null);
        setSortDirection('asc');
    }, [customerView]);

    useEffect(() => {
        const loadData = async () => {
            if (customerView !== 'loyalty' && customerView !== 'top') {
                setLoading(false);
                return;
            }

            setLoading(true);
            try {
                if (customerView === 'loyalty') {
                    const loyaltyRes = await endpoints.insights.customerLoyalty({ _t: lastDbSync });
                    setLoyaltyData(loyaltyRes.data);
                } else {
                    const topRes = await endpoints.insights.topCustomers({ _t: lastDbSync });
                    setTopCustomers(Array.isArray(topRes?.data) ? topRes.data : []);
                }
            } catch (error) {
                console.error(error);
            } finally {
                setLoading(false);
            }
        };

        loadData();
    }, [customerView, lastDbSync]);

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

    const sortedLoyaltyData = sortRows(loyaltyData);
    const sortedTopCustomers = sortRows(topCustomers);
    const isPlaceholderView = customerView === 'returnRate' || customerView === 'retentionRate' || customerView === 'repeatOrderRate';

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

            {loading ? <div>Loading...</div> : (
                customerView === 'loyalty' ? (
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
                )
            )}
        </div>
    );
}
