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
interface ItemBacktestPoint {
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
    backtest?: ItemBacktestPoint[];
    awaiting_action?: boolean;
    message?: string;
    cloud_not_configured?: boolean;
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
    const [cumulativeSearch, setCumulativeSearch] = useState('');

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

        // Backtest predictions (overlaid on historical period)
        data.backtest
            ?.filter(b => b.item_id === selectedItemId)
            .forEach(b => {
                const entry = dataMap.get(b.date) || { date: b.date };
                entry.backtest_p50 = b.p50;
                entry.backtest_p90 = b.p90;
                dataMap.set(b.date, entry);
            });

        // Future forecast predictions
        data.forecast
            .filter(f => f.item_id === selectedItemId)
            .forEach(f => {
                const entry = dataMap.get(f.date) || { date: f.date };
                entry.p50 = f.p50;
                entry.p90 = f.p90;
                entry.probability = f.probability;
                dataMap.set(f.date, entry);
            });

        // Unified p50/p90: backtest where available, else forecast.
        // Ensures a single continuous line and band across the T-1 → today boundary.
        dataMap.forEach((entry) => {
            entry.unified_p50 = entry.backtest_p50 ?? entry.p50;
            entry.unified_p90 = entry.backtest_p90 ?? entry.p90;
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

    const cumulativeDemandRows = useMemo(() => {
        if (!data?.forecast?.length) return [];
        const horizons = [1, 2, 3, 5, 7, 10, 14] as const;
        const byItem = new Map<string, ItemForecastPoint[]>();
        data.forecast.forEach((f) => {
            const list = byItem.get(f.item_id) || [];
            list.push(f);
            byItem.set(f.item_id, list);
        });
        return Array.from(byItem.entries())
            .map(([item_id, forecasts]) => {
            const sorted = [...forecasts].sort(
                (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
            );
            const item_name = sorted[0]?.item_name ?? '';
            const row: Record<string, string | number> = { item_id, item_name };
            horizons.forEach((n) => {
                const sum = sorted
                    .slice(0, n)
                    .reduce((acc, f) => acc + f.p50, 0);
                row[`d${n}`] = sum;
            });
            return row;
        })
            .sort((a, b) => String(a.item_name).localeCompare(String(b.item_name)));
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
            {data.awaiting_action && (
                <div style={{
                    padding: '12px 16px',
                    marginBottom: '16px',
                    background: 'rgba(245, 158, 11, 0.15)',
                    border: '1px solid rgba(245, 158, 11, 0.4)',
                    borderRadius: '8px',
                    color: 'var(--text-color)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    flexWrap: 'wrap',
                }}>
                    <span>{data.message || 'Item demand forecast cache is empty. Use Pull from Cloud or Full Retrain to populate.'}</span>
                    <button
                        onClick={async () => {
                            try {
                                const res = await endpoints.forecast.pullFromCloud('items');
                                const msg = res.data as { item_inserted?: number };
                                alert(`Done. Items: ${msg.item_inserted ?? 0}`);
                                loadData();
                            } catch (e: any) {
                                alert(e.response?.data?.detail || "Pull failed");
                            }
                        }}
                        style={{ padding: '8px 14px', background: 'rgba(34, 197, 94, 0.2)', color: '#22c55e', border: '1px solid rgba(34, 197, 94, 0.4)', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
                    >
                        Pull from Cloud
                    </button>
                    <button
                        onClick={async () => {
                            try {
                                await endpoints.forecast.fullRetrain('items');
                                alert('Item demand retrain started. Refresh in ~30–60 seconds.');
                            } catch (e: any) {
                                alert(e.response?.data?.detail || "Retrain failed");
                            }
                        }}
                        style={{ padding: '8px 14px', background: 'rgba(59, 130, 246, 0.2)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.4)', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
                    >
                        Full Retrain
                    </button>
                </div>
            )}
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

                                {/* P50–P90 confidence band (backtest + forecast, unified) */}
                                <Area
                                    type="monotone"
                                    dataKey="unified_p90"
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
                                    dataKey="unified_p50"
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

                                {/* Unified P50: backtest for historical dates, forecast for future (smooth connection at T-1 → today) */}
                                <Line
                                    type="monotone"
                                    dataKey="unified_p50"
                                    name="Predicted (p50)"
                                    stroke="#10B981"
                                    strokeWidth={2}
                                    strokeDasharray="5 5"
                                    dot={false}
                                    connectNulls={true}
                                />

                                {/* Unified P90: same logic, continuous across boundary */}
                                <Line
                                    type="monotone"
                                    dataKey="unified_p90"
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

            {/* Cumulative Demand */}
            <Card
                title="Cumulative Demand"
                headerAction={
                    <input
                        type="text"
                        placeholder="search by name"
                        value={cumulativeSearch}
                        onChange={(e) => setCumulativeSearch(e.target.value)}
                        style={{
                            padding: '6px 10px',
                            width: '180px',
                            background: 'var(--input-bg)',
                            color: 'var(--text-color)',
                            border: '1px solid var(--border-color)',
                            borderRadius: '6px',
                            fontSize: '13px',
                        }}
                    />
                }
            >
                <ResizableTableWrapper>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th>Menu Item</th>
                                <th className="text-right">1-day</th>
                                <th className="text-right">2-day</th>
                                <th className="text-right">3-day</th>
                                <th className="text-right">5-day</th>
                                <th className="text-right">7-day</th>
                                <th className="text-right">10-day</th>
                                <th className="text-right">14-day</th>
                            </tr>
                        </thead>
                        <tbody>
                            {cumulativeDemandRows
                                .filter((row) =>
                                    !cumulativeSearch ||
                                    String(row.item_name).toLowerCase().includes(cumulativeSearch.toLowerCase())
                                )
                                .map((row) => (
                                <tr key={row.item_id}>
                                    <td>{row.item_name}</td>
                                    <td className="text-right">{(row.d1 as number).toFixed(1)}</td>
                                    <td className="text-right">{(row.d2 as number).toFixed(1)}</td>
                                    <td className="text-right">{(row.d3 as number).toFixed(1)}</td>
                                    <td className="text-right">{(row.d5 as number).toFixed(1)}</td>
                                    <td className="text-right">{(row.d7 as number).toFixed(1)}</td>
                                    <td className="text-right">{(row.d10 as number).toFixed(1)}</td>
                                    <td className="text-right">{(row.d14 as number).toFixed(1)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            </Card>
        </div>
    );
}
