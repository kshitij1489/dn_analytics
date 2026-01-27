import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { Card, LoadingSpinner, ResizableTableWrapper } from '../components';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend
} from 'recharts';
import './ForecastPage.css';

interface ForecastDataPoint {
    date: string;
    revenue: number;
    orders: number;
}

interface ForecastResponse {
    summary: {
        generated_at: string;
        projected_7d_revenue: number;
        projected_7d_orders: number;
    };
    historical: { sale_date: string; revenue: number; orders: number }[];
    forecasts: {
        weekday_avg: ForecastDataPoint[];
        holt_winters: ForecastDataPoint[];
        prophet: ForecastDataPoint[];
    };
}

export default function ForecastPage({ lastDbSync }: { lastDbSync?: number }) {
    const [data, setData] = useState<ForecastResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        loadData();
    }, [lastDbSync]);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await endpoints.forecast.get();
            setData(res.data);
        } catch (e: any) {
            console.error('Failed to fetch forecast:', e);
            setError(e.message || 'Failed to generate forecast');
        } finally {
            setLoading(false);
        }
    };

    const formatCurrency = (val: number) => `₹${Math.round(val).toLocaleString()}`;
    const formatDate = (isoStr: string) => {
        const d = new Date(isoStr);
        return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    };

    if (loading) return (
        <div className="forecast-loading">
            <LoadingSpinner />
            <p>Generating 7-day sales forecast (Prophet + Holt-Winters)...</p>
        </div>
    );

    if (error) return (
        <div className="forecast-error">
            <p>❌ {error}</p>
            <button onClick={loadData} className="forecast-retry-btn">Retry</button>
        </div>
    );

    // Combine all data for unified chart
    const chartData = [
        // Historical data
        ...(data?.historical.map(d => ({
            date: d.sale_date,
            historical: d.revenue,
            weekday_avg: null as number | null,
            holt_winters: null as number | null,
            prophet: null as number | null,
        })) || []),
        // Forecast data (merge all 3 algorithms by date)
        ...(data?.forecasts.weekday_avg.map((wa, idx) => ({
            date: wa.date,
            historical: null as number | null,
            weekday_avg: wa.revenue,
            holt_winters: data?.forecasts.holt_winters[idx]?.revenue || 0,
            prophet: data?.forecasts.prophet[idx]?.revenue || 0,
        })) || [])
    ];

    return (
        <div className="forecast-page">
            <div className="forecast-header">
                <h1>📈 Sales Forecast</h1>
                <span className="forecast-date">Generated on {data?.summary.generated_at}</span>
            </div>

            <div className="forecast-kpis">
                <div className="forecast-kpi-card forecast-kpi-revenue">
                    <span className="kpi-label">Projected 7-Day Revenue (Weekday Avg)</span>
                    <span className="kpi-value">{formatCurrency(data?.summary.projected_7d_revenue || 0)}</span>
                </div>
                <div className="forecast-kpi-card">
                    <span className="kpi-label">Projected 7-Day Orders</span>
                    <span className="kpi-value">{data?.summary.projected_7d_orders || 0}</span>
                </div>
            </div>

            <Card title="Revenue Trend - Multi-Algorithm Comparison">
                <div className="forecast-chart-container">
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData} margin={{ top: 10, right: 30, left: 20, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                            <XAxis
                                dataKey="date"
                                tickFormatter={formatDate}
                                tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                            />
                            <YAxis
                                tickFormatter={(val) => `₹${val / 1000}k`}
                                tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                                axisLine={false}
                            />
                            <Tooltip
                                contentStyle={{ backgroundColor: 'var(--card-bg)', borderColor: 'var(--border-color)', color: 'var(--text-color)' }}
                                labelFormatter={formatDate}
                                formatter={(val: number | undefined, name: string | undefined) => [formatCurrency(val || 0), name || 'Revenue']}
                            />
                            <Legend />
                            {/* Historical Line */}
                            <Line
                                type="monotone"
                                dataKey="historical"
                                name="Historical"
                                stroke="var(--accent-color)"
                                strokeWidth={3}
                                dot={false}
                                connectNulls={false}
                            />
                            {/* Weekday Average Forecast */}
                            <Line
                                type="monotone"
                                dataKey="weekday_avg"
                                name="Weekday Avg"
                                stroke="#10B981"
                                strokeWidth={2}
                                strokeDasharray="5 5"
                                dot={{ r: 3, fill: '#10B981' }}
                                connectNulls={false}
                            />
                            {/* Holt-Winters Forecast */}
                            <Line
                                type="monotone"
                                dataKey="holt_winters"
                                name="Holt-Winters"
                                stroke="#F59E0B"
                                strokeWidth={2}
                                strokeDasharray="3 3"
                                dot={{ r: 3, fill: '#F59E0B' }}
                                connectNulls={false}
                            />
                            {/* Prophet Forecast */}
                            <Line
                                type="monotone"
                                dataKey="prophet"
                                name="Prophet"
                                stroke="#8B5CF6"
                                strokeWidth={2}
                                strokeDasharray="8 4"
                                dot={{ r: 3, fill: '#8B5CF6' }}
                                connectNulls={false}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            </Card>

            <Card title="🗓️ 7-Day Forecast Comparison">
                <ResizableTableWrapper>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Weekday</th>
                                <th className="text-right">Weekday Avg</th>
                                <th className="text-right">Holt-Winters</th>
                                <th className="text-right">Prophet</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data?.forecasts.weekday_avg.map((wa, idx) => (
                                <tr key={idx}>
                                    <td>{formatDate(wa.date)}</td>
                                    <td>{new Date(wa.date).toLocaleDateString('en-IN', { weekday: 'long' })}</td>
                                    <td className="text-right">{formatCurrency(wa.revenue)}</td>
                                    <td className="text-right">{formatCurrency(data?.forecasts.holt_winters[idx]?.revenue || 0)}</td>
                                    <td className="text-right">{formatCurrency(data?.forecasts.prophet[idx]?.revenue || 0)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            </Card>
        </div>
    );
}
