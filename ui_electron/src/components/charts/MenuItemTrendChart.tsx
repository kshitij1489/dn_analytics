/**
 * Per–menu-item trends (volume, quantity, revenue) with Sales-trend style controls.
 * Server aggregates match Menu → Summary rollups + Menu Items revenue; bucketing uses shared chartUtils.
 */

import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
    Brush,
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { endpoints } from '../../api';
import { TabButton } from '../TabButton';
import { ResizableChart } from '../ResizableChart';
import { CHART_TOOLTIP_STYLE } from './chartStyles';
import { FullscreenModal } from './FullscreenModal';
import {
    applyMetric,
    calculateStrictMA,
    filterByWeekdays,
    getHolidaysForChart,
    groupDataByTimeBucket,
    rowsWithRevenueFromMeasure,
} from '../../utils/chartUtils';

const SERIES_COLORS = ['#3B82F6', '#EF4444', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#06B6D4', '#84CC16'];

const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] as const;
const DAYS_ABBR = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const;

type MeasureKind = 'volume' | 'quantity' | 'revenue';

export interface MenuTrendItem {
    menu_item_id: string;
    name: string;
}

function seriesDataKey(menuItemId: string): string {
    return `s_${menuItemId.replace(/[^a-zA-Z0-9_]/g, '_')}`;
}

function formatYValue(kind: MeasureKind, v: number): string {
    if (!Number.isFinite(v)) return '—';
    if (kind === 'revenue') return `₹${Math.round(v).toLocaleString()}`;
    if (kind === 'quantity') return Math.round(v).toLocaleString();
    if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
    return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

const selectLabelStyle: CSSProperties = {
    display: 'block',
    marginBottom: '5px',
    fontSize: '12px',
    color: 'var(--text-secondary)',
};

const selectControlStyle: CSSProperties = {
    width: '100%',
    padding: '8px',
    height: '38px',
    boxSizing: 'border-box',
    borderRadius: '8px',
    border: '1px solid var(--border-color)',
    backgroundColor: 'var(--input-bg)',
    color: 'var(--text-color)',
    fontSize: '12px',
};

export function MenuItemTrendChart({
    throughBusinessDate,
    lastDbSync,
}: {
    /** When set, caps the series at this business date (e.g. Menu Summary “through” date). Otherwise the API uses the current business date. */
    throughBusinessDate?: string;
    lastDbSync?: number;
}) {
    const [measureKind, setMeasureKind] = useState<MeasureKind>('volume');
    const [metric, setMetric] = useState('Moving Average (7-day)');
    const [timeBucket, setTimeBucket] = useState('Day');
    const [showHolidays, setShowHolidays] = useState(false);
    const [selectedDays, setSelectedDays] = useState<string[]>([...DAYS_OF_WEEK]);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [menuList, setMenuList] = useState<MenuTrendItem[]>([]);
    const [selectedItems, setSelectedItems] = useState<MenuTrendItem[]>([]);
    const [addPickerKey, setAddPickerKey] = useState(0);
    const [rawRows, setRawRows] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    useEffect(() => {
        const loadList = async () => {
            try {
                const res = await endpoints.menu.list();
                const rows = (res.data as { menu_item_id: string; name: string }[]).map((r) => ({
                    menu_item_id: r.menu_item_id,
                    name: r.name,
                }));
                setMenuList(rows);
            } catch {
                setMenuList([]);
            }
        };
        void loadList();
    }, [lastDbSync]);

    useEffect(() => {
        if (selectedItems.length === 0) {
            setRawRows([]);
            setLoadError(null);
            return;
        }
        const load = async () => {
            setLoading(true);
            setLoadError(null);
            try {
                const res = await endpoints.menu.summaryTimeseries({
                    menu_item_ids: selectedItems.map((i) => i.menu_item_id).join(','),
                    ...(throughBusinessDate ? { end_date: throughBusinessDate } : {}),
                });
                setRawRows(res.data.data ?? []);
            } catch (e: unknown) {
                const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
                    || (e as Error)?.message
                    || 'Failed to load series';
                setLoadError(msg);
                setRawRows([]);
            } finally {
                setLoading(false);
            }
        };
        void load();
    }, [selectedItems, throughBusinessDate, lastDbSync]);

    const measureField = measureKind;

    const formattedChartData = useMemo(() => {
        if (!selectedItems.length || !rawRows.length) return [];

        const chartDataByDate: Record<string, Record<string, number | string>> = {};

        for (const item of selectedItems) {
            const itemRows = rawRows.filter((r) => r.menu_item_id === item.menu_item_id);
            if (!itemRows.length) continue;

            const asRevenue = rowsWithRevenueFromMeasure(itemRows, measureField);
            const key = seriesDataKey(item.menu_item_id);

            let processed: { date: string; value: number }[];
            if (metric === 'Moving Average (7-day)') {
                processed = calculateStrictMA(asRevenue, selectedDays);
            } else {
                const filtered = filterByWeekdays(asRevenue, selectedDays);
                const grouped = groupDataByTimeBucket(filtered, timeBucket);
                processed = applyMetric(grouped, metric);
            }

            for (const p of processed) {
                const d = p.date as string;
                if (!chartDataByDate[d]) chartDataByDate[d] = { date: d };
                chartDataByDate[d][key] = p.value as number;
            }
        }

        return Object.values(chartDataByDate).sort((a, b) =>
            String(a.date).localeCompare(String(b.date)),
        );
    }, [rawRows, selectedItems, measureField, metric, timeBucket, selectedDays]);

    const lineKeys = useMemo(
        () => selectedItems.map((i) => ({ key: seriesDataKey(i.menu_item_id), label: i.name })),
        [selectedItems],
    );

    const visibleHolidays = useMemo(
        () => getHolidaysForChart(rawRows, showHolidays, metric === 'Moving Average (7-day)' ? 'Day' : timeBucket),
        [rawRows, showHolidays, metric, timeBucket],
    );

    const toggleDay = (day: string) => {
        setSelectedDays((prev) =>
            prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day],
        );
    };

    const addItemFromPicker = (menuItemId: string) => {
        if (!menuItemId) return;
        const row = menuList.find((m) => m.menu_item_id === menuItemId);
        if (!row) return;
        if (selectedItems.some((s) => s.menu_item_id === menuItemId)) return;
        if (selectedItems.length >= 15) return;
        setSelectedItems((prev) => [...prev, row]);
        setAddPickerKey((k) => k + 1);
    };

    const renderLineChart = () => (
        <LineChart data={formattedChartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
            <XAxis dataKey="date" stroke="var(--text-secondary)" tick={{ fontSize: 11 }} />
            <YAxis stroke="var(--text-secondary)" tick={{ fontSize: 11 }} />
            <Tooltip
                contentStyle={CHART_TOOLTIP_STYLE}
                formatter={(value: number | undefined, seriesName: string | undefined) => {
                    const v = typeof value === 'number' ? value : Number(value);
                    return [formatYValue(measureKind, v), seriesName || 'Series'];
                }}
                labelFormatter={(label) => `Period: ${label}`}
            />
            <Legend />
            {lineKeys.map((lk, idx) => (
                <Line
                    key={lk.key}
                    type="monotone"
                    dataKey={lk.key}
                    name={lk.label.length > 36 ? `${lk.label.slice(0, 34)}…` : lk.label}
                    stroke={SERIES_COLORS[idx % SERIES_COLORS.length]}
                    strokeWidth={2}
                    dot={{ r: 2 }}
                    connectNulls
                />
            ))}
            <Brush dataKey="date" height={28} stroke="var(--accent-color, #646cff)" />
            {visibleHolidays.map((holiday, idx) => (
                <ReferenceLine
                    key={idx}
                    x={holiday.xPosition}
                    stroke="#F59E0B"
                    strokeDasharray="4 4"
                    strokeWidth={2}
                    label={{
                        value: holiday.name,
                        position: 'top',
                        fill: '#F59E0B',
                        fontSize: 10,
                        offset: 8,
                    }}
                />
            ))}
        </LineChart>
    );

    return (
        <div
            style={{
                background: 'var(--card-bg)',
                padding: '20px',
                borderRadius: '12px',
                marginBottom: '20px',
                border: '1px solid var(--border-color)',
                boxShadow: 'var(--shadow)',
            }}
        >
            <h3 style={{ marginTop: 0, marginBottom: '12px', color: 'var(--accent-color)' }}>
                Menu item trend
            </h3>

            <div
                style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px 16px',
                    marginBottom: '14px',
                }}
            >
                <div
                    className="segmented-control"
                    style={{
                        width: 'fit-content',
                        maxWidth: '100%',
                        flexWrap: 'wrap',
                        flex: '0 0 auto',
                    }}
                >
                    {(
                        [
                            { id: 'volume' as const, label: 'Volume' },
                            { id: 'quantity' as const, label: 'Quantity' },
                            { id: 'revenue' as const, label: 'Revenue' },
                        ]
                    ).map((t) => (
                        <TabButton
                            key={t.id}
                            active={measureKind === t.id}
                            onClick={() => setMeasureKind(t.id)}
                            variant="segmented"
                        >
                            {t.label}
                        </TabButton>
                    ))}
                </div>
                <div
                    style={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'flex-end',
                        gap: '6px',
                        marginLeft: 'auto',
                    }}
                >
                    <label style={{ ...selectLabelStyle, marginBottom: 0, textAlign: 'right' }}>Include days</label>
                    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        {DAYS_OF_WEEK.map((day, idx) => (
                            <button
                                key={day}
                                type="button"
                                onClick={() => toggleDay(day)}
                                style={{
                                    padding: '6px 10px',
                                    borderRadius: '8px',
                                    border: '1px solid var(--border-color)',
                                    background: selectedDays.includes(day) ? 'var(--accent-color, #3B82F6)' : 'var(--input-bg)',
                                    color: selectedDays.includes(day) ? '#fff' : 'var(--text-color)',
                                    fontSize: '12px',
                                    cursor: 'pointer',
                                }}
                            >
                                {DAYS_ABBR[idx]}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            <div
                style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: '12px',
                    alignItems: 'flex-end',
                    marginBottom: '16px',
                }}
            >
                <div style={{ minWidth: '200px', flex: '1 1 200px' }}>
                    <label style={selectLabelStyle}>Metric</label>
                    <select
                        value={metric}
                        onChange={(e) => setMetric(e.target.value)}
                        style={selectControlStyle}
                    >
                        <option>Total</option>
                        <option>Average</option>
                        <option>Moving Average (7-day)</option>
                    </select>
                </div>
                <div style={{ minWidth: '200px', flex: '1 1 200px' }}>
                    <label style={selectLabelStyle}>Time bucket</label>
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                        <select
                            value={timeBucket}
                            onChange={(e) => setTimeBucket(e.target.value)}
                            disabled={metric === 'Moving Average (7-day)'}
                            style={{
                                ...selectControlStyle,
                                flex: 1,
                                opacity: metric === 'Moving Average (7-day)' ? 0.6 : 1,
                                cursor: metric === 'Moving Average (7-day)' ? 'not-allowed' : 'pointer',
                            }}
                        >
                            <option>Day</option>
                            <option>Week</option>
                            <option>Month</option>
                        </select>
                        <label
                            style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: '6px',
                                fontSize: '12px',
                                color: 'var(--text-secondary)',
                                whiteSpace: 'nowrap',
                            }}
                        >
                            <input
                                type="checkbox"
                                checked={showHolidays}
                                onChange={(e) => setShowHolidays(e.target.checked)}
                            />
                            Holidays
                        </label>
                    </div>
                </div>
                <div style={{ minWidth: '220px', flex: '1 1 220px' }}>
                    <label style={selectLabelStyle}>Add menu item</label>
                    <select
                        key={addPickerKey}
                        defaultValue=""
                        onChange={(e) => {
                            addItemFromPicker(e.target.value);
                            e.target.value = '';
                        }}
                        style={selectControlStyle}
                    >
                        <option value="">Choose an item…</option>
                        {menuList
                            .filter((m) => !selectedItems.some((s) => s.menu_item_id === m.menu_item_id))
                            .map((m) => (
                                <option key={m.menu_item_id} value={m.menu_item_id}>
                                    {m.name}
                                </option>
                            ))}
                    </select>
                </div>
            </div>

            {selectedItems.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '14px' }}>
                    {selectedItems.map((s) => (
                        <button
                            key={s.menu_item_id}
                            type="button"
                            onClick={() =>
                                setSelectedItems((prev) => prev.filter((p) => p.menu_item_id !== s.menu_item_id))
                            }
                            style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '6px',
                                padding: '6px 10px',
                                borderRadius: '999px',
                                border: '1px solid var(--border-color)',
                                background: 'var(--input-bg)',
                                color: 'var(--text-color)',
                                fontSize: '12px',
                                cursor: 'pointer',
                            }}
                            title="Remove from chart"
                        >
                            <span style={{ maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                {s.name}
                            </span>
                            <span aria-hidden style={{ opacity: 0.7 }}>×</span>
                        </button>
                    ))}
                </div>
            )}

            {loadError && (
                <p style={{ color: '#EF4444', fontSize: '13px', marginBottom: '10px' }}>{loadError}</p>
            )}

            {selectedItems.length === 0 ? (
                <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
                    Add at least one menu item to see the chart (up to the last 365 business days through the current business date).
                </p>
            ) : loading ? (
                <p style={{ color: 'var(--text-secondary)' }}>Loading series…</p>
            ) : (
                <>
                    <div style={{ width: '100%', height: '360px' }}>
                        <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                            <ResponsiveContainer width="100%" height="100%">
                                {renderLineChart()}
                            </ResponsiveContainer>
                        </ResizableChart>
                    </div>

                    {metric === 'Moving Average (7-day)' && (
                        <p style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                            Time bucket is disabled for moving average (daily calendar window).
                        </p>
                    )}
                    <p style={{ marginTop: '6px', fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                        Week = week ending Sunday; month = calendar month starting on the 1st.
                    </p>
                </>
            )}

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div
                    style={{
                        height: '100%',
                        display: 'flex',
                        flexDirection: 'column',
                        background: 'var(--card-bg)',
                        padding: '16px',
                        borderRadius: '12px',
                    }}
                >
                    <h3 style={{ marginTop: 0, color: 'var(--accent-color)' }}>Menu item trend (fullscreen)</h3>
                    <div style={{ flex: 1, minHeight: 0 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            {renderLineChart()}
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </div>
    );
}
