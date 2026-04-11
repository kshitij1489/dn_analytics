import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { ResizableTableWrapper, TabButton, KPICard } from '../components';
import { exportToCSV } from '../utils/csv';
import { CUSTOMERS_ESTIMATE_HINT, formatCustomerEstimateRange } from '../utils/customerEstimateDisplay';
export default function Insights({ lastDbSync }: { lastDbSync?: number }) {
    const [activeTab, setActiveTab] = useState<'dailySales' | 'menu'>('dailySales');
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
                <KPICard
                    title="Total Customers (est.)"
                    value={formatCustomerEstimateRange(kpis)}
                    hint={CUSTOMERS_ESTIMATE_HINT}
                />
            </div>

            <hr style={{ margin: '20px 0', border: 'none', borderTop: '1px solid var(--border-color)' }} />

            {/* Tabs */}
            <div className="segmented-control" style={{ marginBottom: '8px', width: 'fit-content' }}>
                <TabButton
                    active={activeTab === 'dailySales'}
                    onClick={() => setActiveTab('dailySales')}
                    variant="segmented"
                    size="large"
                >
                    Daily Sales
                </TabButton>
                <TabButton
                    active={activeTab === 'menu'}
                    onClick={() => setActiveTab('menu')}
                    variant="segmented"
                    size="large"
                >
                    Menu Items
                </TabButton>
            </div>

            {/* Tab Content */}
            {activeTab === 'dailySales' && <DailySalesTab lastDbSync={lastDbSync} />}
            {activeTab === 'menu' && <MenuItemsTab lastDbSync={lastDbSync} />}
        </div>
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
            <div style={{ display: 'flex', gap: '15px', marginBottom: '8px' }}>
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

            <div style={{ display: 'flex', gap: '20px', marginBottom: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
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
                    <span style={{ marginRight: '5px', minWidth: '44px', textAlign: 'right', display: 'inline-block' }}>Days:</span>
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
                                <th className="text-right" onClick={() => handleSort('Total GMS')}>GMS{renderSortIcon('Total GMS')}</th>
                                <th className="text-right" onClick={() => handleSort('Total ML')}>ML{renderSortIcon('Total ML')}</th>
                                <th className="text-right" onClick={() => handleSort('Total COUNT')}>COUNT{renderSortIcon('Total COUNT')}</th>
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
                                    <td className="text-right">{row["Total GMS"] ? Math.round(row["Total GMS"]).toLocaleString() : '-'}</td>
                                    <td className="text-right">{row["Total ML"] ? Math.round(row["Total ML"]).toLocaleString() : '-'}</td>
                                    <td className="text-right">{row["Total COUNT"] ? Math.round(row["Total COUNT"]).toLocaleString() : '-'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            )}
        </div>
    );
}
