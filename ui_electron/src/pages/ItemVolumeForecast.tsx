import { useState, useMemo, useEffect } from 'react';
import { endpoints } from '../api';
import { Card, LoadingSpinner, ResizableTableWrapper, Select, ErrorPopup } from '../components';
import type { PopupMessage } from '../components';
import {
    ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, Area
} from 'recharts';

interface ItemInfo {
    item_id: string;
    item_name: string;
    unit: string;
}
interface VolumeHistoryPoint {
    date: string;
    item_id: string;
    volume: number;
}
interface VolumeForecastPoint {
    date: string;
    item_id: string;
    item_name: string;
    unit: string;
    p50: number;
    p90: number;
    probability: number;
    volume_value?: number;
    recommended_volume?: number;
}
interface VolumeBacktestPoint {
    date: string;
    item_id: string;
    item_name: string;
    unit: string;
    p50: number;
    p90: number;
    probability: number;
}

interface VolumeForecastResponse {
    items: ItemInfo[];
    history: VolumeHistoryPoint[];
    forecast: VolumeForecastPoint[];
    backtest?: VolumeBacktestPoint[];
    awaiting_action?: boolean;
    message?: string;
    cloud_not_configured?: boolean;
}

const formatDate = (isoStr: string) => {
    const d = new Date(isoStr);
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
};

const formatVolume = (val: number, unit: string) => {
    if (unit === 'units' || unit === 'pcs') return `${Math.round(val).toLocaleString()} pcs`;
    if (unit === 'g') {
        if (val >= 1000) {
            const kg = val / 1000;
            return kg >= 10 ? `${Math.round(kg).toLocaleString()} kg` : `${kg.toFixed(1)} kg`;
        }
        return `${Math.round(val).toLocaleString()} g`;
    }
    return `${Math.round(val).toLocaleString()} ${unit}`;
};

export default function ItemVolumeForecast({ trainingActive = false }: { trainingActive?: boolean }) {
    const [data, setData] = useState<VolumeForecastResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [selectedItemId, setSelectedItemId] = useState<string>('');
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const [cumulativeSearch, setCumulativeSearch] = useState('');

    useEffect(() => {
        if (!trainingActive) loadData();
    }, [trainingActive]);

    const loadData = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await endpoints.forecast.volume({ days: 14 });
            setData(res.data);
            if (!selectedItemId && res.data.items?.length > 0) {
                setSelectedItemId(res.data.items[0].item_id);
            }
        } catch (e: any) {
            if (e?.response?.status === 503) {
                // Training in progress — don't show error, parent overlay handles it
                return;
            }
            console.error('Failed to fetch volume forecast:', e);
            const detail = e.response?.data?.detail || e.message || 'Failed to load volume forecast';
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
                dataMap.set(h.date, { date: h.date, historical: h.volume });
            });

        data.backtest
            ?.filter(b => b.item_id === selectedItemId)
            .forEach(b => {
                const entry = dataMap.get(b.date) || { date: b.date };
                entry.backtest_p50 = b.p50;
                entry.backtest_p90 = b.p90;
                dataMap.set(b.date, entry);
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
        return data.items.map(v => ({
            value: v.item_id,
            label: `${v.item_name} (${v.unit})`,
        }));
    }, [data]);

    const cumulativeVolumeRows = useMemo(() => {
        if (!data?.forecast?.length) return [];
        const horizons = [1, 2, 3, 5, 7, 10, 14] as const;
        const byItem = new Map<string, VolumeForecastPoint[]>();
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
                const unit = sorted[0]?.unit ?? 'g';
                const row: Record<string, string | number> = { item_id, item_name, unit };
                horizons.forEach((n) => {
                    const sum = sorted
                        .slice(0, n)
                        .reduce((acc, f) => acc + (f.p50 ?? f.volume_value ?? 0), 0);
                    row[`d${n}`] = Math.round(sum);
                });
                return row;
            });
    }, [data]);

    const selectedUnit = data?.items?.find(v => v.item_id === selectedItemId)?.unit ?? 'g';

    if (loading) return (
        <div className="forecast-loading">
            <LoadingSpinner />
            <p>Loading volume forecast...</p>
        </div>
    );

    if (error) return (
        <div className="forecast-error" style={{ padding: '40px', textAlign: 'center' }}>
            <p>❌ {error}</p>
            <button onClick={loadData} style={{ marginTop: '12px', padding: '8px 16px', cursor: 'pointer' }}>Retry</button>
        </div>
    );

    return (
        <div className="item-forecast-section">
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            {data?.awaiting_action && (
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
                    <span>{data.message || 'Volume forecast cache is empty. Use Pull from Cloud or Full Retrain to populate.'}</span>
                    <button
                        onClick={async () => {
                            try {
                                const res = await endpoints.forecast.pullFromCloud('volume');
                                const msg = res.data as { volume_inserted?: number };
                                setPopup({ type: 'success', message: `Done. Volume: ${msg.volume_inserted ?? 0}` });
                                loadData();
                            } catch (e: any) {
                                setPopup({ type: 'error', message: e.response?.data?.detail || "Pull failed" });
                            }
                        }}
                        style={{ padding: '8px 14px', background: 'rgba(34, 197, 94, 0.2)', color: '#22c55e', border: '1px solid rgba(34, 197, 94, 0.4)', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
                    >
                        Pull from Cloud
                    </button>
                    <button
                        onClick={async () => {
                            try {
                                await endpoints.forecast.fullRetrain('volume');
                                setPopup({ type: 'info', message: 'Volume retrain started. This might take a few minutes.' });
                                loadData();
                            } catch (e: any) {
                                setPopup({ type: 'error', message: e.response?.data?.detail || "Retrain failed" });
                            }
                        }}
                        style={{ padding: '8px 14px', background: 'rgba(59, 130, 246, 0.2)', color: '#3b82f6', border: '1px solid rgba(59, 130, 246, 0.4)', borderRadius: '6px', cursor: 'pointer', fontWeight: 600 }}
                    >
                        Full Retrain
                    </button>
                </div>
            )}
            <Card
                title="Volume Forecast"
                headerAction={
                    <div className="item-forecast-controls">
                        <Select
                            value={selectedItemId}
                            onChange={setSelectedItemId}
                            options={itemOptions}
                            placeholder="Select menu item..."
                        />
                    </div>
                }
            >
                <div className="forecast-chart-container">
                    {chartData.length === 0 ? (
                        <div className="empty-state-message">
                            {data?.items?.length ? 'Select a menu item to view its volume forecast.' : 'No volume data available. Configure menu items with variants (unit: Count/mg/ml) and value.'}
                        </div>
                    ) : (
                        <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                                <XAxis dataKey="date" type="category" tickFormatter={formatDate} tick={{ fill: 'var(--text-secondary)', fontSize: 12 }} />
                                <YAxis tick={{ fill: 'var(--text-secondary)', fontSize: 12 }} axisLine={false}
                                    label={{ value: `Volume (${selectedUnit})`, angle: -90, position: 'insideLeft', fill: 'var(--text-secondary)' }} />
                                <Tooltip contentStyle={{ backgroundColor: 'var(--card-bg)', borderColor: 'var(--border-color)', color: 'var(--text-color)' }}
                                    labelFormatter={formatDate}
                                    formatter={(val: any) => [typeof val === 'number' ? val.toLocaleString(undefined, { maximumFractionDigits: 0 }) : val, '']} />
                                <Legend />
                                <Area type="monotone" dataKey="unified_p90" stroke="none" fill="#F59E0B" fillOpacity={0.15} name="P90" legendType="none" connectNulls />
                                <Area type="monotone" dataKey="unified_p50" stroke="none" fill="var(--card-bg)" fillOpacity={1} legendType="none" connectNulls />
                                <Line type="monotone" dataKey="historical" name="Historical Volume" stroke="var(--accent-color)" strokeWidth={3} dot={false} connectNulls />
                                <Line type="monotone" dataKey="unified_p50" name="Predicted (p50)" stroke="#10B981" strokeWidth={2} strokeDasharray="5 5" dot={false} connectNulls />
                                <Line type="monotone" dataKey="unified_p90" name="Predicted (p90)" stroke="#F59E0B" strokeWidth={2} strokeDasharray="2 2" dot={false} connectNulls />
                            </ComposedChart>
                        </ResponsiveContainer>
                    )}
                </div>
            </Card>

            <Card title="14-Day Volume Forecast" style={{ marginTop: '20px' }}>
                <ResizableTableWrapper>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Menu Item</th>
                                <th className="text-right">Volume (p50)</th>
                                <th className="text-right">P(Sale)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data?.forecast?.length ? (
                                data.forecast
                                    .filter(f => !selectedItemId || f.item_id === selectedItemId)
                                    .sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())
                                    .slice(0, 14)
                                    .map((f, idx) => (
                                        <tr key={idx}>
                                            <td>{formatDate(f.date)}</td>
                                            <td>{f.item_name} ({f.unit})</td>
                                            <td className="text-right">{formatVolume(f.p50 ?? f.volume_value ?? 0, f.unit)}</td>
                                            <td className="text-right">{((f.probability ?? 0) * 100).toFixed(0)}%</td>
                                        </tr>
                                    ))
                            ) : (
                                <tr>
                                    <td colSpan={4} style={{ textAlign: 'center', padding: '20px', color: 'var(--text-secondary)' }}>
                                        No forecast data
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            </Card>

            <Card
                title="Cumulative Volume (1d–14d)"
                style={{ marginTop: '20px' }}
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
                            {cumulativeVolumeRows.length ? (
                                cumulativeVolumeRows
                                    .filter((row) =>
                                        !cumulativeSearch ||
                                        String(row.item_name).toLowerCase().includes(cumulativeSearch.toLowerCase())
                                    )
                                    .map((row, idx) => (
                                        <tr key={idx}>
                                            <td>{row.item_name} ({row.unit})</td>
                                            <td className="text-right">{formatVolume(Number(row.d1 ?? 0), String(row.unit))}</td>
                                            <td className="text-right">{formatVolume(Number(row.d2 ?? 0), String(row.unit))}</td>
                                            <td className="text-right">{formatVolume(Number(row.d3 ?? 0), String(row.unit))}</td>
                                            <td className="text-right">{formatVolume(Number(row.d5 ?? 0), String(row.unit))}</td>
                                            <td className="text-right">{formatVolume(Number(row.d7 ?? 0), String(row.unit))}</td>
                                            <td className="text-right">{formatVolume(Number(row.d10 ?? 0), String(row.unit))}</td>
                                            <td className="text-right">{formatVolume(Number(row.d14 ?? 0), String(row.unit))}</td>
                                        </tr>
                                    ))
                            ) : (
                                <tr>
                                    <td colSpan={8} style={{ textAlign: 'center', padding: '20px', color: 'var(--text-secondary)' }}>
                                        No data available
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            </Card>
        </div>
    );
}
