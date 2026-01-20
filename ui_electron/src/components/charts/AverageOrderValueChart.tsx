/**
 * AverageOrderValueChart Component
 * 
 * Extracted from Insights.tsx for reusability in AI Mode
 */

import { useEffect, useState } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine, LabelList, Brush } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { filterByWeekdays, getVisibleHolidays, groupDataByTimeBucket } from '../../utils/chartUtils';

export function AverageOrderValueChart() {
    const [data, setData] = useState<any[]>([]);
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

        // Filter by selected weekdays
        const filtered = filterByWeekdays(data, selectedDays);

        // Group by time bucket
        const grouped = groupDataByTimeBucket(filtered, timeBucket);

        // Calculate AOV for each bucket
        return grouped.map(item => ({
            ...item,
            aov: item.num_orders > 0 ? item.revenue / item.num_orders : 0
        }));
    };

    const chartData = processData();

    // Get holidays within the visible date range
    const visibleHolidays = getVisibleHolidays(chartData, showHolidays);

    return (
        <>
            <div className="card">
                <h3>Average Order Value Trend</h3>

                {/* Controls */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '15px', marginBottom: '20px' }}>
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Time Bucket</label>
                        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                            <select
                                value={timeBucket}
                                onChange={(e) => setTimeBucket(e.target.value)}
                                style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
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
                                Show Holidays
                            </label>
                        </div>
                    </div>
                </div>

                {/* Weekday Selector */}
                <div style={{ marginBottom: '20px' }}>
                    <label style={{ display: 'block', marginBottom: '10px', fontSize: '14px', color: '#aaa' }}>Include Days</label>
                    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        {daysOfWeek.map((day, idx) => (
                            <button
                                key={day}
                                onClick={() => toggleDay(day)}
                                style={{
                                    padding: '6px 12px',
                                    backgroundColor: selectedDays.includes(day) ? '#646cff' : '#333',
                                    color: selectedDays.includes(day) ? 'white' : '#aaa',
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
                                        `AOV: ₹${Math.round(value).toLocaleString()}`,
                                        `Total Revenue: ₹${Math.round(payload.total_revenue || 0).toLocaleString()}`,
                                        `Total Orders: ${Math.round(payload.total_orders || 0)}`
                                    ];
                                }}
                                labelFormatter={(label) => `Period: ${label}`}
                            />
                            <Legend />
                            <Line type="monotone" dataKey="aov" stroke="#8B5CF6" name="AOV (₹)" strokeWidth={2} dot={{ r: 3 }} />

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

                <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                    * Average Order Value = Total Revenue / Total Orders for each period
                </p>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Average Order Value Trend (Fullscreen)</h3>
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
                                            `AOV: ₹${Math.round(value).toLocaleString()}`,
                                            `Total Revenue: ₹${Math.round(payload.total_revenue || 0).toLocaleString()}`,
                                            `Total Orders: ${Math.round(payload.total_orders || 0)}`
                                        ];
                                    }}
                                    labelFormatter={(label) => `Period: ${label}`}
                                />
                                <Legend />
                                <Line type="monotone" dataKey="aov" stroke="#8B5CF6" name="AOV (₹)" strokeWidth={2} dot={{ r: 3 }} />
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
