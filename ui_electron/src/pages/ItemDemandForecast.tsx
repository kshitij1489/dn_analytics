import { useState, useMemo, useEffect } from 'react';
import { endpoints } from '../api';
import { Card, LoadingSpinner, ResizableTableWrapper, Select } from '../components';
import {
    ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, Area
} from 'recharts';

interface ItemInfo {
    item_id: string;
    item_name: string;
}
interface ItemHistoryPoint {
    date: string;
    item_id: string;
    qty: number;
}
interface ItemForecastPoint {
    date: string;
    item_id: string;
    item_name: string;
    p50: number;
    p90: number;
    probability: number;
}
interface ItemForecastResponse {
    items: ItemInfo[];
    history: ItemHistoryPoint[];
    forecast: ItemForecastPoint[];
}

const formatDate = (isoStr: string) => {
    const d = new Date(isoStr);
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
};

export default function ItemDemandForecast() {
    const [data, setData] = useState<ItemForecastResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [selectedItemId, setSelectedItemId] = useState<string>('');

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await endpoints.forecast.items({ days: 14 });
            setData(res.data);
            if (!selectedItemId && res.data.items?.length > 0) {
                setSelectedItemId(res.data.items[0].item_id);
            }
        } catch (e: any) {
            console.error('Failed to fetch item forecast:', e);
            const detail = e.response?.data?.detail || e.message || 'Failed to load item forecast';
            setError(detail);
        } finally {
            setLoading(false);
        }
    };

    const chartData = useMemo(() => {
        if (!data || !selectedItemId) return [];

        const dataMap = new Map<string, any>();

        data.history
            .filter(h => h.item_id === selectedItemId)
            .forEach(h => {
                dataMap.set(h.date, { date: h.date, historical: h.qty });
            });

        data.forecast
            .filter(f => f.item_id === selectedItemId)
            .forEach(f => {
                const entry = dataMap.get(f.date) || { date: f.date };
                entry.p50 = f.p50;
                entry.p90 = f.p90;
                entry.probability = f.probability;
                dataMap.set(f.date, entry);
            });

        return Array.from(dataMap.values()).sort(
            (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
        );
    }, [data, selectedItemId]);

    const itemOptions = useMemo(() => {
        if (!data?.items) return [];
        return data.items.map(i => ({
            value: i.item_id,
            label: i.item_name,
        }));
    }, [data]);

    if (loading) return (
        <div className="forecast-loading item-demand-loading">
            <LoadingSpinner />
            <p>Loading item demand forecast...</p>
        </div>
    );

    if (error) return (
        <div className="forecast-placeholder">
            <h2>Item Demand Forecast</h2>
            <p className="item-demand-error-text">{error}</p>
            <button onClick={loadData} className="forecast-retry-btn item-demand-retry">Retry</button>
        </div>
    );

    if (!data) return null;

    return (
        <div className="item-forecast-section">
            <Card
                title="Item Demand Forecast"
                headerAction={
                    <div className="item-forecast-controls">
                        <Select
                            value={selectedItemId}
                            onChange={setSelectedItemId}
                            options={itemOptions}
                            placeholder="Select item..."
                        />
                    </div>
                }
            >
                <div className="forecast-chart-container">
                    {chartData.length === 0 ? (
                        <div className="empty-state-message">
                            Select an item to view its demand forecast.
                        </div>
                    ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                                <XAxis
                                    dataKey="date"
                                    type="category"
                                    tickFormatter={formatDate}
                                    tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                                />
                                <YAxis
                                    tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                                    axisLine={false}
                                    label={{ value: 'Quantity', angle: -90, position: 'insideLeft', fill: 'var(--text-secondary)' }}
                                />
                                <Tooltip
                                    contentStyle={{ backgroundColor: 'var(--card-bg)', borderColor: 'var(--border-color)', color: 'var(--text-color)' }}
                                    labelFormatter={formatDate}
                                    formatter={(val: any, name: string | undefined) => {
                                        if (name === 'P(Sale)') return [`${(parseFloat(val) * 100).toFixed(0)}%`, name];
                                        return [typeof val === 'number' ? val.toFixed(1) : val, name || ''];
                                    }}
                                />
                                <Legend />

                                {/* P90 confidence band */}
                                <Area
                                    type="monotone"
                                    dataKey="p90"
                                    stroke="none"
                                    fill="#F59E0B"
                                    fillOpacity={0.15}
                                    name="P90 Upper"
                                    legendType="none"
                                    tooltipType="none"
                                    connectNulls={true}
                                />
                                <Area
                                    type="monotone"
                                    dataKey="p50"
                                    stroke="none"
                                    fill="var(--card-bg)"
                                    fillOpacity={1}
                                    name="P50 Lower"
                                    legendType="none"
                                    tooltipType="none"
                                    connectNulls={true}
                                />

                                {/* Historical Sales */}
                                <Line
                                    type="monotone"
                                    dataKey="historical"
                                    name="Historical Sales"
                                    stroke="var(--accent-color)"
                                    strokeWidth={3}
                                    dot={false}
                                    connectNulls={true}
                                />

                                {/* Predicted P50 (dashed) */}
                                <Line
                                    type="monotone"
                                    dataKey="p50"
                                    name="Predicted (p50)"
                                    stroke="#10B981"
                                    strokeWidth={2}
                                    strokeDasharray="5 5"
                                    dot={false}
                                    connectNulls={true}
                                />

                                {/* Predicted P90 (dotted) */}
                                <Line
                                    type="monotone"
                                    dataKey="p90"
                                    name="Predicted (p90)"
                                    stroke="#F59E0B"
                                    strokeWidth={2}
                                    strokeDasharray="2 2"
                                    dot={false}
                                    connectNulls={true}
                                />
                            </ComposedChart>
                        </ResponsiveContainer>
                    )}
                </div>
            </Card>

            {/* Demand Table */}
            <Card title="14-Day Item Demand Forecast">
                <ResizableTableWrapper>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Item</th>
                                <th className="text-right">P(Sale)</th>
                                <th className="text-right">P50 Qty</th>
                                <th className="text-right">P90 Qty</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.forecast
                                .filter(f => !selectedItemId || f.item_id === selectedItemId)
                                .map((f, idx) => (
                                    <tr key={`${f.date}-${f.item_id}-${idx}`}>
                                        <td>{formatDate(f.date)}</td>
                                        <td>{f.item_name}</td>
                                        <td className="text-right">{(f.probability * 100).toFixed(0)}%</td>
                                        <td className="text-right">{f.p50.toFixed(1)}</td>
                                        <td className="text-right item-demand-p90">{f.p90.toFixed(1)}</td>
                                    </tr>
                                ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            </Card>
        </div>
    );
}
