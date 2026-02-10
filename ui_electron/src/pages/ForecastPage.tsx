import { useEffect, useState } from 'react';
import { endpoints } from '../api';
import { Card, LoadingSpinner, ResizableTableWrapper, TabButton } from '../components';
import {
    ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine, Area
} from 'recharts';
import ItemDemandForecast from './ItemDemandForecast';
import './ForecastPage.css';

interface ForecastDataPoint {
    date: string;
    revenue: number;
    orders: number;
    temp_max?: number;
    rain_category?: 'heavy' | 'drizzle' | 'none';
    gp_lower?: number;
    gp_upper?: number;
}

interface ReplayDataPoint {
    date: string;
    pred_mean: number;
    pred_std?: number;
    lower_95: number;
    upper_95: number;
    actual_revenue: number | null;
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
        gp: ForecastDataPoint[];
    };
}

export default function ForecastPage({ lastDbSync }: { lastDbSync?: number }) {
    const [data, setData] = useState<ForecastResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [activeTab, setActiveTab] = useState<'forecast' | 'menu'>('forecast');
    const [activeModels, setActiveModels] = useState<string[]>(['weekday_avg', 'holt_winters', 'prophet']);

    // Replay Mode State
    const [replayDate, setReplayDate] = useState<string>('');
    const [replayData, setReplayData] = useState<ReplayDataPoint[] | null>(null);
    const [replayLoading, setReplayLoading] = useState(false);
    const [replayError, setReplayError] = useState<string | null>(null);

    const toggleModel = (model: string) => {
        setActiveModels(prev =>
            prev.includes(model) ? prev.filter(m => m !== model) : [...prev, model]
        );
    };

    useEffect(() => {
        loadData();
    }, [lastDbSync]);

    // Effect to fetch replay data when date changes
    useEffect(() => {
        if (replayDate) {
            loadReplayData(replayDate);
        }
    }, [replayDate]);

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

    const loadReplayData = async (date: string) => {
        setReplayLoading(true);
        setReplayError(null);
        try {
            const res = await endpoints.forecast.replay(date);
            setReplayData(res.data.data);
        } catch (e: any) {
            console.error(e);
            setReplayError(e.message || 'Failed to load forecast replay');
            setReplayData([]);
        } finally {
            setReplayLoading(false);
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

    // 5. GP (Pink Line) with confidence band
    data?.forecasts.gp?.forEach(d => {
        updateEntry(d.date, 'gp', d.revenue);
        updateEntry(d.date, 'gp_lower', d.gp_lower);
        updateEntry(d.date, 'gp_upper', d.gp_upper);
    });

    // Sort by date
    const chartData = Array.from(chartDataMap.values()).sort((a, b) =>
        new Date(a.date).getTime() - new Date(b.date).getTime()
    );

    return (
        <div className="forecast-page">
            <div className="forecast-header-container">
                <div className="forecast-segmented-control">
                    <TabButton
                        active={activeTab === 'forecast'}
                        onClick={() => setActiveTab('forecast')}
                        variant="segmented"
                    >
                        📈 Sales Forecast
                    </TabButton>
                    <TabButton
                        active={activeTab === 'menu'}
                        onClick={() => setActiveTab('menu')}
                        variant="segmented"
                    >
                        Menu Items
                    </TabButton>
                </div>
                {activeTab === 'forecast' && (
                    <span className="forecast-date">Generated on {data?.summary.generated_at}</span>
                )}
            </div>

            {activeTab === 'menu' ? (
                <ItemDemandForecast />
            ) : (
                <>

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

                    <Card
                        title="Revenue"
                        headerAction={
                            <div className="model-toggle-group">
                                <TabButton
                                    active={activeModels.includes('weekday_avg')}
                                    onClick={() => toggleModel('weekday_avg')}
                                    variant="segmented"
                                >
                                    Weekday Avg
                                </TabButton>
                                <TabButton
                                    active={activeModels.includes('holt_winters')}
                                    onClick={() => toggleModel('holt_winters')}
                                    variant="segmented"
                                >
                                    Holt-Winters
                                </TabButton>
                                <TabButton
                                    active={activeModels.includes('prophet')}
                                    onClick={() => toggleModel('prophet')}
                                    variant="segmented"
                                >
                                    Prophet
                                </TabButton>
                                <TabButton
                                    active={activeModels.includes('gp')}
                                    onClick={() => toggleModel('gp')}
                                    variant="segmented"
                                >
                                    GP
                                </TabButton>
                            </div>
                        }
                    >
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

                                            // Map display name to internal key for active check
                                            const nameMap: Record<string, string> = {
                                                'Weekday Avg': 'weekday_avg',
                                                'Holt-Winters': 'holt_winters',
                                                'Prophet': 'prophet',
                                                'GP': 'gp'
                                            };

                                            if (labelName !== 'Historical' && nameMap[labelName] && !activeModels.includes(nameMap[labelName])) {
                                                return [null, null]; // Recharts handles nulls by hiding them in tooltip
                                            }

                                            const entry = props.payload;

                                            // Add min/max range for GP to show uncertainty band
                                            let rangeInfo = '';
                                            if (labelName === 'GP' && entry.gp_lower != null && entry.gp_upper != null) {
                                                rangeInfo = ` (${formatCurrency(entry.gp_lower)} - ${formatCurrency(entry.gp_upper)})`;
                                            }

                                            const rainLabel = entry?.rain_category && entry.rain_category !== 'none'
                                                ? ` (${entry.rain_category.toUpperCase()} RAIN)`
                                                : '';
                                            return [`${formatCurrency(val || 0)}${rangeInfo}${labelName === 'Historical' ? rainLabel : ''}`, labelName];
                                        }}
                                        // Filter out hidden models from the tooltip summary entirely
                                        itemSorter={(item) => (item.value ? -1 : 1)}
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
                                    {activeModels.includes('weekday_avg') && (
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
                                    )}
                                    {/* Holt-Winters Forecast */}
                                    {activeModels.includes('holt_winters') && (
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
                                    )}
                                    {/* Prophet Forecast */}
                                    {activeModels.includes('prophet') && (
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
                                    )}
                                    {/* GP Confidence Band (stacked areas for cutout effect) */}
                                    {activeModels.includes('gp') && (
                                        <>
                                            <Area
                                                yAxisId="left"
                                                type="monotone"
                                                dataKey="gp_upper"
                                                stroke="none"
                                                fill="#EC4899"
                                                fillOpacity={0.15}
                                                name="GP Upper"
                                                legendType="none"
                                                tooltipType="none"
                                                connectNulls={true}
                                            />
                                            <Area
                                                yAxisId="left"
                                                type="monotone"
                                                dataKey="gp_lower"
                                                stroke="none"
                                                fill="var(--card-bg)"
                                                fillOpacity={1}
                                                name="GP Lower"
                                                legendType="none"
                                                tooltipType="none"
                                                connectNulls={true}
                                            />
                                        </>
                                    )}
                                    {/* GP Forecast Line */}
                                    {activeModels.includes('gp') && (
                                        <Line
                                            yAxisId="left"
                                            type="monotone"
                                            dataKey="gp"
                                            name="GP"
                                            stroke="#EC4899"
                                            strokeWidth={2}
                                            strokeDasharray="2 2"
                                            dot={false}
                                            connectNulls={true}
                                        />
                                    )}

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
                                        <th className="text-right">GP</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data?.forecasts.weekday_avg
                                        .filter(wa => new Date(wa.date) >= new Date(new Date().setHours(0, 0, 0, 0)))
                                        .map((wa) => {
                                            const p = data?.forecasts.prophet.find(x => x.date === wa.date);
                                            const hw = data?.forecasts.holt_winters.find(x => x.date === wa.date);
                                            const gpForecast = data?.forecasts.gp?.find(x => x.date === wa.date);

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
                                                    <td className="text-right">{gpForecast ? formatCurrency(gpForecast.revenue) : '—'}</td>
                                                </tr>
                                            );
                                        })}
                                </tbody>
                            </table>
                        </ResizableTableWrapper>
                    </Card>

                    {/* Forecast Replay (Audit Mode) */}
                    <Card
                        title={replayDate ? `Forecast Audit: ${formatDate(replayDate)}` : 'Forecast Audit (Replay Mode)'}
                        headerAction={
                            <div className="audit-controls">
                                <label>Model Run Date:</label>
                                <input
                                    type="date"
                                    className="audit-date-input"
                                    value={replayDate}
                                    onChange={(e) => setReplayDate(e.target.value)}
                                />
                            </div>
                        }
                    >
                        <div className="forecast-chart-container forecast-chart-container--replay">
                            {replayLoading && <div className="loading-overlay"><LoadingSpinner /></div>}

                            {!replayDate && (
                                <div className="empty-state-message">
                                    Select a past date to replay the forecast generated on that day.
                                </div>
                            )}

                            {replayDate && replayError && (
                                <div className="replay-error-message">
                                    ⚠️ {replayError}
                                </div>
                            )}

                            {replayDate && !replayError && replayData && replayData.length === 0 && (
                                <div className="empty-state-message">
                                    No forecast snapshot found for {formatDate(replayDate)}.
                                </div>
                            )}

                            {replayDate && !replayError && replayData && replayData.length > 0 && (
                                <ResponsiveContainer width="100%" height="100%">
                                    <ComposedChart data={replayData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
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
                                            formatter={(val: number | undefined, name?: string) => {
                                                if (val === undefined || val === null) return ['—', name || ''];
                                                return [formatCurrency(val), name || ''];
                                            }}
                                        />
                                        <Legend />

                                        {/* Confidence Interval Band (two stacked areas) */}
                                        <Area
                                            type="monotone"
                                            dataKey="upper_95"
                                            stroke="none"
                                            fill="#93C5FD"
                                            fillOpacity={0.3}
                                            name="Upper 95%"
                                            legendType="none"
                                            tooltipType="none"
                                        />
                                        <Area
                                            type="monotone"
                                            dataKey="lower_95"
                                            stroke="none"
                                            fill="var(--card-bg)"
                                            fillOpacity={1}
                                            name="Lower 95%"
                                            legendType="none"
                                            tooltipType="none"
                                        />

                                        {/* Forecast Mean (Blue) */}
                                        <Line
                                            type="monotone"
                                            dataKey="pred_mean"
                                            name={`Forecast (${formatDate(replayDate)})`}
                                            stroke="#3B82F6"
                                            strokeWidth={3}
                                            dot={true}
                                        />

                                        {/* Actual Revenue (Black) */}
                                        <Line
                                            type="monotone"
                                            dataKey="actual_revenue"
                                            name="Actual Revenue"
                                            stroke="#000000"
                                            strokeWidth={2}
                                            dot={true}
                                            connectNulls={true}
                                        />
                                    </ComposedChart>
                                </ResponsiveContainer>
                            )}
                        </div>
                    </Card>
                </>
            )}
        </div>
    );
}

