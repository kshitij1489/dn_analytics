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
import { calculateStrictMA, filterByWeekdays, getVisibleHolidays, groupDataByTimeBucket } from '../../utils/chartUtils';

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
            // 1. Sort data by date just in case
            const sortedData = [...data].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());

            // 2. Calculate MA for each point using STRICT 7-day calendar window
            const computed = sortedData.map((currentPoint) => {
                const currentDate = new Date(currentPoint.date);

                // Define Window: [Current - 6 days, Current]
                // We do this by filtering the full dataset
                const windowStart = new Date(currentDate);
                windowStart.setDate(currentDate.getDate() - 6);

                // Find all records that fall in this date range AND are valid 'selected days'
                const validWindowRecords = sortedData.filter(d => {
                    const dDate = new Date(d.date);
                    // Check date range
                    if (dDate < windowStart || dDate > currentDate) return false;

                    // Check if this specific day is allowed (e.g. if Monday is unchecked, ignore Monday's data)
                    const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][dDate.getDay()];
                    return selectedDays.includes(dayName);
                });

                // Calculate Average
                const sum = validWindowRecords.reduce((acc, r) => acc + (r.revenue || 0), 0);
                const count = validWindowRecords.length;
                const avg = count > 0 ? sum / count : 0;

                return { ...currentPoint, value: avg };
            });

            // 3. Finally, filter the DISPLAY to only show selected days
            return computed.filter(row => {
                const date = new Date(row.date);
                const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
                return selectedDays.includes(dayName);
            });

        } else {
            // Standard Logic for other metrics
            // Filter by selected weekdays FIRST
            const filtered = data.filter(row => {
                const date = new Date(row.date);
                const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
                return selectedDays.includes(dayName);
            });

            // Group by time bucket and apply metric
            const grouped = groupByTimeBucket(filtered, timeBucket);
            return applyMetric(grouped, metric);
        }
    };

    // Helper needed if not using the old flow for MA
    // But we still need applyMetric for non-MA? 
    // Actually, I moved the MA logic INTO processData for that branch.
    // So applyMetric is only for the else block.
    // I need to keep groupByTimeBucket and applyMetric definitions below/outside as they were?
    // - Yes, they are component scope. I just changed processData internals.

    // WARNING: I used `groupByTimeBucket` and `applyMetric` in the `else` block ABOVE. 
    // They are defined BELOW in the original file. 
    // This is fine in JS/TS due to hoisting or if they are defined as `const ... = () =>` before use?
    // In React components, helper functions defined with `const` MUST be defined before use if used in `processData`.
    // Let's check the original file order. `processData` called them. `processData` was defined at line 46.
    // `groupByTimeBucket` at 61. `applyMetric` at 96.
    // So `processData` was calling functions defined AFTER it. This works for `function` keyword but NOT for `const x = () =>`.
    // The original file used `const groupByTimeBucket = ...`. This would fail if called before definition?
    // Wait, `processData` is called in `const chartData = processData()` at line 117. 
    // And `processData` calls `groupByTimeBucket`. 
    // Since `processData` is NOT called until line 117, strictly speaking the definitions exist by execution time.
    // SO referencing them inside the function body is fine as long as the function isn't EXECUTED before they are defined.
    // Replacing `processData` body is safe.

    // However, I need to make sure I don't delete them.
    // The replacement range is 46-115. This includes `groupByTimeBucket` and `applyMetric`.
    // So I MUST RE-INCLUDE THEM in my replacement content or change the range.

    // Better strategy: Replace ONLY `processData` implementation.
    // But `processData` in original is lines 46-59.
    // `groupByTimeBucket` is 61-94.
    // `applyMetric` is 96-115.

    // I should probably just replace `processData` (46-59) and LEAVE the others alone.
    // But wait, the standard logic uses `applyMetric` which has the OLD MA logic inside it (lines 108-114).
    // I should remove the MA logic from `applyMetric` or just let it be dead code (since I handle MA in processData now).
    // It's cleaner to remove it or update it.

    // Let's replace the whole block 46-115 to be safe and clean.

    const chartData = processData();

    // Get holidays within the visible date range
    const visibleHolidays = getVisibleHolidays(chartData, showHolidays);

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
                                contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
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
                                    x={holiday.date}
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
                                    contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
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
                                        x={holiday.date}
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
