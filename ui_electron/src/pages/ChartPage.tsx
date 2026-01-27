import { useState, useEffect } from 'react';
import {
    SalesTrendChart,
    CategoryTrendChart,
    RevenueVsOrdersChart,
    AverageOrderValueChart,
    TopItemsChart,
    RevenueByCategoryChart,
    HourlyRevenueChart,
    OrderSourceChart,
    AverageRevenueByDayChart
} from '../components/charts';

import { endpoints } from '../api';

export default function ChartPage({ lastDbSync }: { lastDbSync?: number }) {
    const [chartType, setChartType] = useState('salesTrend');
    const [kpis, setKpis] = useState<any>(null);

    useEffect(() => {
        loadKPIs();
    }, [lastDbSync]);

    const loadKPIs = async () => {
        try {
            const res = await endpoints.insights.kpis({ _t: lastDbSync });
            setKpis(res.data);
        } catch (error) {
            console.error(error);
        }
    };

    // Using key on charts to force re-mount on sync
    const chartKey = `chart-${lastDbSync}`;

    // Split charts into two rows
    const row1 = [
        { id: 'salesTrend', label: '📈 Sales Trend' },
        { id: 'revenueVsOrders', label: '🖇️ Rev vs Orders' },
        { id: 'aovTrend', label: '📊 Avg Order Val' },
        { id: 'hourlyRevenue', label: '⏰ Hourly Rev' },
        { id: 'avgRevenueByDay', label: '📅 Avg Rev/Day' }
    ];

    const row2 = [
        { id: 'categoryTrend', label: '📉 Category Trend' },
        { id: 'revenueByCategory', label: '📂  Rev by Cat' },
        { id: 'topItems', label: '🏆 Top Items' },
        { id: 'orderSource', label: '🛵 Order Source' }
    ];

    // Segmented Control Button Component 
    const SegmentedButton = ({ chart }: { chart: any }) => (
        <button
            onClick={() => setChartType(chart.id)}
            style={{
                flex: 1,
                padding: '8px 4px',
                fontSize: '0.85em',
                background: chartType === chart.id ? 'var(--card-bg)' : 'transparent',
                color: chartType === chart.id ? 'var(--text-color)' : 'var(--text-secondary)',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontWeight: chartType === chart.id ? '600' : '500',
                transition: 'all 0.2s',
                boxShadow: chartType === chart.id ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis'
            }}
            title={chart.label}
        >
            {chart.label}
        </button>
    );


    return (
        <div style={{ padding: '20px' }}>
            {/* KPIs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '20px', marginBottom: '30px' }}>
                <KPICard title="Revenue" value={`₹${kpis?.total_revenue?.toLocaleString() || 0}`} />
                <KPICard title="Orders" value={kpis?.total_orders?.toLocaleString() || 0} />
                <KPICard title="Avg Order" value={`₹${kpis?.avg_order_value ? Math.round(kpis.avg_order_value).toLocaleString() : 0}`} />
                <KPICard title="Customers" value={kpis?.total_customers?.toLocaleString() || 0} />
            </div>

            <hr style={{ margin: '30px 0', border: 'none', borderTop: '1px solid #333' }} />

            <div style={{ marginBottom: '20px' }}>
                {/* Row 1 */}
                <div style={{
                    display: 'flex',
                    background: 'var(--input-bg)',
                    padding: '4px',
                    borderRadius: '8px',
                    marginBottom: '10px'
                }}>
                    {row1.map(chart => <SegmentedButton key={chart.id} chart={chart} />)}
                </div>

                {/* Row 2 */}
                <div style={{
                    display: 'flex',
                    background: 'var(--input-bg)',
                    padding: '4px',
                    borderRadius: '8px'
                }}>
                    {row2.map(chart => <SegmentedButton key={chart.id} chart={chart} />)}
                </div>
            </div>

            <div>
                {chartType === 'salesTrend' && <SalesTrendChart key={chartKey} />}
                {chartType === 'categoryTrend' && <CategoryTrendChart key={chartKey} />}
                {chartType === 'revenueVsOrders' && <RevenueVsOrdersChart key={chartKey} />}
                {chartType === 'aovTrend' && <AverageOrderValueChart key={chartKey} />}
                {chartType === 'topItems' && <TopItemsChart key={chartKey} />}
                {chartType === 'revenueByCategory' && <RevenueByCategoryChart key={chartKey} />}
                {chartType === 'hourlyRevenue' && <HourlyRevenueChart key={chartKey} />}
                {chartType === 'orderSource' && <OrderSourceChart key={chartKey} />}
                {chartType === 'avgRevenueByDay' && <AverageRevenueByDayChart key={chartKey} />}
            </div>
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
