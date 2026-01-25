import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { Card, LoadingSpinner, ResizableTableWrapper } from '../components';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine
} from 'recharts';
import './ForecastPage.css';

interface ForecastDataPoint {
    date: string;
    revenue: number;
    orders: number;
    temp_max?: number;
    rain_category?: 'heavy' | 'drizzle' | 'none';
}

interface ForecastResponse {
    summary: {
        generated_at: string;
        projected_7d_revenue: number;
        projected_7d_orders: number;
    };
    historical: { sale_date: string; revenue: number; orders: number; temp_max: number; rain_category: string }[];
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
    // We need to merge multiple arrays (historical, weekday_avg, holt_winters, prophet) by DATE
    // because forecasts now include historical fitted values.

    const chartDataMap = new Map<string, any>();

    // Helper to init or update map entry
    const updateEntry = (date: string, key: string, val: any) => {
        if (!chartDataMap.has(date)) {
            chartDataMap.set(date, { date });
        }
        const entry = chartDataMap.get(date);
        entry[key] = val;
    };

    // 1. Historical Actuals (Blue Line)
    data?.historical.forEach(d => {
        updateEntry(d.sale_date, 'historical', d.revenue);
        updateEntry(d.sale_date, 'temp_max', d.temp_max);
        updateEntry(d.sale_date, 'rain_category', d.rain_category);
    });

    // 2. Weekday Avg (Green Line)
    data?.forecasts.weekday_avg.forEach(d => {
        updateEntry(d.date, 'weekday_avg', d.revenue);
    });

    // 3. Holt-Winters (Orange Line)
    data?.forecasts.holt_winters.forEach(d => {
        updateEntry(d.date, 'holt_winters', d.revenue);
    });

    // 4. Prophet (Purple Line)
    data?.forecasts.prophet.forEach(d => {
        updateEntry(d.date, 'prophet', d.revenue);
        // Only override weather if not present (historical takes precedence for truth, but prophet has future weather)
        const entry = chartDataMap.get(d.date);
        if (entry.temp_max === undefined) entry.temp_max = d.temp_max;
        if (entry.rain_category === undefined) entry.rain_category = d.rain_category;
    });

    // Sort by date
    const chartData = Array.from(chartDataMap.values()).sort((a, b) =>
        new Date(a.date).getTime() - new Date(b.date).getTime()
    );

    return (
        <div className="forecast-page">
            <div className="forecast-header">
                <h1>📈 Sales Forecast</h1>
                <span className="forecast-date">Generated on {data?.summary.generated_at}</span>
            </div>

            <div className="forecast-kpis">
                <div className="forecast-kpi-card forecast-kpi-revenue">
                    <span className="kpi-label">Projected 7-Day Revenue (Prophet)</span>
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
                            {/* Left Y Axis - Revenue */}
                            <YAxis
                                yAxisId="left"
                                tickFormatter={(val) => `₹${val / 1000}k`}
                                tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                                axisLine={false}
                            />
                            {/* Right Y Axis - Temperature */}
                            <YAxis
                                yAxisId="right"
                                orientation="right"
                                domain={[0, 50]}
                                unit="°C"
                                tick={{ fill: '#EF4444', fontSize: 12 }}
                                axisLine={{ stroke: '#EF4444' }}
                            />

                            <Tooltip
                                contentStyle={{ backgroundColor: 'var(--card-bg)', borderColor: 'var(--border-color)', color: 'var(--text-color)' }}
                                labelFormatter={formatDate}
                                formatter={(val: number | undefined, name: string | undefined) => {
                                    if (name === 'Temperature') return [`${val}°C`, name];
                                    return [formatCurrency(val || 0), name || 'Revenue'];
                                }}
                            />
                            <Legend />

                            {/* Rain Indicators (Vertical Lines) */}
                            {chartData.map((entry, index) => {
                                if (entry.rain_category === 'heavy') {
                                    return <ReferenceLine key={`heavy-${index}`} x={entry.date} stroke="#3B82F6" strokeWidth={2} label={{ value: '🌧️', position: 'insideTop' }} />;
                                }
                                if (entry.rain_category === 'drizzle') {
                                    return <ReferenceLine key={`drizzle-${index}`} x={entry.date} stroke="#93C5FD" strokeDasharray="3 3" label={{ value: '💧', position: 'insideTop' }} />;
                                }
                                return null;
                            })}

                            {/* Historical Line */}
                            <Line
                                yAxisId="left"
                                type="monotone"
                                dataKey="historical"
                                name="Historical"
                                stroke="var(--accent-color)"
                                strokeWidth={3}
                                dot={false}
                                connectNulls={true}
                            />
                            {/* Weekday Average Forecast */}
                            <Line
                                yAxisId="left"
                                type="monotone"
                                dataKey="weekday_avg"
                                name="Weekday Avg"
                                stroke="#10B981"
                                strokeWidth={2}
                                strokeDasharray="5 5"
                                dot={false}
                                connectNulls={true}
                            />
                            {/* Holt-Winters Forecast */}
                            <Line
                                yAxisId="left"
                                type="monotone"
                                dataKey="holt_winters"
                                name="Holt-Winters"
                                stroke="#F59E0B"
                                strokeWidth={2}
                                strokeDasharray="3 3"
                                dot={false}
                                connectNulls={true}
                            />
                            {/* Prophet Forecast */}
                            <Line
                                yAxisId="left"
                                type="monotone"
                                dataKey="prophet"
                                name="Prophet"
                                stroke="#8B5CF6"
                                strokeWidth={2}
                                strokeDasharray="8 4"
                                dot={false}
                                connectNulls={true}
                            />

                            {/* Temperature Line */}
                            <Line
                                yAxisId="right"
                                type="monotone"
                                dataKey="temp_max"
                                name="Temperature"
                                stroke="#EF4444"
                                strokeWidth={1}
                                dot={false}
                                connectNulls={true}
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
                                <th>Weather</th>
                                <th className="text-right">Weekday Avg</th>
                                <th className="text-right">Holt-Winters</th>
                                <th className="text-right">Prophet</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data?.forecasts.weekday_avg
                                .filter(wa => new Date(wa.date) >= new Date(new Date().setHours(0, 0, 0, 0)))
                                .map((wa) => {
                                    // Find matching index in original full array or lookup by date
                                    // Since filtered, simple idx won't work. We need to find by date.
                                    const p = data?.forecasts.prophet.find(x => x.date === wa.date);
                                    const hw = data?.forecasts.holt_winters.find(x => x.date === wa.date);

                                    return (
                                        <tr key={wa.date}>
                                            <td>{formatDate(wa.date)}</td>
                                            <td>{new Date(wa.date).toLocaleDateString('en-IN', { weekday: 'long' })}</td>
                                            <td>
                                                {p?.temp_max?.toFixed(1)}°C
                                                {p?.rain_category === 'heavy' ? ' 🌧️' : p?.rain_category === 'drizzle' ? ' 💧' : ''}
                                            </td>
                                            <td className="text-right">{formatCurrency(wa.revenue)}</td>
                                            <td className="text-right">{formatCurrency(hw?.revenue || 0)}</td>
                                            <td className="text-right">{formatCurrency(p?.revenue || 0)}</td>
                                        </tr>
                                    );
                                })}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            </Card>
        </div>
    );
}

