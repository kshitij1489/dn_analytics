import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { ResizableTableWrapper } from '../components/ResizableTableWrapper';
import { exportToCSV } from '../utils/csv';
export default function Insights({ lastDbSync }: { lastDbSync?: number }) {
    const [activeTab, setActiveTab] = useState('dailySales');
    const [kpis, setKpis] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadKPIs();
    }, [lastDbSync]);

    const loadKPIs = async () => {
        try {
            const res = await endpoints.insights.kpis({ _t: lastDbSync });
            setKpis(res.data);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    if (loading) return <div>Loading...</div>;

    return (
        <div>
            {/* KPIs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px', marginBottom: '30px' }}>
                <KPICard title="Total Revenue" value={`₹${kpis?.total_revenue?.toLocaleString() || 0}`} />
                <KPICard title="Today's Revenue" value={`₹${kpis?.today_revenue?.toLocaleString() || 0}`} />
                <KPICard title="Orders" value={kpis?.total_orders?.toLocaleString() || 0} />
                <KPICard title="Avg Order" value={`₹${kpis?.avg_order_value ? Math.round(kpis.avg_order_value).toLocaleString() : 0}`} />
                <KPICard title="Customers" value={kpis?.total_customers?.toLocaleString() || 0} />
            </div>

            <hr style={{ margin: '30px 0', border: 'none', borderTop: '1px solid #333' }} />

            {/* Tabs */}
            <div style={{ marginBottom: '20px' }}>
                <TabButton active={activeTab === 'dailySales'} onClick={() => setActiveTab('dailySales')}>📅 Daily Sales</TabButton>
                <TabButton active={activeTab === 'menu'} onClick={() => setActiveTab('menu')}>🗓️ Menu Items</TabButton>
                <TabButton active={activeTab === 'customer'} onClick={() => setActiveTab('customer')}>👥 Customer</TabButton>
            </div>

            {/* Tab Content */}
            {activeTab === 'dailySales' && <DailySalesTab lastDbSync={lastDbSync} />}
            {activeTab === 'menu' && <MenuItemsTab lastDbSync={lastDbSync} />}
            {activeTab === 'customer' && <CustomerTab lastDbSync={lastDbSync} />}
        </div>
    );
}


function KPICard({ title, value }: { title: string, value: string | number }) {
    return (
        <div style={{ background: 'var(--card-bg)', padding: '20px', borderRadius: '12px', border: '1px solid var(--border-color)', boxShadow: 'var(--shadow)' }}>
            <h3 style={{ margin: '0 0 10px 0', color: 'var(--text-secondary)', fontSize: '0.9em' }}>{title}</h3>
            <div style={{ fontSize: '1.8em', fontWeight: 'bold', color: 'var(--accent-color)' }}>{value}</div>
        </div>
    );
}

function TabButton({ children, active, onClick }: { children: React.ReactNode, active: boolean, onClick: () => void }) {
    return (
        <button
            onClick={onClick}
            style={{
                marginRight: '10px',
                background: active ? 'var(--accent-color)' : 'var(--button-bg)',
                color: active ? 'white' : 'var(--button-text)',
                border: active ? 'none' : '1px solid var(--border-color)',
                padding: '8px 16px',
                borderRadius: '8px', /* Rounded iOS buttons */
                cursor: 'pointer',
                fontWeight: '500'
            }}
        >
            {children}
        </button>
    );
}

function DailySalesTab({ lastDbSync }: { lastDbSync?: number }) {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [sortKey, setSortKey] = useState<string | null>(null);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

    useEffect(() => {
        loadData();
    }, [lastDbSync]);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.dailySales({ _t: lastDbSync });
            setData(res.data);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDirection('asc');
        }
    };

    const getSortedData = () => {
        if (!sortKey) return data;

        return [...data].sort((a, b) => {
            let aVal = a[sortKey];
            let bVal = b[sortKey];

            // Handle null/undefined values
            if (aVal == null) aVal = 0;
            if (bVal == null) bVal = 0;

            // Compare values
            if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
            return 0;
        });
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return ' ⇅';
        return sortDirection === 'asc' ? ' ↑' : ' ↓';
    };

    if (loading) return <div>Loading...</div>;

    const sortedData = getSortedData();

    const handleExportCSV = () => {
        exportToCSV(sortedData, 'daily_sales');
    };

    return (
        <div>
            <ResizableTableWrapper onExportCSV={handleExportCSV}>
                <table className="standard-table">
                    <thead>
                        <tr>
                            <th onClick={() => handleSort('order_date')}>Date{renderSortIcon('order_date')}</th>
                            <th className="text-right" onClick={() => handleSort('total_revenue')}>Total Revenue{renderSortIcon('total_revenue')}</th>
                            <th className="text-right" onClick={() => handleSort('net_revenue')}>Net Revenue{renderSortIcon('net_revenue')}</th>
                            <th className="text-right" onClick={() => handleSort('tax_collected')}>Tax{renderSortIcon('tax_collected')}</th>
                            <th className="text-right" onClick={() => handleSort('total_orders')}>Orders{renderSortIcon('total_orders')}</th>
                            <th className="text-right" onClick={() => handleSort('Website Revenue')}>Website Revenue{renderSortIcon('Website Revenue')}</th>
                            <th className="text-right" onClick={() => handleSort('POS Revenue')}>POS Revenue{renderSortIcon('POS Revenue')}</th>
                            <th className="text-right" onClick={() => handleSort('Swiggy Revenue')}>Swiggy Revenue{renderSortIcon('Swiggy Revenue')}</th>
                            <th className="text-right" onClick={() => handleSort('Zomato Revenue')}>Zomato Revenue{renderSortIcon('Zomato Revenue')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sortedData.map((row, idx) => (
                            <tr key={idx}>
                                <td>{row.order_date}</td>
                                <td className="text-right">₹{Math.round(row.total_revenue || 0).toLocaleString()}</td>
                                <td className="text-right">₹{Math.round(row.net_revenue || 0).toLocaleString()}</td>
                                <td className="text-right">₹{Math.round(row.tax_collected || 0).toLocaleString()}</td>
                                <td className="text-right">{row.total_orders}</td>
                                <td className="text-right">₹{Math.round(row['Website Revenue'] || 0).toLocaleString()}</td>
                                <td className="text-right">₹{Math.round(row['POS Revenue'] || 0).toLocaleString()}</td>
                                <td className="text-right">₹{Math.round(row['Swiggy Revenue'] || 0).toLocaleString()}</td>
                                <td className="text-right">₹{Math.round(row['Zomato Revenue'] || 0).toLocaleString()}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </ResizableTableWrapper>
        </div>
    );
}

function MenuItemsTab({ lastDbSync }: { lastDbSync?: number }) {
    const [data, setData] = useState<any[]>([]);
    const [types, setTypes] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [sortKey, setSortKey] = useState<string | null>(null);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

    const [nameSearch, setNameSearch] = useState('');
    const [typeFilter, setTypeFilter] = useState('All');

    // New Filters
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const [selectedDays, setSelectedDays] = useState<string[]>(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']);

    const ALL_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    const DAY_MAPPING: Record<string, string> = {
        'Mon': 'Monday',
        'Tue': 'Tuesday',
        'Wed': 'Wednesday',
        'Thu': 'Thursday',
        'Fri': 'Friday',
        'Sat': 'Saturday',
        'Sun': 'Sunday'
    };

    useEffect(() => {
        loadTypes();
        loadData();
    }, [lastDbSync]);

    useEffect(() => {
        const timer = setTimeout(() => loadData(), 300);
        return () => clearTimeout(timer);
    }, [nameSearch, typeFilter, startDate, endDate, selectedDays]);

    const loadTypes = async () => {
        try {
            const res = await endpoints.menu.types();
            setTypes(['All', ...res.data]);
        } catch (e) {
            console.error(e);
        }
    };

    const loadData = async () => {
        setLoading(true);
        try {
            // Map short days to full days for backend
            const mappedDays = selectedDays.map(d => DAY_MAPPING[d]);

            const res = await endpoints.menu.items({
                name_search: nameSearch || undefined,
                type_choice: typeFilter,
                start_date: startDate || undefined,
                end_date: endDate || undefined,
                days: selectedDays.length === 7 ? undefined : mappedDays
            });
            setData(res.data);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const toggleDay = (day: string) => {
        if (selectedDays.includes(day)) {
            setSelectedDays(selectedDays.filter(d => d !== day));
        } else {
            setSelectedDays([...selectedDays, day]);
        }
    };

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDirection('asc');
        }
    };

    const getSortedData = () => {
        if (!sortKey) return data;

        return [...data].sort((a, b) => {
            let aVal = a[sortKey];
            let bVal = b[sortKey];

            // Handle null/undefined values
            if (aVal == null) aVal = 0;
            if (bVal == null) bVal = 0;

            // Compare values
            if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
            return 0;
        });
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return ' ⇅';
        return sortDirection === 'asc' ? ' ↑' : ' ↓';
    };

    const sortedData = getSortedData();

    const handleExportCSV = () => {
        exportToCSV(sortedData, 'menu_items');
    };

    return (
        <div>
            <div style={{ display: 'flex', gap: '15px', marginBottom: '20px' }}>
                <input
                    type="text"
                    placeholder="Search Item..."
                    value={nameSearch}
                    onChange={(e) => setNameSearch(e.target.value)}
                    style={{ padding: '8px', flex: 1, borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                />
                <select
                    value={typeFilter}
                    onChange={(e) => setTypeFilter(e.target.value)}
                    style={{ padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                >
                    {types.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
            </div>

            <div style={{ display: 'flex', gap: '20px', marginBottom: '20px', alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <label>Start:</label>
                    <input
                        type="date"
                        value={startDate}
                        onChange={e => setStartDate(e.target.value)}
                        style={{ padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                    />
                    <label>End:</label>
                    <input
                        type="date"
                        value={endDate}
                        onChange={e => setEndDate(e.target.value)}
                        style={{ padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                    />
                </div>

                <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                    <span style={{ marginRight: '5px' }}>Days:</span>
                    {ALL_DAYS.map(day => (
                        <button
                            key={day}
                            onClick={() => toggleDay(day)}
                            style={{
                                padding: '8px 12px',
                                borderRadius: '4px',
                                border: '1px solid #444',
                                backgroundColor: selectedDays.includes(day) ? '#10b981' : '#333',
                                color: 'white',
                                cursor: 'pointer',
                                fontWeight: selectedDays.includes(day) ? 'bold' : 'normal',
                                transition: 'all 0.2s'
                            }}
                        >
                            {day[0]}
                        </button>
                    ))}
                </div>
            </div>

            {loading ? <div>Loading...</div> : (
                <ResizableTableWrapper onExportCSV={handleExportCSV}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th onClick={() => handleSort('Item Name')}>Item Name{renderSortIcon('Item Name')}</th>
                                <th onClick={() => handleSort('Type')}>Type{renderSortIcon('Type')}</th>
                                <th className="text-right" onClick={() => handleSort('As Addon (Qty)')}>As Addon (Qty){renderSortIcon('As Addon (Qty)')}</th>
                                <th className="text-right" onClick={() => handleSort('As Item (Qty)')}>As Item (Qty){renderSortIcon('As Item (Qty)')}</th>
                                <th className="text-right" onClick={() => handleSort('Total Sold (Qty)')}>Total Sold (Qty){renderSortIcon('Total Sold (Qty)')}</th>
                                <th className="text-right" onClick={() => handleSort('Total Revenue')}>Total Revenue{renderSortIcon('Total Revenue')}</th>
                                <th className="text-right" onClick={() => handleSort('Reorder Count')}>Reorder Count{renderSortIcon('Reorder Count')}</th>
                                <th className="text-right" onClick={() => handleSort('Repeat Customer (Lifetime)')}>Repeat Customers{renderSortIcon('Repeat Customer (Lifetime)')}</th>
                                <th className="text-right" onClick={() => handleSort('Unique Customers')}>Unique Customers{renderSortIcon('Unique Customers')}</th>
                                <th className="text-right" onClick={() => handleSort('Reorder Rate %')}>Reorder Rate %{renderSortIcon('Reorder Rate %')}</th>
                                <th className="text-right" onClick={() => handleSort('Repeat Revenue %')}>Repeat Revenue %{renderSortIcon('Repeat Revenue %')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedData.map((row, idx) => (
                                <tr key={idx}>
                                    <td>{row["Item Name"]}</td>
                                    <td>{row["Type"]}</td>
                                    <td className="text-right">{row["As Addon (Qty)"]}</td>
                                    <td className="text-right">{row["As Item (Qty)"]}</td>
                                    <td className="text-right">{row["Total Sold (Qty)"]}</td>
                                    <td className="text-right">₹{Math.round(row["Total Revenue"] || 0).toLocaleString()}</td>
                                    <td className="text-right">{row["Reorder Count"] || 0}</td>
                                    <td className="text-right">{row["Repeat Customer (Lifetime)"]}</td>
                                    <td className="text-right">{row["Unique Customers"]}</td>
                                    <td className="text-right">{row["Reorder Rate %"]}%</td>
                                    <td className="text-right">{row["Repeat Revenue %"]}%</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            )}
        </div>
    );
}

function CustomerTab({ lastDbSync }: { lastDbSync?: number }) {
    const [customerView, setCustomerView] = useState('loyalty');
    const [reorderRate, setReorderRate] = useState<any>(null);
    const [loyaltyData, setLoyaltyData] = useState<any[]>([]);
    const [topCustomers, setTopCustomers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [sortKey, setSortKey] = useState<string | null>(null);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

    useEffect(() => {
        loadData();
    }, [customerView, lastDbSync]);

    const loadData = async () => {
        setLoading(true);
        try {
            const rateRes = await endpoints.insights.customerReorderRate({ _t: lastDbSync });
            setReorderRate(rateRes.data);

            if (customerView === 'loyalty') {
                const loyaltyRes = await endpoints.insights.customerLoyalty({ _t: lastDbSync });
                setLoyaltyData(loyaltyRes.data);
            } else {
                const topRes = await endpoints.insights.topCustomers({ _t: lastDbSync });
                setTopCustomers(topRes.data);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDirection('asc');
        }
    };

    const getSortedLoyaltyData = () => {
        if (!sortKey) return loyaltyData;

        return [...loyaltyData].sort((a, b) => {
            let aVal = a[sortKey];
            let bVal = b[sortKey];

            // Handle null/undefined values
            if (aVal == null) aVal = 0;
            if (bVal == null) bVal = 0;

            // Compare values
            if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
            return 0;
        });
    };

    const getSortedTopCustomers = () => {
        if (!sortKey) return topCustomers;

        return [...topCustomers].sort((a, b) => {
            let aVal = a[sortKey];
            let bVal = b[sortKey];

            // Handle null/undefined values
            if (aVal == null) aVal = 0;
            if (bVal == null) bVal = 0;

            // Compare values
            if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
            if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
            return 0;
        });
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return ' ⇅';
        return sortDirection === 'asc' ? ' ↑' : ' ↓';
    };

    const sortedLoyaltyData = getSortedLoyaltyData();
    const sortedTopCustomers = getSortedTopCustomers();

    const handleExportLoyaltyCSV = () => {
        exportToCSV(sortedLoyaltyData, 'customer_retention');
    };

    const handleExportTopCustomersCSV = () => {
        exportToCSV(sortedTopCustomers, 'top_customers');
    };

    return (
        <div>
            {/* KPIs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px', marginBottom: '30px' }}>
                <KPICard title="Total Customers" value={reorderRate?.total_customers?.toLocaleString() || 0} />
                <KPICard title="Returning Customers" value={reorderRate?.returning_customers?.toLocaleString() || 0} />
                <KPICard title="Return Rate" value={`${reorderRate?.reorder_rate?.toFixed(1) || 0}%`} />
            </div>

            <hr style={{ margin: '30px 0', border: 'none', borderTop: '1px solid #333' }} />

            {/* View Toggle */}
            <div style={{ marginBottom: '20px' }}>
                <button
                    onClick={() => setCustomerView('loyalty')}
                    style={{
                        padding: '10px 20px',
                        marginRight: '10px',
                        background: customerView === 'loyalty' ? '#EF4444' : 'var(--card-bg)',
                        color: customerView === 'loyalty' ? 'white' : 'var(--text-color)',
                        border: customerView === 'loyalty' ? 'none' : '1px solid var(--border-color)',
                        borderRadius: '12px',
                        cursor: 'pointer',
                        fontWeight: '500',
                        transition: 'all 0.2s'
                    }}
                >
                    🔄 Customer Retention
                </button>
                <button
                    onClick={() => setCustomerView('top')}
                    style={{
                        padding: '10px 20px',
                        background: customerView === 'top' ? '#EF4444' : 'var(--card-bg)',
                        color: customerView === 'top' ? 'white' : 'var(--text-color)',
                        border: customerView === 'top' ? 'none' : '1px solid var(--border-color)',
                        borderRadius: '12px',
                        cursor: 'pointer',
                        fontWeight: '500',
                        transition: 'all 0.2s'
                    }}
                >
                    💎 Top Verified Customers
                </button>
            </div>

            <hr style={{ margin: '30px 0', border: 'none', borderTop: '1px solid #333' }} />

            {/* Data Display */}
            {loading ? <div>Loading...</div> : (
                customerView === 'loyalty' ? (
                    <ResizableTableWrapper onExportCSV={handleExportLoyaltyCSV}>
                        <table className="standard-table">
                            <thead>
                                <tr>
                                    <th onClick={() => handleSort('Month')}>Month{renderSortIcon('Month')}</th>
                                    <th className="text-right" onClick={() => handleSort('Repeat Orders')}>Repeat Orders{renderSortIcon('Repeat Orders')}</th>
                                    <th className="text-right" onClick={() => handleSort('Total Orders')}>Total Orders{renderSortIcon('Total Orders')}</th>
                                    <th className="text-right" onClick={() => handleSort('Order Repeat%')}>Order Repeat%{renderSortIcon('Order Repeat%')}</th>
                                    <th className="text-right" onClick={() => handleSort('Repeat Customer Count')}>Repeat Customer Count{renderSortIcon('Repeat Customer Count')}</th>
                                    <th className="text-right" onClick={() => handleSort('Total Verified Customers')}>Total Verified Customers{renderSortIcon('Total Verified Customers')}</th>
                                    <th className="text-right" onClick={() => handleSort('Repeat Customer %')}>Repeat Customer %{renderSortIcon('Repeat Customer %')}</th>
                                    <th className="text-right" onClick={() => handleSort('Repeat Revenue')}>Repeat Revenue{renderSortIcon('Repeat Revenue')}</th>
                                    <th className="text-right" onClick={() => handleSort('Total Revenue')}>Total Revenue{renderSortIcon('Total Revenue')}</th>
                                    <th className="text-right" onClick={() => handleSort('Revenue Repeat %')}>Revenue Repeat %{renderSortIcon('Revenue Repeat %')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {sortedLoyaltyData.map((row, idx) => (
                                    <tr key={idx}>
                                        <td>{row.Month}</td>
                                        <td className="text-right">{row['Repeat Orders']}</td>
                                        <td className="text-right">{row['Total Orders']}</td>
                                        <td className="text-right">{row['Order Repeat%']}%</td>
                                        <td className="text-right">{row['Repeat Customer Count']}</td>
                                        <td className="text-right">{row['Total Verified Customers']}</td>
                                        <td className="text-right">{row['Repeat Customer %']}%</td>
                                        <td className="text-right">₹{Math.round(row['Repeat Revenue'] || 0).toLocaleString()}</td>
                                        <td className="text-right">₹{Math.round(row['Total Revenue'] || 0).toLocaleString()}</td>
                                        <td className="text-right">{row['Revenue Repeat %']}%</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </ResizableTableWrapper>
                ) : (
                    <ResizableTableWrapper onExportCSV={handleExportTopCustomersCSV}>
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
                                        <td>{row.name}</td>
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










