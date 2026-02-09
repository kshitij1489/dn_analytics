import { useEffect, useRef, useState } from 'react';
import { endpoints } from '../api';
import { ResizableTableWrapper, LoadingSpinner, Card, DateSelector } from '../components';
import './TodayPage.css';

interface SourceData {
    source: string;
    orders: number;
    revenue: number;
}

interface MenuItem {
    menu_item_id: string;
    item_name: string;
    cluster_name: string;
    qty_sold: number;
    revenue: number;
    reorder_count: number;
}

interface Customer {
    customer_id: number;
    name: string;
    is_verified: boolean;
    order_value: number;
    is_returning: boolean;
    items_ordered: string[];
    history_orders: number;
    history_spent: number;
}

interface Order {
    order_id: number;
    petpooja_order_id: number;
    customer_name: string;
    order_items: string[];
    total: number;
    time: string;
    source: string;
}

interface SummaryData {
    date: string;
    total_revenue: number;
    total_orders: number;
    total_customers: number;
    returning_customer_count: number;
    sources: SourceData[];
}

interface TodayPageProps {
    lastDbSync?: number;
}

export default function TodayPage({ lastDbSync }: TodayPageProps) {
    const [summary, setSummary] = useState<SummaryData | null>(null);
    const [menuItems, setMenuItems] = useState<MenuItem[]>([]);
    const [customers, setCustomers] = useState<Customer[]>([]);
    const [orders, setOrders] = useState<Order[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Date selection: empty string = use backend's current business date (default)
    // Non-empty = user explicitly selected a date
    const [selectedDate, setSelectedDate] = useState<string>('');

    // Max date for the picker (today in local timezone)
    const maxDate = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD format

    // Ref to skip the redundant refetch when syncing the backend's business date
    const isInitialSync = useRef(false);

    useEffect(() => {
        if (isInitialSync.current) {
            isInitialSync.current = false;
            return;
        }
        loadData();
    }, [lastDbSync, selectedDate]);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            // Only send date param if user explicitly selected one
            const params = selectedDate ? { date: selectedDate } : undefined;
            const [summaryRes, itemsRes, customersRes, ordersRes] = await Promise.all([
                endpoints.today.getSummary(params),
                endpoints.today.getMenuItems(params),
                endpoints.today.getCustomers(params),
                endpoints.today.getOrders(params)
            ]);
            setSummary(summaryRes.data);
            setMenuItems(itemsRes.data.items || []);
            setCustomers(customersRes.data.customers || []);
            setOrders(ordersRes.data.orders || []);

            // Sync the date picker with the backend's business date on first load
            if (!selectedDate && summaryRes.data.date) {
                isInitialSync.current = true;
                setSelectedDate(summaryRes.data.date);
            }
        } catch (e: any) {
            console.error('Failed to load today data:', e);
            setError(e.message || 'Failed to load data');
        } finally {
            setLoading(false);
        }
    };

    const formatCurrency = (val: number) => `₹${Math.round(val).toLocaleString()}`;

    const getSourceColor = (source: string) => {
        const colors: Record<string, string> = {
            'Swiggy': '#fc8019',
            'Zomato': '#e23744',
            'POS': '#10b981',
            'Home Website': '#3b82f6'
        };
        return colors[source] || '#6b7280';
    };

    if (loading) {
        return (
            <div className="today-loading">
                <LoadingSpinner />
                <p>Loading business day data...</p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="today-error">
                <p>❌ {error}</p>
                <button onClick={loadData} className="today-retry-btn">Retry</button>
            </div>
        );
    }

    return (
        <div className="today-page">
            {/* Header */}
            <div className="today-header">
                <h1>📅 Business Day Snapshot</h1>
                <DateSelector
                    value={selectedDate}
                    displayValue={summary?.date || selectedDate}
                    onChange={setSelectedDate}
                    maxDate={maxDate}
                />
            </div>

            {/* KPI Cards */}
            <div className="today-kpis">
                <div className="today-kpi-card today-kpi-revenue">
                    <span className="kpi-label">Total Revenue</span>
                    <span className="kpi-value">{formatCurrency(summary?.total_revenue || 0)}</span>
                </div>
                <div className="today-kpi-card">
                    <span className="kpi-label">Orders</span>
                    <span className="kpi-value">{summary?.total_orders || 0}</span>
                </div>
                <div className="today-kpi-card">
                    <span className="kpi-label">Customers</span>
                    <span className="kpi-value">{summary?.total_customers || 0}</span>
                </div>
                <div className="today-kpi-card today-kpi-returning">
                    <span className="kpi-label">Returning Customers</span>
                    <span className="kpi-value">{summary?.returning_customer_count || 0}</span>
                </div>
            </div>

            {/* Source Breakdown */}
            <Card title="📊 Orders by Source">
                <div className="today-sources">
                    {summary?.sources.map((src) => (
                        <div
                            key={src.source}
                            className="today-source-chip"
                            style={{ borderColor: getSourceColor(src.source) }}
                        >
                            <span
                                className="source-dot"
                                style={{ backgroundColor: getSourceColor(src.source) }}
                            />
                            <span className="source-name">{src.source}</span>
                            <span className="source-stats">
                                {src.orders} orders • {formatCurrency(src.revenue)}
                            </span>
                        </div>
                    ))}
                    {(!summary?.sources || summary.sources.length === 0) && (
                        <p className="today-empty">No orders for this date</p>
                    )}
                </div>
            </Card>

            {/* Menu Items Table */}
            <Card title="🍽️ Menu Items Sold">
                {menuItems.length > 0 ? (
                    <ResizableTableWrapper>
                        <table className="standard-table">
                            <thead>
                                <tr>
                                    <th>Item Name</th>
                                    <th>Category</th>
                                    <th className="text-right">Qty Sold</th>
                                    <th className="text-right">Revenue</th>
                                    <th className="text-right">Reorder Count</th>
                                </tr>
                            </thead>
                            <tbody>
                                {menuItems.map((item, idx) => (
                                    <tr key={idx}>
                                        <td>{item.item_name}</td>
                                        <td>{item.cluster_name}</td>
                                        <td className="text-right">{item.qty_sold}</td>
                                        <td className="text-right">{formatCurrency(item.revenue)}</td>
                                        <td className="text-right">
                                            {item.reorder_count > 0 ? (
                                                <span className="reorder-badge">{item.reorder_count}</span>
                                            ) : (
                                                <span className="reorder-zero">0</span>
                                            )}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </ResizableTableWrapper>
                ) : (
                    <p className="today-empty">No items sold on this date</p>
                )}
            </Card>

            {/* Customer List */}
            <Card title="👥 Customers">
                {customers.length > 0 ? (
                    <ResizableTableWrapper>
                        <table className="standard-table">
                            <thead>
                                <tr>
                                    <th>Customer</th>
                                    <th className="text-right">Order Value</th>
                                    <th>Returning</th>
                                    <th>Items Ordered</th>
                                    <th className="text-right"># Orders (All)</th>
                                    <th className="text-right">Total Spent (All)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {customers.map((cust, idx) => (
                                    <tr key={idx}>
                                        <td>
                                            <span className="customer-name">
                                                {cust.name}
                                                {cust.is_verified && (
                                                    <span className="verified-badge" title="Verified">✓</span>
                                                )}
                                            </span>
                                        </td>
                                        <td className="text-right">{formatCurrency(cust.order_value)}</td>
                                        <td>
                                            {cust.is_returning ? (
                                                <span className="returning-yes">Yes</span>
                                            ) : (
                                                <span className="returning-no">No</span>
                                            )}
                                        </td>
                                        <td>
                                            <div className="items-list">
                                                {cust.items_ordered.slice(0, 5).map((item, i) => (
                                                    <span key={i} className="item-chip">{item}</span>
                                                ))}
                                                {cust.items_ordered.length > 5 && (
                                                    <span className="item-more">+{cust.items_ordered.length - 5} more</span>
                                                )}
                                            </div>
                                        </td>
                                        <td className="text-right">{cust.history_orders}</td>
                                        <td className="text-right">{formatCurrency(cust.history_spent)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </ResizableTableWrapper>
                ) : (
                    <p className="today-empty">No customers on this date</p>
                )}
            </Card>

            {/* Orders List */}
            <Card title="🧾 Orders">
                {orders.length > 0 ? (
                    <ResizableTableWrapper>
                        <table className="standard-table">
                            <thead>
                                <tr>
                                    <th>Customer</th>
                                    <th>Order Items</th>
                                    <th className="text-right">Total</th>
                                    <th>Order ID</th>
                                    <th>Time</th>
                                    <th>Source</th>
                                </tr>
                            </thead>
                            <tbody>
                                {orders.map((order) => (
                                    <tr key={order.order_id}>
                                        <td>{order.customer_name}</td>
                                        <td>
                                            <div className="items-list">
                                                {order.order_items.slice(0, 5).map((item, i) => (
                                                    <span
                                                        key={i}
                                                        className={`item-chip ${item.endsWith('-Repeat') ? 'repeat-item' : ''}`}
                                                    >
                                                        {item}
                                                    </span>
                                                ))}
                                                {order.order_items.length > 5 && (
                                                    <span className="item-more">+{order.order_items.length - 5} more</span>
                                                )}
                                            </div>
                                        </td>
                                        <td className="text-right">{formatCurrency(order.total)}</td>
                                        <td>{order.petpooja_order_id}</td>
                                        <td>{order.time}</td>
                                        <td>
                                            <span
                                                className="source-dot"
                                                style={{ backgroundColor: getSourceColor(order.source) }}
                                            />
                                            {order.source}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </ResizableTableWrapper>
                ) : (
                    <p className="today-empty">No orders on this date</p>
                )}
            </Card>
        </div>
    );
}
