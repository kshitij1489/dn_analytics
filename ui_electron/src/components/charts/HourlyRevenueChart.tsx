/**
 * HourlyRevenueChart Component
 * 
 * Extracted from Insights.tsx for reusability in AI Mode
 */

import { useEffect, useState, useRef } from 'react';
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { CHART_TOOLTIP_STYLE } from './chartStyles';

type ViewMode = 'cumulative' | 'daily';

interface DailyDataEntry {
    date: string;
    data: any[];
}

// Color palette for different date lines
const LINE_COLORS = ['#10B981', '#3B82F6', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6', '#F97316'];

// Day labels and their PostgreSQL DOW values (0=Sunday, 1=Monday, ..., 6=Saturday)
const DAYS_CONFIG = [
    { label: 'Mon', dow: 1 },
    { label: 'Tue', dow: 2 },
    { label: 'Wed', dow: 3 },
    { label: 'Thu', dow: 4 },
    { label: 'Fri', dow: 5 },
    { label: 'Sat', dow: 6 },
    { label: 'Sun', dow: 0 }
];

export function HourlyRevenueChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [viewMode, setViewMode] = useState<ViewMode>('cumulative');
    const [selectedDates, setSelectedDates] = useState<string[]>([]);
    const [dailyData, setDailyData] = useState<DailyDataEntry[]>([]);
    const [selectedDays, setSelectedDays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]); // All days
    const [beginDate, setBeginDate] = useState('');
    const [endDate, setEndDate] = useState('');
    const dateInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (viewMode === 'cumulative') loadData();
    }, [selectedDays, beginDate, endDate, viewMode]);

    // Business day hour order for x-axis: 5 AM → 4 AM
    const CUMULATIVE_HOUR_ORDER = [
        ...Array.from({ length: 19 }, (_, i) => i + 5),
        ...Array.from({ length: 5 }, (_, i) => i)
    ];

    const loadData = async () => {
        try {
            const params: { days?: number[]; start_date?: string; end_date?: string } = {};
            if (selectedDays.length < 7) params.days = selectedDays;
            if (beginDate && endDate && beginDate <= endDate) {
                params.start_date = beginDate;
                params.end_date = endDate;
            }
            const res = await endpoints.insights.hourlyRevenue(params);
            const byHour: Record<number, any> = {};
            (res.data || []).forEach((d: any) => {
                byHour[d.hour_num] = {
                    ...d,
                    hour_label: formatHour(d.hour_num)
                };
            });
            // Ensure all 24 hours in business order (5 AM → 4 AM), fill 0 for missing
            const formatted = CUMULATIVE_HOUR_ORDER.map((hour_num) =>
                byHour[hour_num] ?? {
                    hour_num,
                    hour_label: formatHour(hour_num),
                    revenue: 0,
                    avg_revenue: 0
                }
            );
            setData(formatted);
        } catch (e) {
            console.error(e);
        }
    };

    const formatHour = (h: number) => {
        if (h === 0) return '12 AM';
        if (h === 12) return 'Noon';
        if (h < 12) return `${h} AM`;
        return `${h - 12} PM`;
    };

    const formatDateLabel = (dateStr: string) => {
        const date = new Date(dateStr);
        return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    };

    const handleAddDate = () => {
        dateInputRef.current?.showPicker();
    };

    const handleDateChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const date = e.target.value;
        if (!date || selectedDates.includes(date)) return;

        try {
            const res = await endpoints.insights.hourlyRevenueByDate(date);
            const formatted = res.data.map((d: any) => ({
                hour_num: d.hour_num,
                hour_label: formatHour(d.hour_num),
                revenue: d.revenue
            }));

            setSelectedDates(prev => [...prev, date]);
            setDailyData(prev => [...prev, { date, data: formatted }]);
        } catch (e) {
            console.error('Failed to fetch data for date:', date, e);
        }

        // Reset input value
        e.target.value = '';
    };

    const handleReset = () => {
        setSelectedDates([]);
        setDailyData([]);
    };

    const toggleDay = (dow: number) => {
        if (selectedDays.includes(dow)) {
            if (selectedDays.length > 1) {
                setSelectedDays(selectedDays.filter(d => d !== dow));
            }
        } else {
            setSelectedDays([...selectedDays, dow]);
        }
    };

    // Business day order: 5 AM → 4:59 AM (5, 6, ..., 23, 0, 1, 2, 3, 4)
    const BUSINESS_DAY_HOUR_ORDER = [
        ...Array.from({ length: 19 }, (_, i) => i + 5),
        ...Array.from({ length: 5 }, (_, i) => i)
    ];

    // Build combined data for line chart (all hours with revenue per date)
    const buildDailyChartData = () => {
        const hours = BUSINESS_DAY_HOUR_ORDER.map((hour_num) => ({
            hour_num,
            hour_label: formatHour(hour_num)
        }));

        // Track running totals for each date
        const runningTotals: { [key: string]: number } = {};
        selectedDates.forEach(date => { runningTotals[date] = 0; });

        return hours.map(hour => {
            const entry: any = { ...hour };
            dailyData.forEach(({ date, data }) => {
                const match = data.find(d => d.hour_num === hour.hour_num);
                const revenue = match?.revenue || 0;
                entry[date] = revenue;

                // Calculate and store cumulative revenue
                if (!runningTotals[date]) runningTotals[date] = 0;
                runningTotals[date] += revenue;
                entry[`${date}_cumulative`] = runningTotals[date];
            });
            return entry;
        });
    };

    const dailyChartData = buildDailyChartData();

    const controlsRowStyle = {
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '12px',
        gap: '12px',
        flexWrap: 'wrap' as const
    };

    const segmentedControlStyle = {
        display: 'flex',
        background: 'var(--input-bg)',
        borderRadius: '8px',
        padding: '3px',
        border: '1px solid var(--border-color)',
        width: 'fit-content'
    };

    const buttonStyle = (isActive: boolean) => ({
        padding: '6px 16px',
        border: 'none',
        borderRadius: '6px',
        cursor: 'pointer',
        fontSize: '12px',
        fontWeight: 600,
        transition: 'all 0.2s',
        background: isActive ? 'var(--card-bg)' : 'transparent',
        color: isActive ? 'var(--text-color)' : 'var(--text-secondary)',
        boxShadow: isActive ? '0 1px 3px rgba(0,0,0,0.1)' : 'none'
    });

    const dayButtonStyle = (isActive: boolean) => ({
        padding: '6px 12px',
        border: 'none',
        borderRadius: '4px',
        cursor: 'pointer',
        fontSize: '13px',
        fontWeight: 600,
        transition: 'all 0.2s',
        background: isActive ? '#646cff' : 'var(--input-bg)',
        color: isActive ? 'white' : 'var(--text-secondary)',
        minWidth: '40px'
    });

    const actionButtonStyle = {
        padding: '6px 12px',
        border: '1px solid var(--border-color)',
        borderRadius: '6px',
        cursor: 'pointer',
        fontSize: '12px',
        fontWeight: 600,
        background: 'var(--input-bg)',
        color: 'var(--text-color)',
        display: 'flex',
        alignItems: 'center',
        gap: '6px'
    };

    const clearButtonStyle = {
        padding: '4px 8px',
        border: 'none',
        borderRadius: '12px',
        cursor: 'pointer',
        fontSize: '11px',
        fontWeight: 500,
        background: '#9CA3AF',
        color: 'white',
        display: 'flex',
        alignItems: 'center',
        gap: '4px'
    };

    const CustomTooltip = ({ active, payload, label }: any) => {
        if (!active || !payload || !payload.length) return null;

        return (
            <div style={{ ...CHART_TOOLTIP_STYLE, padding: '8px', fontSize: '12px' }}>
                <p style={{ margin: '0 0 8px', fontWeight: 600, borderBottom: '1px solid var(--border-color)', paddingBottom: '4px' }}>
                    {label}
                </p>
                {payload.map((entry: any, index: number) => {
                    const date = entry.name;
                    const revenue = entry.value;
                    const cumulative = entry.payload[`${date}_cumulative`];

                    return (
                        <div key={index} style={{ marginBottom: '6px', color: entry.color }}>
                            <div style={{ fontWeight: 600 }}>{formatDateLabel(date)}</div>
                            <div style={{ display: 'flex', gap: '8px', fontSize: '11px', opacity: 0.9 }}>
                                <span>Hr Rev: ₹{revenue?.toLocaleString()}</span>
                                <span>|</span>
                                <span>Till now: ₹{cumulative?.toLocaleString()}</span>
                            </div>
                        </div>
                    );
                })}
            </div>
        );
    };

    const renderDayFilters = () => (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Days:</span>
            {DAYS_CONFIG.map(({ label, dow }) => (
                <button
                    key={dow}
                    style={dayButtonStyle(selectedDays.includes(dow))}
                    onClick={() => toggleDay(dow)}
                >
                    {label}
                </button>
            ))}
        </div>
    );

    const renderDateRangeFilters = () => (
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                Begin:
                <input
                    type="date"
                    value={beginDate}
                    onChange={(e) => setBeginDate(e.target.value)}
                    style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid var(--border-color)', background: 'var(--input-bg)', color: 'var(--text-color)', fontSize: '12px' }}
                />
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                End:
                <input
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid var(--border-color)', background: 'var(--input-bg)', color: 'var(--text-color)', fontSize: '12px' }}
                />
            </label>
            {(beginDate || endDate) && (
                <button
                    style={{ ...clearButtonStyle, padding: '6px 10px' }}
                    onClick={() => { setBeginDate(''); setEndDate(''); }}
                >
                    Clear range
                </button>
            )}
        </div>
    );

    const renderDailyControls = () => (
        <div style={{ display: 'flex', gap: '8px' }}>
            {selectedDates.length > 0 && (
                <button style={clearButtonStyle} onClick={handleReset}>
                    ✕ Clear
                </button>
            )}
            <button style={actionButtonStyle} onClick={handleAddDate}>
                📅 Add Date
            </button>
            <input
                ref={dateInputRef}
                type="date"
                onChange={handleDateChange}
                style={{ position: 'absolute', opacity: 0, pointerEvents: 'none' }}
            />
        </div>
    );

    const renderCumulativeChart = () => (
        <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
            <XAxis dataKey="hour_label" stroke="#aaa" interval={0} tick={{ fontSize: 10 }} angle={-45} textAnchor="end" height={50} />
            <YAxis stroke="#aaa" />
            <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
            <Legend />
            <Bar dataKey="avg_revenue" fill="#10B981" name="Avg Revenue (₹)" />
        </BarChart>
    );

    const renderDailyChart = () => (
        <LineChart data={dailyChartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
            <XAxis dataKey="hour_label" stroke="#aaa" interval={0} tick={{ fontSize: 10 }} angle={-45} textAnchor="end" height={50} />
            <YAxis stroke="#aaa" />
            <Tooltip content={<CustomTooltip />} />
            <Legend formatter={(value) => formatDateLabel(value)} />
            {selectedDates.map((date, idx) => (
                <Line
                    key={date}
                    type="monotone"
                    dataKey={date}
                    stroke={LINE_COLORS[idx % LINE_COLORS.length]}
                    strokeWidth={2}
                    dot={{ r: 4 }}
                    activeDot={{ r: 6 }}
                    name={date}
                />
            ))}
        </LineChart>
    );

    return (
        <>
            <div className="card">
                <h3>Hourly Revenue Analysis (Local Time - IST)</h3>
                <div style={controlsRowStyle}>
                    <div style={segmentedControlStyle}>
                        <button
                            style={buttonStyle(viewMode === 'cumulative')}
                            onClick={() => setViewMode('cumulative')}
                        >
                            Cumulative
                        </button>
                        <button
                            style={buttonStyle(viewMode === 'daily')}
                            onClick={() => setViewMode('daily')}
                        >
                            Daily
                        </button>
                    </div>
                    {viewMode === 'daily' && (
                        <span style={{ fontSize: '13px', color: '#666', fontStyle: 'italic', fontWeight: 500 }}>
                            Select & View Multiple Dates
                            <span style={{ marginLeft: '6px', fontSize: '16px' }}>→</span>
                        </span>
                    )}
                    {viewMode === 'cumulative' && (
                        <>
                            {renderDayFilters()}
                            {renderDateRangeFilters()}
                        </>
                    )}
                    {viewMode === 'daily' && renderDailyControls()}
                </div>
                {viewMode === 'cumulative' && (beginDate || endDate) && (
                    <p style={{ margin: '0 0 12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                        Hourly revenue for selected date range (business days 5:00 AM–4:59 AM IST)
                    </p>
                )}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        {viewMode === 'cumulative' ? renderCumulativeChart() : renderDailyChart()}
                    </ResponsiveContainer>
                </ResizableChart>
                <p style={{ marginTop: '15px', fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                    Each bar/hour = revenue in the hour starting at that time (e.g. 11 PM = 11:00 PM–12:00 AM).
                </p>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Hourly Revenue Analysis (Local Time - IST) (Fullscreen)</h3>
                    <div style={controlsRowStyle}>
                        <div style={segmentedControlStyle}>
                            <button
                                style={buttonStyle(viewMode === 'cumulative')}
                                onClick={() => setViewMode('cumulative')}
                            >
                                Cumulative
                            </button>
                            <button
                                style={buttonStyle(viewMode === 'daily')}
                                onClick={() => setViewMode('daily')}
                            >
                                Daily
                            </button>
                        </div>
                        {viewMode === 'daily' && (
                            <span style={{ fontSize: '13px', color: '#666', fontStyle: 'italic', fontWeight: 500 }}>
                                Select & View Multiple Dates
                                <span style={{ marginLeft: '6px', fontSize: '16px' }}>→</span>
                            </span>
                        )}
                        {viewMode === 'cumulative' && (
                            <>
                                {renderDayFilters()}
                                {renderDateRangeFilters()}
                            </>
                        )}
                        {viewMode === 'daily' && renderDailyControls()}
                    </div>
                    {viewMode === 'cumulative' && (beginDate || endDate) && (
                        <p style={{ margin: '0 0 12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                            Hourly revenue for selected date range (business days 5:00 AM–4:59 AM IST)
                        </p>
                    )}
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            {viewMode === 'cumulative' ? renderCumulativeChart() : renderDailyChart()}
                        </ResponsiveContainer>
                    </div>
                    <p style={{ marginTop: '15px', fontSize: '12px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                        Each bar/hour = revenue in the hour starting at that time (e.g. 11 PM = 11:00 PM–12:00 AM).
                    </p>
                </div>
            </FullscreenModal>
        </>
    );
}
