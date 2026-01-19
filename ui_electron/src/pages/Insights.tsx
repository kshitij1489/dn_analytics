import { useEffect, useState, useRef } from 'react';
import { endpoints } from '../api';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine, LabelList, Brush } from 'recharts';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';

const INDIAN_HOLIDAYS = [
    { date: "2025-01-26", name: "Republic Day 🇮🇳" },
    { date: "2025-02-26", name: "Maha Shivratri" },
    { date: "2025-03-14", name: "Holi" },
    { date: "2025-03-31", name: "Id-ul-Fitr (Eid)" },
    { date: "2025-04-10", name: "Mahavir Jayanti" },
    { date: "2025-04-14", name: "Dr. B. R. Ambedkar Jayanti" },
    { date: "2025-04-18", name: "Good Friday" },
    { date: "2025-05-12", name: "Buddha Purnima" },
    { date: "2025-06-07", name: "Id-ul-Zuha (Bakrid)" },
    { date: "2025-07-06", name: "Muharram" },
    { date: "2025-08-15", name: "Independence Day 🇮🇳" },
    { date: "2025-08-16", name: "Janmashtami" },
    { date: "2025-10-02", name: "Gandhi Jayanti 🇮🇳" },
    { date: "2025-10-20", name: "Diwali (Deepavali)" },
    { date: "2025-11-05", name: "Guru Nanak Jayanti" },
    { date: "2025-12-25", name: "Christmas" },
    { date: "2026-01-26", name: "Republic Day 🇮🇳" },
    { date: "2026-03-04", name: "Holi" },
    { date: "2026-03-21", name: "Id-ul-Fitr (Eid)" },
    { date: "2026-03-26", name: "Ram Navami" },
    { date: "2026-03-31", name: "Mahavir Jayanti" },
    { date: "2026-04-03", name: "Good Friday" },
    { date: "2026-05-01", name: "Buddha Purnima" },
    { date: "2026-05-27", name: "Id-ul-Zuha (Bakrid)" },
    { date: "2026-06-26", name: "Muharram" },
    { date: "2026-08-15", name: "Independence Day 🇮🇳" },
    { date: "2026-08-26", name: "Milad-un-Nabi / Id-e-Milad" },
    { date: "2026-09-04", name: "Janmashtami" },
    { date: "2026-10-02", name: "Gandhi Jayanti 🇮🇳" },
    { date: "2026-10-20", name: "Dussehra" },
    { date: "2026-11-08", name: "Diwali" },
    { date: "2026-11-24", name: "Guru Nanak Jayanti" },
    { date: "2026-12-25", name: "Christmas" }
];

export default function Insights() {
    const [activeTab, setActiveTab] = useState('dailySales');
    const [kpis, setKpis] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadKPIs();
    }, []);

    const loadKPIs = async () => {
        try {
            const res = await endpoints.insights.kpis();
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
            <h1>📊 Executive Insights</h1>

            {/* KPIs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px', marginBottom: '30px' }}>
                <KPICard title="Revenue" value={`₹${kpis?.total_revenue?.toLocaleString() || 0}`} />
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
                <TabButton active={activeTab === 'charts'} onClick={() => setActiveTab('charts')}>📈 Charts</TabButton>
            </div>

            {/* Tab Content */}
            {activeTab === 'dailySales' && <DailySalesTab />}
            {activeTab === 'menu' && <MenuItemsTab />}
            {activeTab === 'customer' && <CustomerTab />}
            {activeTab === 'charts' && <ChartsTab />}
        </div>
    );
}

// Resizable Chart Wrapper
function ResizableChart({
    children,
    defaultHeight = 400,
    onFullscreen
}: {
    children: React.ReactNode;
    defaultHeight?: number;
    onFullscreen: () => void;
}) {
    const containerRef = useRef<HTMLDivElement>(null);
    const [height, setHeight] = useState(defaultHeight);
    const [width, setWidth] = useState(800);

    // Set initial width to match container width
    useEffect(() => {
        if (containerRef.current) {
            setWidth(containerRef.current.offsetWidth);
        }
    }, []);

    return (
        <div ref={containerRef} style={{ position: 'relative', width: '100%' }}>
            <button
                onClick={onFullscreen}
                title="Open in fullscreen"
                style={{
                    position: 'absolute',
                    top: '10px',
                    right: '10px',
                    zIndex: 10,
                    background: '#646cff',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '6px 12px',
                    color: 'white',
                    cursor: 'pointer',
                    fontSize: '12px',
                    fontWeight: 'bold'
                }}
            >
                ⛶ Fullscreen
            </button>
            <Resizable
                height={height}
                width={width}
                onResize={(_e, { size }) => {
                    setHeight(size.height);
                    setWidth(size.width);
                }}
                resizeHandles={['s', 'e', 'se', 'sw', 'ne', 'nw', 'n', 'w']}
                minConstraints={[400, 300]}
                maxConstraints={[1600, 800]}
            >
                <div style={{ height: `${height}px`, width: `${width}px` }}>
                    {children}
                </div>
            </Resizable>
        </div>
    );
}

// Fullscreen Modal
function FullscreenModal({
    isOpen,
    onClose,
    children
}: {
    isOpen: boolean;
    onClose: () => void;
    children: React.ReactNode;
}) {
    if (!isOpen) return null;

    return (
        <div style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0, 0, 0, 0.95)',
            zIndex: 9999,
            padding: '20px',
            display: 'flex',
            flexDirection: 'column'
        }}>
            <button
                onClick={onClose}
                style={{
                    alignSelf: 'flex-end',
                    background: '#646cff',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '8px 16px',
                    color: 'white',
                    cursor: 'pointer',
                    marginBottom: '10px',
                    fontSize: '14px',
                    fontWeight: 'bold'
                }}
            >
                ✕ Close Fullscreen
            </button>
            <div style={{ flex: 1, overflow: 'auto' }}>
                {children}
            </div>
        </div>
    );
}

function KPICard({ title, value }: { title: string, value: string | number }) {
    return (
        <div className="card">
            <h3 style={{ fontSize: '0.9em', color: '#aaa', margin: '0 0 10px 0' }}>{title}</h3>
            <div style={{ fontSize: '1.8em', fontWeight: 'bold' }}>{value}</div>
        </div>
    );
}

function TabButton({ active, onClick, children }: any) {
    return (
        <button
            onClick={onClick}
            style={{
                padding: '10px 20px',
                marginRight: '10px',
                background: active ? '#646cff' : '#333',
                color: 'white',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer'
            }}
        >
            {children}
        </button>
    );
}

function DailySalesTab() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.dailySales();
            setData(res.data);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    if (loading) return <div>Loading...</div>;

    return (
        <div className="card">
            <h2>Daily Sales Performance</h2>
            <div style={{ overflowX: 'auto', maxHeight: '600px' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                    <thead style={{ position: 'sticky', top: 0, background: '#2d2d2d' }}>
                        <tr style={{ borderBottom: '2px solid #444', textAlign: 'left' }}>
                            <th style={{ padding: '10px' }}>Date</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>Total Revenue</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>Net Revenue</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>Tax</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>Orders</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>Website Revenue</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>POS Revenue</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>Swiggy Revenue</th>
                            <th style={{ padding: '10px', textAlign: 'right' }}>Zomato Revenue</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.map((row, idx) => (
                            <tr key={idx} style={{ borderBottom: '1px solid #333' }}>
                                <td style={{ padding: '10px' }}>{row.order_date}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row.total_revenue || 0).toLocaleString()}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row.net_revenue || 0).toLocaleString()}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row.tax_collected || 0).toLocaleString()}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>{row.total_orders}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row['Website Revenue'] || 0).toLocaleString()}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row['POS Revenue'] || 0).toLocaleString()}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row['Swiggy Revenue'] || 0).toLocaleString()}</td>
                                <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row['Zomato Revenue'] || 0).toLocaleString()}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function MenuItemsTab() {
    const [data, setData] = useState<any[]>([]);
    const [types, setTypes] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);

    const [nameSearch, setNameSearch] = useState('');
    const [typeFilter, setTypeFilter] = useState('All');

    useEffect(() => {
        loadTypes();
        loadData();
    }, []);

    useEffect(() => {
        const timer = setTimeout(() => loadData(), 300);
        return () => clearTimeout(timer);
    }, [nameSearch, typeFilter]);

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
            const res = await endpoints.menu.items({
                name_search: nameSearch || undefined,
                type_choice: typeFilter
            });
            setData(res.data);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
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

            <div className="card" style={{ overflowX: 'auto', maxHeight: '600px' }}>
                {loading ? <div>Loading...</div> : (
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                        <thead style={{ position: 'sticky', top: 0, background: '#2d2d2d' }}>
                            <tr style={{ borderBottom: '2px solid #444', textAlign: 'left' }}>
                                <th style={{ padding: '10px' }}>Item Name</th>
                                <th style={{ padding: '10px' }}>Type</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>As Addon (Qty)</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>As Item (Qty)</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Total Sold (Qty)</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Total Revenue</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Reorder Count</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Repeat Customers</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Unique Customers</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Reorder Rate %</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Repeat Revenue %</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.map((row, idx) => (
                                <tr key={idx} style={{ borderBottom: '1px solid #333' }}>
                                    <td style={{ padding: '10px' }}>{row["Item Name"]}</td>
                                    <td style={{ padding: '10px' }}>{row["Type"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["As Addon (Qty)"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["As Item (Qty)"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Total Sold (Qty)"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row["Total Revenue"] || 0).toLocaleString()}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Reorder Count"] || 0}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Repeat Customer (Lifetime)"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Unique Customers"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Reorder Rate %"]}%</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Repeat Revenue %"]}%</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}

function CustomerTab() {
    const [customerView, setCustomerView] = useState('loyalty');
    const [reorderRate, setReorderRate] = useState<any>(null);
    const [loyaltyData, setLoyaltyData] = useState<any[]>([]);
    const [topCustomers, setTopCustomers] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        loadData();
    }, [customerView]);

    const loadData = async () => {
        setLoading(true);
        try {
            const rateRes = await endpoints.insights.customerReorderRate();
            setReorderRate(rateRes.data);

            if (customerView === 'loyalty') {
                const loyaltyRes = await endpoints.insights.customerLoyalty();
                setLoyaltyData(loyaltyRes.data);
            } else {
                const topRes = await endpoints.insights.topCustomers();
                setTopCustomers(topRes.data);
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
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
                        background: customerView === 'loyalty' ? '#646cff' : '#333',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer'
                    }}
                >
                    🔄 Customer Retention
                </button>
                <button
                    onClick={() => setCustomerView('top')}
                    style={{
                        padding: '10px 20px',
                        background: customerView === 'top' ? '#646cff' : '#333',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: 'pointer'
                    }}
                >
                    💎 Top Verified Customers
                </button>
            </div>

            <hr style={{ margin: '30px 0', border: 'none', borderTop: '1px solid #333' }} />

            {/* Data Display */}
            {loading ? <div>Loading...</div> : (
                <div className="card" style={{ overflowX: 'auto', maxHeight: '500px' }}>
                    {customerView === 'loyalty' ? (
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                            <thead style={{ position: 'sticky', top: 0, background: '#2d2d2d' }}>
                                <tr style={{ borderBottom: '2px solid #444', textAlign: 'left' }}>
                                    <th style={{ padding: '10px' }}>Month</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Repeat Orders</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Total Orders</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Order Repeat%</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Repeat Customer Count</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Total Verified Customers</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Repeat Customer %</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Repeat Revenue</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Total Revenue</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Revenue Repeat %</th>
                                </tr>
                            </thead>
                            <tbody>
                                {loyaltyData.map((row, idx) => (
                                    <tr key={idx} style={{ borderBottom: '1px solid #333' }}>
                                        <td style={{ padding: '10px' }}>{row.Month}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row['Repeat Orders']}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row['Total Orders']}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row['Order Repeat%']}%</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row['Repeat Customer Count']}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row['Total Verified Customers']}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row['Repeat Customer %']}%</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row['Repeat Revenue'] || 0).toLocaleString()}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row['Total Revenue'] || 0).toLocaleString()}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row['Revenue Repeat %']}%</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    ) : (
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                            <thead style={{ position: 'sticky', top: 0, background: '#2d2d2d' }}>
                                <tr style={{ borderBottom: '2px solid #444', textAlign: 'left' }}>
                                    <th style={{ padding: '10px' }}>Customer</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Total Orders</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Total Spent</th>
                                    <th style={{ padding: '10px' }}>Last Order Date</th>
                                    <th style={{ padding: '10px' }}>Status</th>
                                    <th style={{ padding: '10px' }}>Favorite Item</th>
                                    <th style={{ padding: '10px', textAlign: 'right' }}>Fav Item Qty</th>
                                </tr>
                            </thead>
                            <tbody>
                                {topCustomers.map((row, idx) => (
                                    <tr key={idx} style={{ borderBottom: '1px solid #333' }}>
                                        <td style={{ padding: '10px' }}>{row.name}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row.total_orders}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row.total_spent || 0).toLocaleString()}</td>
                                        <td style={{ padding: '10px' }}>{row.last_order_date}</td>
                                        <td style={{ padding: '10px' }}>
                                            <span style={{
                                                padding: '2px 8px',
                                                borderRadius: '3px',
                                                fontSize: '11px',
                                                backgroundColor: row.status === 'Returning' ? '#064e3b' : '#422006',
                                                color: row.status === 'Returning' ? '#6ee7b7' : '#fbbf24'
                                            }}>
                                                {row.status}
                                            </span>
                                        </td>
                                        <td style={{ padding: '10px' }}>{row.favorite_item}</td>
                                        <td style={{ padding: '10px', textAlign: 'right' }}>{row.fav_item_qty}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            )}
        </div>
    );
}

function ChartsTab() {
    const [chartType, setChartType] = useState('salesTrend');

    return (
        <div>
            <select
                value={chartType}
                onChange={(e) => setChartType(e.target.value)}
                style={{ padding: '10px', marginBottom: '20px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
            >
                <option value="salesTrend">📈 Daily Sales Trend</option>
                <option value="categoryTrend">📉 Sales by Category Trend</option>
                <option value="revenueVsOrders">🖇️ Revenue vs Orders</option>
                <option value="aovTrend">📊 Average Order Value Trend</option>
                <option value="topItems">🏆 Top 10 Items</option>
                <option value="revenueByCategory">📂 Revenue by Category</option>
                <option value="hourlyRevenue">⏰ Hourly Revenue</option>
                <option value="orderSource">🛵 Order Source</option>
            </select>

            {chartType === 'salesTrend' && <SalesTrendChart />}
            {chartType === 'categoryTrend' && <CategoryTrendChart />}
            {chartType === 'revenueVsOrders' && <RevenueVsOrdersChart />}
            {chartType === 'aovTrend' && <AverageOrderValueChart />}
            {chartType === 'topItems' && <TopItemsChart />}
            {chartType === 'revenueByCategory' && <RevenueByCategoryChart />}
            {chartType === 'hourlyRevenue' && <HourlyRevenueChart />}
            {chartType === 'orderSource' && <OrderSourceChart />}
        </div>
    );
}

function SalesTrendChart() {
    const [data, setData] = useState<any[]>([]);
    const [metric, setMetric] = useState('Moving Average (7-day)');
    const [timeBucket, setTimeBucket] = useState('Day');
    const [showHolidays, setShowHolidays] = useState(false);
    const [selectedDays, setSelectedDays] = useState(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
    const [isFullscreen, setIsFullscreen] = useState(false);

    const daysOfWeek = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const daysAbbr = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.salesTrend();
            setData(res.data);
        } catch (e) {
            console.error(e);
        }
    };

    const toggleDay = (day: string) => {
        if (selectedDays.includes(day)) {
            setSelectedDays(selectedDays.filter(d => d !== day));
        } else {
            setSelectedDays([...selectedDays, day]);
        }
    };

    const processData = () => {
        if (!data.length) return [];

        // Filter by selected weekdays
        const filtered = data.filter(row => {
            const date = new Date(row.date);
            const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
            return selectedDays.includes(dayName);
        });

        // Group by time bucket and apply metric
        const grouped = groupByTimeBucket(filtered, timeBucket);
        return applyMetric(grouped, metric);
    };

    const groupByTimeBucket = (data: any[], bucket: string) => {
        if (bucket === 'Day' || metric === 'Moving Average (7-day)') return data;

        const groups: { [key: string]: any[] } = {};
        data.forEach(row => {
            const dateStr = row.date;
            const [year, month, day] = dateStr.split('-').map(Number);
            let key: string;

            if (bucket === 'Week') {
                // Match pandas resample('W') behavior: week ENDS on Sunday
                const date = new Date(year, month - 1, day);
                const dayOfWeek = date.getDay();  // 0 = Sunday

                // Calculate the Sunday that ends this week
                const weekEnd = new Date(year, month - 1, day + (7 - dayOfWeek) % 7);
                const weekYear = weekEnd.getFullYear();
                const weekMonth = String(weekEnd.getMonth() + 1).padStart(2, '0');
                const weekDay = String(weekEnd.getDate()).padStart(2, '0');
                key = `${weekYear}-${weekMonth}-${weekDay}`;
            } else { // Month
                key = `${year}-${String(month).padStart(2, '0')}-01`;
            }

            if (!groups[key]) groups[key] = [];
            groups[key].push(row);
        });

        return Object.keys(groups).sort().map(key => ({
            date: key,
            revenue: groups[key].reduce((sum, r) => sum + (r.revenue || 0), 0),
            num_orders: groups[key].reduce((sum, r) => sum + (r.num_orders || 0), 0)
        }));
    };

    const applyMetric = (data: any[], metric: string) => {
        if (metric === 'Total') {
            return data.map(d => ({ ...d, value: d.revenue }));
        } else if (metric === 'Average') {
            const grouped = groupByTimeBucket(data, timeBucket);
            return grouped.map(d => ({ ...d, value: d.revenue / (timeBucket === 'Day' ? 1 : data.filter(r => r.date === d.date).length || 1) }));
        } else if (metric === 'Cumulative') {
            let cumulative = 0;
            return data.map(d => {
                cumulative += d.revenue;
                return { ...d, value: cumulative };
            });
        } else { // Moving Average (7-day)
            return data.map((d, idx) => {
                const window = data.slice(Math.max(0, idx - 6), idx + 1);
                const avg = window.reduce((sum, r) => sum + (r.revenue || 0), 0) / window.length;
                return { ...d, value: avg };
            });
        }
    };

    const chartData = processData();

    // Get holidays within the visible date range
    const getVisibleHolidays = () => {
        if (!chartData.length || !showHolidays) return [];

        const dates = chartData.map(d => d.date);
        const minDate = dates[0];
        const maxDate = dates[dates.length - 1];

        return INDIAN_HOLIDAYS.filter(h => h.date >= minDate && h.date <= maxDate);
    };

    const visibleHolidays = getVisibleHolidays();

    return (
        <>
            <div className="card">
                <h3>Daily Sales Trend</h3>

                {/* Controls */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px', marginBottom: '20px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Metric</label>
                        <select
                            value={metric}
                            onChange={(e) => setMetric(e.target.value)}
                            style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                        >
                            <option>Total</option>
                            <option>Average</option>
                            <option>Cumulative</option>
                            <option>Moving Average (7-day)</option>
                        </select>
                    </div>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Time Bucket</label>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <select
                                value={timeBucket}
                                onChange={(e) => setTimeBucket(e.target.value)}
                                disabled={metric === 'Moving Average (7-day)'}
                                style={{
                                    flex: 1,
                                    padding: '8px',
                                    borderRadius: '4px',
                                    border: '1px solid #444',
                                    backgroundColor: metric === 'Moving Average (7-day)' ? '#222' : '#333',
                                    color: metric === 'Moving Average (7-day)' ? '#666' : 'white',
                                    cursor: metric === 'Moving Average (7-day)' ? 'not-allowed' : 'pointer'
                                }}
                            >
                                <option>Day</option>
                                <option>Week</option>
                                <option>Month</option>
                            </select>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '14px', color: '#aaa', whiteSpace: 'nowrap' }}>
                                <input
                                    type="checkbox"
                                    checked={showHolidays}
                                    onChange={(e) => setShowHolidays(e.target.checked)}
                                    style={{ cursor: 'pointer' }}
                                />
                                Holidays
                            </label>
                        </div>
                    </div>
                </div>

                {/* Weekday Toggles */}
                <div style={{ marginBottom: '20px' }}>
                    <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', color: '#aaa' }}>Include Days:</label>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        {daysOfWeek.map((day, idx) => (
                            <button
                                key={day}
                                onClick={() => toggleDay(day)}
                                style={{
                                    flex: 1,
                                    padding: '8px',
                                    background: selectedDays.includes(day) ? '#646cff' : '#333',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    fontSize: '13px',
                                    fontWeight: selectedDays.includes(day) ? 'bold' : 'normal'
                                }}
                            >
                                {daysAbbr[idx]}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="date" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip
                                contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                formatter={(value: any, _name: string | undefined, props: any) => {
                                    const payload = props.payload;
                                    return [
                                        `Revenue: ₹${Math.round(value).toLocaleString()}`,
                                        `Orders: ${Math.round(payload.num_orders || 0)}`
                                    ];
                                }}
                                labelFormatter={(label) => `Period: ${label}`}
                            />
                            <Legend />
                            <Line type="monotone" dataKey="value" stroke="#3B82F6" name={`${metric} Revenue (₹)`} strokeWidth={2} dot={{ r: 3 }} />

                            {/* Brush for Zoom */}
                            <Brush dataKey="date" height={30} stroke="#646cff" />

                            {/* Holiday Reference Lines */}
                            {visibleHolidays.map((holiday, idx) => (
                                <ReferenceLine
                                    key={idx}
                                    x={holiday.date}
                                    stroke="#F59E0B"
                                    strokeDasharray="4 4"
                                    strokeWidth={2}
                                    label={{
                                        value: holiday.name,
                                        position: 'top',
                                        fill: '#F59E0B',
                                        fontSize: 11,
                                        offset: 10
                                    }}
                                />
                            ))}
                        </LineChart>
                    </ResponsiveContainer>
                </ResizableChart>

                {metric === 'Moving Average (7-day)' && (
                    <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                        * Time bucket is disabled for Moving Average (calculates daily)
                    </p>
                )}
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Sales Trend Analysis (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="date" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip
                                    contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                    formatter={(value: any, _name: string | undefined, props: any) => {
                                        const payload = props.payload;
                                        return [
                                            `Revenue: ₹${Math.round(value).toLocaleString()}`,
                                            `Orders: ${Math.round(payload.num_orders || 0)}`
                                        ];
                                    }}
                                    labelFormatter={(label) => `Period: ${label}`}
                                />
                                <Legend />
                                <Line type="monotone" dataKey="value" stroke="#3B82F6" name={`${metric} Revenue (₹)`} strokeWidth={2} dot={{ r: 3 }} />
                                <Brush dataKey="date" height={30} stroke="#646cff" />
                                {visibleHolidays.map((holiday, idx) => (
                                    <ReferenceLine
                                        key={idx}
                                        x={holiday.date}
                                        stroke="#F59E0B"
                                        strokeDasharray="4 4"
                                        strokeWidth={2}
                                        label={{
                                            value: holiday.name,
                                            position: 'top',
                                            fill: '#F59E0B',
                                            fontSize: 11,
                                            offset: 10
                                        }}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

function CategoryTrendChart() {
    const [data, setData] = useState<any[]>([]);
    const [metric, setMetric] = useState('Total');
    const [timeBucket, setTimeBucket] = useState('Day');
    const [showHolidays, setShowHolidays] = useState(false);
    const [selectedDays, setSelectedDays] = useState(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
    const [isFullscreen, setIsFullscreen] = useState(false);

    const daysOfWeek = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const daysAbbr = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.categoryTrend();
            setData(res.data);
        } catch (e) {
            console.error(e);
        }
    };

    const toggleDay = (day: string) => {
        if (selectedDays.includes(day)) {
            setSelectedDays(selectedDays.filter(d => d !== day));
        } else {
            setSelectedDays([...selectedDays, day]);
        }
    };

    const processData = () => {
        if (!data.length) return [];

        // Filter by selected weekdays
        const filtered = data.filter(row => {
            const date = new Date(row.date);
            const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
            return selectedDays.includes(dayName);
        });

        // Group by category
        const categories = [...new Set(filtered.map(row => row.category))];
        const result: any[] = [];

        categories.forEach(category => {
            const categoryData = filtered.filter(row => row.category === category);
            const grouped = groupByTimeBucket(categoryData, timeBucket);
            const processed = applyMetric(grouped, metric);

            processed.forEach(item => {
                result.push({
                    date: item.date,
                    category: category,
                    value: item.value
                });
            });
        });

        return result;
    };

    const groupByTimeBucket = (data: any[], bucket: string) => {
        if (bucket === 'Day' || metric === 'Moving Average (7-day)') return data;

        const groups: { [key: string]: any[] } = {};
        data.forEach(row => {
            const dateStr = row.date;
            const [year, month, day] = dateStr.split('-').map(Number);
            let key: string;

            if (bucket === 'Week') {
                const date = new Date(year, month - 1, day);
                const dayOfWeek = date.getDay();
                const weekStart = new Date(year, month - 1, day - dayOfWeek);
                const weekYear = weekStart.getFullYear();
                const weekMonth = String(weekStart.getMonth() + 1).padStart(2, '0');
                const weekDay = String(weekStart.getDate()).padStart(2, '0');
                key = `${weekYear}-${weekMonth}-${weekDay}`;
            } else { // Month
                key = `${year}-${String(month).padStart(2, '0')}-01`;
            }

            if (!groups[key]) groups[key] = [];
            groups[key].push(row);
        });

        return Object.keys(groups).sort().map(key => ({
            date: key,
            revenue: groups[key].reduce((sum, r) => sum + (r.revenue || 0), 0)
        }));
    };

    const applyMetric = (data: any[], metric: string) => {
        if (metric === 'Total') {
            return data.map(d => ({ ...d, value: d.revenue }));
        } else if (metric === 'Average') {
            const grouped = groupByTimeBucket(data, timeBucket);
            return grouped.map(d => ({ ...d, value: d.revenue / (timeBucket === 'Day' ? 1 : data.filter(r => r.date === d.date).length || 1) }));
        } else if (metric === 'Cumulative') {
            let cumulative = 0;
            return data.map(d => {
                cumulative += d.revenue;
                return { ...d, value: cumulative };
            });
        } else { // Moving Average (7-day)
            return data.map((d, idx) => {
                const window = data.slice(Math.max(0, idx - 6), idx + 1);
                const avg = window.reduce((sum, r) => sum + (r.revenue || 0), 0) / window.length;
                return { ...d, value: avg };
            });
        }
    };

    const chartData = processData();

    // Get unique categories for rendering separate lines
    const categories = [...new Set(chartData.map(d => d.category))];
    const colors = ['#3B82F6', '#EF4444', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#06B6D4'];

    // Get holidays within the visible date range
    const getVisibleHolidays = () => {
        if (!chartData.length || !showHolidays) return [];

        const dates = chartData.map(d => d.date);
        const minDate = Math.min(...dates.map(d => new Date(d).getTime()));
        const maxDate = Math.max(...dates.map(d => new Date(d).getTime()));

        return INDIAN_HOLIDAYS.filter(h => {
            const hDate = new Date(h.date).getTime();
            return hDate >= minDate && hDate <= maxDate;
        });
    };

    const visibleHolidays = getVisibleHolidays();

    // Group data by date for recharts
    const chartDataByDate: { [key: string]: any } = {};
    chartData.forEach(item => {
        if (!chartDataByDate[item.date]) {
            chartDataByDate[item.date] = { date: item.date };
        }
        chartDataByDate[item.date][item.category] = item.value;
    });
    const formattedChartData = Object.values(chartDataByDate).sort((a, b) => a.date.localeCompare(b.date));

    return (
        <>
            <div className="card">
                <h3>Sales by Category Trend</h3>

                {/* Controls */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px', marginBottom: '20px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Metric</label>
                        <select
                            value={metric}
                            onChange={(e) => setMetric(e.target.value)}
                            style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                        >
                            <option>Total</option>
                            <option>Average</option>
                            <option>Cumulative</option>
                            <option>Moving Average (7-day)</option>
                        </select>
                    </div>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Time Bucket</label>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <select
                                value={timeBucket}
                                onChange={(e) => setTimeBucket(e.target.value)}
                                disabled={metric === 'Moving Average (7-day)'}
                                style={{
                                    flex: 1,
                                    padding: '8px',
                                    borderRadius: '4px',
                                    border: '1px solid #444',
                                    backgroundColor: metric === 'Moving Average (7-day)' ? '#222' : '#333',
                                    color: metric === 'Moving Average (7-day)' ? '#666' : 'white',
                                    cursor: metric === 'Moving Average (7-day)' ? 'not-allowed' : 'pointer'
                                }}
                            >
                                <option>Day</option>
                                <option>Week</option>
                                <option>Month</option>
                            </select>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '14px', color: '#aaa', whiteSpace: 'nowrap' }}>
                                <input
                                    type="checkbox"
                                    checked={showHolidays}
                                    onChange={(e) => setShowHolidays(e.target.checked)}
                                    style={{ cursor: 'pointer' }}
                                />
                                Show Holidays
                            </label>
                        </div>
                    </div>
                </div>

                {/* Weekday Selector */}
                <div style={{ marginBottom: '20px' }}>
                    <label style={{ display: 'block', marginBottom: '10px', fontSize: '14px', color: '#aaa' }}>Include Days</label>
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        {daysOfWeek.map((day, idx) => (
                            <button
                                key={day}
                                onClick={() => toggleDay(day)}
                                style={{
                                    padding: '6px 12px',
                                    backgroundColor: selectedDays.includes(day) ? '#646cff' : '#333',
                                    color: selectedDays.includes(day) ? 'white' : '#aaa',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    fontSize: '13px',
                                    fontWeight: selectedDays.includes(day) ? 'bold' : 'normal'
                                }}
                            >
                                {daysAbbr[idx]}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={formattedChartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="date" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                            <Legend />

                            {/* Render a line for each category */}
                            {categories.map((category, idx) => (
                                <Line
                                    key={category}
                                    type="monotone"
                                    dataKey={category}
                                    stroke={colors[idx % colors.length]}
                                    name={`${category} (₹)`}
                                    strokeWidth={2}
                                    dot={{ r: 3 }}
                                />
                            ))}

                            {/* Brush for Zoom */}
                            <Brush dataKey="date" height={30} stroke="#646cff" />

                            {/* Holiday Reference Lines */}
                            {visibleHolidays.map((holiday, idx) => (
                                <ReferenceLine
                                    key={idx}
                                    x={holiday.date}
                                    stroke="#F59E0B"
                                    strokeDasharray="4 4"
                                    strokeWidth={2}
                                    label={{
                                        value: holiday.name,
                                        position: 'top',
                                        fill: '#F59E0B',
                                        fontSize: 11,
                                        offset: 10
                                    }}
                                />
                            ))}
                        </LineChart>
                    </ResponsiveContainer>
                </ResizableChart>

                {metric === 'Moving Average (7-day)' && (
                    <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                        * Time bucket is disabled for Moving Average (calculates daily)
                    </p>
                )}
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Sales by Category Trend (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={formattedChartData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="date" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                                <Legend />
                                {categories.map((category, idx) => (
                                    <Line
                                        key={category}
                                        type="monotone"
                                        dataKey={category}
                                        stroke={colors[idx % colors.length]}
                                        name={`${category} (₹)`}
                                        strokeWidth={2}
                                        dot={{ r: 3 }}
                                    />
                                ))}
                                <Brush dataKey="date" height={30} stroke="#646cff" />
                                {visibleHolidays.map((holiday, idx) => (
                                    <ReferenceLine
                                        key={idx}
                                        x={holiday.date}
                                        stroke="#F59E0B"
                                        strokeDasharray="4 4"
                                        strokeWidth={2}
                                        label={{
                                            value: holiday.name,
                                            position: 'top',
                                            fill: '#F59E0B',
                                            fontSize: 11,
                                            offset: 10
                                        }}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

function RevenueVsOrdersChart() {
    const [data, setData] = useState<any[]>([]);
    const [timeBucket, setTimeBucket] = useState('Day');
    const [showHolidays, setShowHolidays] = useState(false);
    const [selectedDays, setSelectedDays] = useState(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
    const [isFullscreen, setIsFullscreen] = useState(false);

    const daysOfWeek = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const daysAbbr = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.salesTrend();
            setData(res.data);
        } catch (e) {
            console.error(e);
        }
    };

    const toggleDay = (day: string) => {
        if (selectedDays.includes(day)) {
            setSelectedDays(selectedDays.filter(d => d !== day));
        } else {
            setSelectedDays([...selectedDays, day]);
        }
    };

    const processData = () => {
        if (!data.length) return [];

        // Filter by selected weekdays
        const filtered = data.filter(row => {
            const date = new Date(row.date);
            const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
            return selectedDays.includes(dayName);
        });

        // Group by time bucket
        return groupByTimeBucket(filtered, timeBucket);
    };

    const groupByTimeBucket = (data: any[], bucket: string) => {
        if (bucket === 'Day') return data;

        const groups: { [key: string]: any[] } = {};
        data.forEach(row => {
            const dateStr = row.date;
            const [year, month, day] = dateStr.split('-').map(Number);
            let key: string;

            if (bucket === 'Week') {
                const date = new Date(year, month - 1, day);
                const dayOfWeek = date.getDay();
                const weekStart = new Date(year, month - 1, day - dayOfWeek);
                const weekYear = weekStart.getFullYear();
                const weekMonth = String(weekStart.getMonth() + 1).padStart(2, '0');
                const weekDay = String(weekStart.getDate()).padStart(2, '0');
                key = `${weekYear}-${weekMonth}-${weekDay}`;
            } else { // Month
                key = `${year}-${String(month).padStart(2, '0')}-01`;
            }

            if (!groups[key]) groups[key] = [];
            groups[key].push(row);
        });

        return Object.keys(groups).sort().map(key => ({
            date: key,
            revenue: groups[key].reduce((sum, r) => sum + (r.revenue || 0), 0),
            num_orders: groups[key].reduce((sum, r) => sum + (r.num_orders || 0), 0)
        }));
    };

    const chartData = processData();

    // Get holidays within the visible date range
    const getVisibleHolidays = () => {
        if (!chartData.length || !showHolidays) return [];

        const dates = chartData.map(d => d.date);
        const minDate = Math.min(...dates.map(d => new Date(d).getTime()));
        const maxDate = Math.max(...dates.map(d => new Date(d).getTime()));

        return INDIAN_HOLIDAYS.filter(h => {
            const hDate = new Date(h.date).getTime();
            return hDate >= minDate && hDate <= maxDate;
        });
    };

    const visibleHolidays = getVisibleHolidays();

    return (
        <>
            <div className="card">
                <h3>Revenue vs Orders</h3>

                {/* Controls */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '15px', marginBottom: '20px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Time Bucket</label>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <select
                                value={timeBucket}
                                onChange={(e) => setTimeBucket(e.target.value)}
                                style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                            >
                                <option>Day</option>
                                <option>Week</option>
                                <option>Month</option>
                            </select>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '14px', color: '#aaa', whiteSpace: 'nowrap' }}>
                                <input
                                    type="checkbox"
                                    checked={showHolidays}
                                    onChange={(e) => setShowHolidays(e.target.checked)}
                                    style={{ cursor: 'pointer' }}
                                />
                                Show Holidays
                            </label>
                        </div>
                    </div>
                </div>

                {/* Weekday Selector */}
                <div style={{ marginBottom: '20px' }}>
                    <label style={{ display: 'block', marginBottom: '10px', fontSize: '14px', color: '#aaa' }}>Include Days</label>
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        {daysOfWeek.map((day, idx) => (
                            <button
                                key={day}
                                onClick={() => toggleDay(day)}
                                style={{
                                    padding: '6px 12px',
                                    backgroundColor: selectedDays.includes(day) ? '#646cff' : '#333',
                                    color: selectedDays.includes(day) ? 'white' : '#aaa',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    fontSize: '13px',
                                    fontWeight: selectedDays.includes(day) ? 'bold' : 'normal'
                                }}
                            >
                                {daysAbbr[idx]}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="date" stroke="#aaa" />
                            <YAxis yAxisId="left" stroke="#3B82F6" />
                            <YAxis yAxisId="right" orientation="right" stroke="#10B981" />
                            <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                            <Legend />
                            <Line yAxisId="left" type="monotone" dataKey="revenue" stroke="#3B82F6" name="Revenue (₹)" strokeWidth={2} dot={{ r: 3 }} />
                            <Line yAxisId="right" type="monotone" dataKey="num_orders" stroke="#10B981" name="Orders" strokeWidth={2} dot={{ r: 3 }} />

                            {/* Brush for Zoom */}
                            <Brush dataKey="date" height={30} stroke="#646cff" />

                            {/* Holiday Reference Lines */}
                            {visibleHolidays.map((holiday, idx) => (
                                <ReferenceLine
                                    key={idx}
                                    x={holiday.date}
                                    stroke="#F59E0B"
                                    strokeDasharray="4 4"
                                    strokeWidth={2}
                                    label={{
                                        value: holiday.name,
                                        position: 'top',
                                        fill: '#F59E0B',
                                        fontSize: 11,
                                        offset: 10
                                    }}
                                />
                            ))}
                        </LineChart>
                    </ResponsiveContainer>
                </ResizableChart>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Revenue vs Orders (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="date" stroke="#aaa" />
                                <YAxis yAxisId="left" stroke="#3B82F6" />
                                <YAxis yAxisId="right" orientation="right" stroke="#10B981" />
                                <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                                <Legend />
                                <Line yAxisId="left" type="monotone" dataKey="revenue" stroke="#3B82F6" name="Revenue (₹)" strokeWidth={2} dot={{ r: 3 }} />
                                <Line yAxisId="right" type="monotone" dataKey="num_orders" stroke="#10B981" name="Orders" strokeWidth={2} dot={{ r: 3 }} />
                                <Brush dataKey="date" height={30} stroke="#646cff" />
                                {visibleHolidays.map((holiday, idx) => (
                                    <ReferenceLine
                                        key={idx}
                                        x={holiday.date}
                                        stroke="#F59E0B"
                                        strokeDasharray="4 4"
                                        strokeWidth={2}
                                        label={{
                                            value: holiday.name,
                                            position: 'top',
                                            fill: '#F59E0B',
                                            fontSize: 11,
                                            offset: 10
                                        }}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

function AverageOrderValueChart() {
    const [data, setData] = useState<any[]>([]);
    const [timeBucket, setTimeBucket] = useState('Day');
    const [showHolidays, setShowHolidays] = useState(false);
    const [selectedDays, setSelectedDays] = useState(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
    const [isFullscreen, setIsFullscreen] = useState(false);

    const daysOfWeek = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const daysAbbr = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.salesTrend();
            setData(res.data);
        } catch (e) {
            console.error(e);
        }
    };

    const toggleDay = (day: string) => {
        if (selectedDays.includes(day)) {
            setSelectedDays(selectedDays.filter(d => d !== day));
        } else {
            setSelectedDays([...selectedDays, day]);
        }
    };

    const processData = () => {
        if (!data.length) return [];

        // Filter by selected weekdays
        const filtered = data.filter(row => {
            const date = new Date(row.date);
            const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
            return selectedDays.includes(dayName);
        });

        // Group by time bucket and calculate AOV
        const grouped = groupByTimeBucket(filtered, timeBucket);

        // Calculate AOV for each bucket
        return grouped.map(item => ({
            ...item,
            aov: item.num_orders > 0 ? item.revenue / item.num_orders : 0
        }));
    };

    const groupByTimeBucket = (data: any[], bucket: string) => {
        if (bucket === 'Day') return data;

        const groups: { [key: string]: any[] } = {};
        data.forEach(row => {
            const dateStr = row.date;  // Keep as string "YYYY-MM-DD"
            const [year, month, day] = dateStr.split('-').map(Number);

            let key: string;

            if (bucket === 'Week') {
                // Match pandas resample('W') behavior: week ENDS on Sunday
                const date = new Date(year, month - 1, day);
                const dayOfWeek = date.getDay();  // 0 = Sunday

                // Calculate the Sunday that ends this week
                const weekEnd = new Date(year, month - 1, day + (7 - dayOfWeek) % 7);
                const weekYear = weekEnd.getFullYear();
                const weekMonth = String(weekEnd.getMonth() + 1).padStart(2, '0');
                const weekDay = String(weekEnd.getDate()).padStart(2, '0');
                key = `${weekYear}-${weekMonth}-${weekDay}`;
            } else { // Month
                key = `${year}-${String(month).padStart(2, '0')}-01`;
            }

            if (!groups[key]) groups[key] = [];
            groups[key].push(row);
        });

        return Object.keys(groups).sort().map(key => ({
            date: key,
            revenue: groups[key].reduce((sum, r) => sum + (r.revenue || 0), 0),
            num_orders: groups[key].reduce((sum, r) => sum + (r.num_orders || 0), 0)
        }));
    };

    const chartData = processData();

    // Get holidays within the visible date range
    const getVisibleHolidays = () => {
        if (!chartData.length || !showHolidays) return [];

        const dates = chartData.map(d => d.date);
        const minDate = Math.min(...dates.map(d => new Date(d).getTime()));
        const maxDate = Math.max(...dates.map(d => new Date(d).getTime()));

        return INDIAN_HOLIDAYS.filter(h => {
            const hDate = new Date(h.date).getTime();
            return hDate >= minDate && hDate <= maxDate;
        });
    };

    const visibleHolidays = getVisibleHolidays();

    return (
        <>
            <div className="card">
                <h3>Average Order Value Trend</h3>

                {/* Controls */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '15px', marginBottom: '20px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Time Bucket</label>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <select
                                value={timeBucket}
                                onChange={(e) => setTimeBucket(e.target.value)}
                                style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                            >
                                <option>Day</option>
                                <option>Week</option>
                                <option>Month</option>
                            </select>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '14px', color: '#aaa', whiteSpace: 'nowrap' }}>
                                <input
                                    type="checkbox"
                                    checked={showHolidays}
                                    onChange={(e) => setShowHolidays(e.target.checked)}
                                    style={{ cursor: 'pointer' }}
                                />
                                Show Holidays
                            </label>
                        </div>
                    </div>
                </div>

                {/* Weekday Selector */}
                <div style={{ marginBottom: '20px' }}>
                    <label style={{ display: 'block', marginBottom: '10px', fontSize: '14px', color: '#aaa' }}>Include Days</label>
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        {daysOfWeek.map((day, idx) => (
                            <button
                                key={day}
                                onClick={() => toggleDay(day)}
                                style={{
                                    padding: '6px 12px',
                                    backgroundColor: selectedDays.includes(day) ? '#646cff' : '#333',
                                    color: selectedDays.includes(day) ? 'white' : '#aaa',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    fontSize: '13px',
                                    fontWeight: selectedDays.includes(day) ? 'bold' : 'normal'
                                }}
                            >
                                {daysAbbr[idx]}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="date" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip
                                contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                formatter={(value: any, _name: string | undefined, props: any) => {
                                    const payload = props.payload;
                                    return [
                                        `AOV: ₹${Math.round(value).toLocaleString()}`,
                                        `Total Revenue: ₹${Math.round(payload.total_revenue || 0).toLocaleString()}`,
                                        `Total Orders: ${Math.round(payload.total_orders || 0)}`
                                    ];
                                }}
                                labelFormatter={(label) => `Period: ${label}`}
                            />
                            <Legend />
                            <Line type="monotone" dataKey="aov" stroke="#8B5CF6" name="AOV (₹)" strokeWidth={2} dot={{ r: 3 }} />

                            {/* Brush for Zoom */}
                            <Brush dataKey="date" height={30} stroke="#646cff" />

                            {/* Holiday Reference Lines */}
                            {visibleHolidays.map((holiday, idx) => (
                                <ReferenceLine
                                    key={idx}
                                    x={holiday.date}
                                    stroke="#F59E0B"
                                    strokeDasharray="4 4"
                                    strokeWidth={2}
                                    label={{
                                        value: holiday.name,
                                        position: 'top',
                                        fill: '#F59E0B',
                                        fontSize: 11,
                                        offset: 10
                                    }}
                                />
                            ))}
                        </LineChart>
                    </ResponsiveContainer>
                </ResizableChart>

                <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                    * Average Order Value = Total Revenue / Total Orders for each period
                </p>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Average Order Value Trend (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="date" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip
                                    contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                    formatter={(value: any, _name: string | undefined, props: any) => {
                                        const payload = props.payload;
                                        return [
                                            `AOV: ₹${Math.round(value).toLocaleString()}`,
                                            `Total Revenue: ₹${Math.round(payload.total_revenue || 0).toLocaleString()}`,
                                            `Total Orders: ${Math.round(payload.total_orders || 0)}`
                                        ];
                                    }}
                                    labelFormatter={(label) => `Period: ${label}`}
                                />
                                <Legend />
                                <Line type="monotone" dataKey="aov" stroke="#8B5CF6" name="AOV (₹)" strokeWidth={2} dot={{ r: 3 }} />
                                <Brush dataKey="date" height={30} stroke="#646cff" />
                                {visibleHolidays.map((holiday, idx) => (
                                    <ReferenceLine
                                        key={idx}
                                        x={holiday.date}
                                        stroke="#F59E0B"
                                        strokeDasharray="4 4"
                                        strokeWidth={2}
                                        label={{
                                            value: holiday.name,
                                            position: 'top',
                                            fill: '#F59E0B',
                                            fontSize: 11,
                                            offset: 10
                                        }}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

function TopItemsChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.topItems();
            const topItems = res.data.items || res.data;  // Handle both old and new format
            const totalSystemRevenue = res.data.total_system_revenue || (Array.isArray(topItems) ? topItems.reduce((sum: number, item: any) => sum + (item.item_revenue || 0), 0) : 0);

            const dataSlice = Array.isArray(topItems) ? topItems.slice(0, 10) : [];

            // Calculate revenue percentage for each item using total system revenue
            const dataWithPct = dataSlice.map((item: any) => ({
                ...item,
                rev_pct: totalSystemRevenue > 0 ? (item.item_revenue / totalSystemRevenue) * 100 : 0,
                pct_label: totalSystemRevenue > 0 ? `${((item.item_revenue / totalSystemRevenue) * 100).toFixed(1)}%` : '0%',
                total_system_revenue: totalSystemRevenue  // Store for caption
            }));

            setData(dataWithPct);
        } catch (e) {
            console.error(e);
        }
    };

    // Get total system revenue from first item (they all have the same value)
    const totalRevenue = data.length > 0 ? data[0].total_system_revenue : 0;

    return (
        <>
            <div className="card">
                <h3>Top 10 Most Sold Items</h3>
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="name" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip
                                contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                formatter={(value: any, name: string | undefined) => {
                                    if (name === 'Quantity Sold') return Math.round(value);
                                    return value;
                                }}
                                labelFormatter={(label) => `Item: ${label}`}
                            />
                            <Bar dataKey="total_sold" fill="#8B5CF6" name="Quantity Sold">
                                <LabelList
                                    dataKey="pct_label"
                                    position="top"
                                    style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                />
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ResizableChart>
                <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                    * Revenue % calculated against total system revenue: ₹{totalRevenue.toLocaleString()}
                </p>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Top 10 Most Sold Items (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="name" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip
                                    contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                    formatter={(value: any, name: string | undefined) => {
                                        if (name === 'Quantity Sold') return Math.round(value);
                                        return value;
                                    }}
                                    labelFormatter={(label) => `Item: ${label}`}
                                />
                                <Bar dataKey="total_sold" fill="#8B5CF6" name="Quantity Sold">
                                    <LabelList
                                        dataKey="pct_label"
                                        position="top"
                                        style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                    />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

function RevenueByCategoryChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.revenueByCategory();
            const categories = res.data.categories || res.data;  // Handle both old and new format
            const totalSystemRevenue = res.data.total_system_revenue || (Array.isArray(categories) ? categories.reduce((sum: number, cat: any) => sum + (cat.revenue || 0), 0) : 0);

            // Calculate revenue percentage for each category
            const dataWithPct = (Array.isArray(categories) ? categories : []).map((cat: any) => ({
                ...cat,
                rev_pct: totalSystemRevenue > 0 ? (cat.revenue / totalSystemRevenue) * 100 : 0,
                pct_label: totalSystemRevenue > 0 ? `${((cat.revenue / totalSystemRevenue) * 100).toFixed(1)}%` : '0%',
                total_system_revenue: totalSystemRevenue  // Store for caption
            }));

            setData(dataWithPct);
        } catch (e) {
            console.error(e);
        }
    };

    // Get total system revenue from first item (they all have the same value)
    const totalRevenue = data.length > 0 ? data[0].total_system_revenue : 0;

    return (
        <>
            <div className="card">
                <h3>Revenue by Category</h3>
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="category" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                            <Bar dataKey="revenue" fill="#F59E0B" name="Revenue (₹)">
                                <LabelList
                                    dataKey="pct_label"
                                    position="top"
                                    style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                />
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ResizableChart>
                <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                    * Revenue % calculated against total system revenue: ₹{totalRevenue.toLocaleString()}
                </p>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Revenue by Category (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="category" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                                <Bar dataKey="revenue" fill="#F59E0B" name="Revenue (₹)">
                                    <LabelList
                                        dataKey="pct_label"
                                        position="top"
                                        style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                    />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

function HourlyRevenueChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.hourlyRevenue();
            const formatted = res.data.map((d: any) => ({
                ...d,
                hour_label: formatHour(d.hour_num)
            }));
            setData(formatted);
        } catch (e) {
            console.error(e);
        }
    };

    const formatHour = (h: number) => {
        if (h === 0) return '12 AM';
        if (h === 12) return '12 PM';
        if (h < 12) return `${h} AM`;
        return `${h - 12} PM`;
    };

    return (
        <>
            <div className="card">
                <h3>Hourly Revenue Analysis (Local Time - IST)</h3>
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="hour" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                            <Legend />
                            <Bar dataKey="revenue" fill="#10B981" name="Revenue (₹)" />
                        </BarChart>
                    </ResponsiveContainer>
                </ResizableChart>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Hourly Revenue Analysis (Local Time - IST) (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="hour" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                                <Legend />
                                <Bar dataKey="revenue" fill="#10B981" name="Revenue (₹)" />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

function OrderSourceChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.orderSource();

            // Add formatted revenue labels
            const dataWithLabels = (res.data || []).map((item: any) => ({
                ...item,
                revenue_label: `₹${Math.round(item.revenue || 0).toLocaleString()}`
            }));

            setData(dataWithLabels);
        } catch (e) {
            console.error(e);
        }
    };

    return (
        <>
            <div className="card">
                <h3>Order Source Analysis</h3>
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="order_from" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                            <Bar dataKey="count" fill="#06B6D4" name="Orders">
                                <LabelList
                                    dataKey="revenue_label"
                                    position="top"
                                    style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                />
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ResizableChart>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Order Source Analysis (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="order_from" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                                <Bar dataKey="count" fill="#06B6D4" name="Orders">
                                    <LabelList
                                        dataKey="revenue_label"
                                        position="top"
                                        style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                    />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}
