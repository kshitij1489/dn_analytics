import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { Card, LoadingSpinner, ResizableTableWrapper } from '../components';
import {
    ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine
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
    // CRITICAL: Use sale_date as both map key AND set it as 'date' field for chart consistency
    data?.historical.forEach(d => {
        const dateKey = d.sale_date;
        updateEntry(dateKey, 'date', dateKey); // Explicitly set the date field
        updateEntry(dateKey, 'historical', d.revenue);
        updateEntry(dateKey, 'temp_max', d.temp_max);
        updateEntry(dateKey, 'rain_category', d.rain_category);
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

    // DEBUG: Log chart data to console
    console.log('=== CHART DATA DEBUG ===');
    console.log('Total data points:', chartData.length);
    console.log('First 3 dates:', chartData.slice(0, 3).map(d => d.date));
    console.log('Last 3 dates:', chartData.slice(-3).map(d => d.date));
    const rainDays = chartData.filter(d => d.rain_category && d.rain_category !== 'none');
    console.log('Rain days count:', rainDays.length);
    console.log('Rain days:', rainDays.map(d => ({ date: d.date, category: d.rain_category })));
    const todayStr = new Date().toISOString().split('T')[0];
    console.log('Today string:', todayStr);
    console.log('Today in data?', chartData.some(d => d.date === todayStr));

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
                        <ComposedChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                            <XAxis
                                dataKey="date"
                                type="category"
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
                                formatter={(val: any, name: string | undefined, props: any) => {
                                    const labelName = name || 'Revenue';
                                    if (labelName === 'Temperature') return [`${parseFloat(val).toFixed(1)}°C`, labelName];
                                    const entry = props.payload;
                                    const rainLabel = entry?.rain_category && entry.rain_category !== 'none'
                                        ? ` (${entry.rain_category.toUpperCase()} RAIN)`
                                        : '';
                                    return [`${formatCurrency(val || 0)}${labelName === 'Historical' ? rainLabel : ''}`, labelName];
                                }}
                            />
                            <Legend />


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


                            {/* Rain Indicators (Vertical Lines) - Light dotted lines */}
                            {chartData.map((entry) => {
                                if (entry.rain_category && entry.rain_category !== 'none') {
                                    const isHeavy = entry.rain_category === 'heavy';
                                    const color = isHeavy ? "#60A5FA" : "#93C5FD"; // Light blue colors
                                    return (
                                        <ReferenceLine
                                            yAxisId="left"
                                            key={`rain-${entry.date}`}
                                            x={entry.date}
                                            stroke={color}
                                            strokeWidth={1}
                                            strokeDasharray="4 4"
                                            label={{
                                                value: isHeavy ? '🌧️' : '💧',
                                                position: 'top',
                                                fill: color,
                                                fontSize: 14
                                            }}
                                        />
                                    );
                                }
                                return null;
                            })}
                        </ComposedChart>
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

