/**
 * Sales Trend Chart Component
 * 
 * Displays daily/weekly/monthly sales trends with various metrics and filters.
 */

import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine, Brush } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { CHART_TOOLTIP_STYLE } from './chartStyles';
import { applyMetric, calculateStrictMA, getHolidaysForChart, groupDataByTimeBucket } from '../../utils/chartUtils';

export function SalesTrendChart() {
    const [data, setData] = useState<any[]>([]);
    const [metric, setMetric] = useState('Moving Average (7-day)');
    const [timeBucket, setTimeBucket] = useState('Day');
    const [showHolidays, setShowHolidays] = useState(false);
    const [selectedDays, setSelectedDays] = useState(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']);
    const [isFullscreen, setIsFullscreen] = useState(false);

    const daysOfWeek = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const daysAbbr = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.salesTrend();
            setData(res.data);
        } catch (e) {
            console.error(e);
        }
    };

    const toggleDay = (day: string) => {
        if (selectedDays.includes(day)) {
            setSelectedDays(selectedDays.filter(d => d !== day));
        } else {
            setSelectedDays([...selectedDays, day]);
        }
    };

    const processData = () => {
        if (!data.length) return [];

        // For Moving Average, we need all data to calculate correctly based on calendar days,
        // even if some days are hidden from the final view.
        if (metric === 'Moving Average (7-day)') {
            return calculateStrictMA(data, selectedDays);
        } else {
            // Standard Logic for other metrics
            // Filter by selected weekdays FIRST
            const filtered = data.filter(row => {
                const date = new Date(row.date);
                const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
                return selectedDays.includes(dayName);
            });

            // Group by time bucket and apply metric
            const grouped = groupDataByTimeBucket(filtered, timeBucket);
            return applyMetric(grouped, metric);
        }
    };

    const chartData = processData();

    // Get holidays with X-axis positions mapped for the current time bucket
    const visibleHolidays = getHolidaysForChart(data, showHolidays, timeBucket);

    return (
        <>
            <div className="card">
                <h3>Daily Sales Trend</h3>

                {/* Controls */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px', marginBottom: '20px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Metric</label>
                        <select
                            value={metric}
                            onChange={(e) => setMetric(e.target.value)}
                            style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                        >
                            <option>Total</option>
                            <option>Average</option>
                            <option>Cumulative</option>
                            <option>Moving Average (7-day)</option>
                        </select>
                    </div>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Time Bucket</label>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <select
                                value={timeBucket}
                                onChange={(e) => setTimeBucket(e.target.value)}
                                disabled={metric === 'Moving Average (7-day)'}
                                style={{
                                    flex: 1,
                                    padding: '8px',
                                    borderRadius: '4px',
                                    border: '1px solid #444',
                                    backgroundColor: metric === 'Moving Average (7-day)' ? '#222' : '#333',
                                    color: metric === 'Moving Average (7-day)' ? '#666' : 'white',
                                    cursor: metric === 'Moving Average (7-day)' ? 'not-allowed' : 'pointer'
                                }}
                            >
                                <option>Day</option>
                                <option>Week</option>
                                <option>Month</option>
                            </select>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '14px', color: '#aaa', whiteSpace: 'nowrap' }}>
                                <input
                                    type="checkbox"
                                    checked={showHolidays}
                                    onChange={(e) => setShowHolidays(e.target.checked)}
                                    style={{ cursor: 'pointer' }}
                                />
                                Holidays
                            </label>
                        </div>
                    </div>
                </div>

                {/* Weekday Toggles */}
                <div style={{ marginBottom: '20px' }}>
                    <label style={{ display: 'block', marginBottom: '8px', fontSize: '14px', color: '#aaa' }}>Include Days:</label>
                    <div style={{ display: 'flex', gap: '8px' }}>
                        {daysOfWeek.map((day, idx) => (
                            <button
                                key={day}
                                onClick={() => toggleDay(day)}
                                style={{
                                    flex: 1,
                                    padding: '8px',
                                    background: selectedDays.includes(day) ? '#646cff' : '#333',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    fontSize: '13px',
                                    fontWeight: selectedDays.includes(day) ? 'bold' : 'normal'
                                }}
                            >
                                {daysAbbr[idx]}
                            </button>
                        ))}
                    </div>
                </div>

                {/* Chart */}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="date" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip
                                contentStyle={CHART_TOOLTIP_STYLE}
                                formatter={(value: any, _name: string | undefined, props: any) => {
                                    const payload = props.payload;
                                    return [
                                        `Revenue: ₹${Math.round(value).toLocaleString()}`,
                                        `Orders: ${Math.round(payload.num_orders || 0)}`
                                    ];
                                }}
                                labelFormatter={(label) => `Period: ${label}`}
                            />
                            <Legend />
                            <Line type="monotone" dataKey="value" stroke="#3B82F6" name={`${metric} Revenue (₹)`} strokeWidth={2} dot={{ r: 3 }} />

                            {/* Brush for Zoom */}
                            <Brush dataKey="date" height={30} stroke="#646cff" />

                            {/* Holiday Reference Lines */}
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
                                        fontSize: 11,
                                        offset: 10
                                    }}
                                />
                            ))}
                        </LineChart>
                    </ResponsiveContainer>
                </ResizableChart>

                {metric === 'Moving Average (7-day)' && (
                    <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                        * Time bucket is disabled for Moving Average (calculates daily)
                    </p>
                )}
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Sales Trend Analysis (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="date" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip
                                    contentStyle={CHART_TOOLTIP_STYLE}
                                    formatter={(value: any, _name: string | undefined, props: any) => {
                                        const payload = props.payload;
                                        return [
                                            `Revenue: ₹${Math.round(value).toLocaleString()}`,
                                            `Orders: ${Math.round(payload.num_orders || 0)}`
                                        ];
                                    }}
                                    labelFormatter={(label) => `Period: ${label}`}
                                />
                                <Legend />
                                <Line type="monotone" dataKey="value" stroke="#3B82F6" name={`${metric} Revenue (₹)`} strokeWidth={2} dot={{ r: 3 }} />
                                <Brush dataKey="date" height={30} stroke="#646cff" />
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
                                            fontSize: 11,
                                            offset: 10
                                        }}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}
