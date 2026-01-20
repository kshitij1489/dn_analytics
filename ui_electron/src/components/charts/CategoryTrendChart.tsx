/**
 * CategoryTrendChart Component
 * 
 * Extracted from Insights.tsx for reusability in AI Mode
 */

import { useEffect, useState } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine, LabelList, Brush } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { calculateStrictMA, filterByWeekdays, getVisibleHolidays, groupDataByTimeBucket } from '../../utils/chartUtils';

export function CategoryTrendChart() {
    const [data, setData] = useState<any[]>([]);
    const [metric, setMetric] = useState('Total');
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
            const res = await endpoints.insights.categoryTrend();
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

        const categories = [...new Set(data.map(row => row.category))];
        const result: any[] = [];

        categories.forEach(category => {
            const categoryData = data.filter(row => row.category === category);

            if (metric === 'Moving Average (7-day)') {
                // Now using the shared Strict MA logic per category
                const maData = calculateStrictMA(categoryData, selectedDays);
                result.push(...maData);
            } else {
                // Standard Logic
                const filtered = filterByWeekdays(categoryData, selectedDays);
                const grouped = groupDataByTimeBucket(filtered, timeBucket);
                const processed = applyMetric(grouped, metric);

                // Add category back to processed data as grouping might lose it if not careful 
                // (groupDataByTimeBucket doesn't preserve extra fields from first row necessarily? 
                //  Actually groupDataByTimeBucket implementation returns new objects: { date, revenue, num_orders, count }.
                //  So we MUST re-attach category.)
                processed.forEach(p => {
                    p.category = category;
                });
                result.push(...processed);
            }
        });

        return result;
    };

    const applyMetric = (data: any[], metric: string) => {
        if (metric === 'Total') {
            return data.map(d => ({ ...d, value: d.revenue }));
        } else if (metric === 'Average') {
            return data.map(d => ({
                ...d,
                value: d.revenue / (d.count || 1)
            }));
        } else if (metric === 'Cumulative') {
            let cumulative = 0;
            return data.map(d => {
                cumulative += d.revenue;
                return { ...d, value: cumulative };
            });
        }
        return data;
    };

    const chartData = processData();

    // Get unique categories for rendering separate lines
    const categories = [...new Set(chartData.map(d => d.category))];
    const colors = ['#3B82F6', '#EF4444', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#06B6D4'];

    // Get holidays within the visible date range
    const visibleHolidays = getVisibleHolidays(chartData, showHolidays);

    // Group data by date for recharts
    const chartDataByDate: { [key: string]: any } = {};
    chartData.forEach(item => {
        if (!chartDataByDate[item.date]) {
            chartDataByDate[item.date] = { date: item.date };
        }
        chartDataByDate[item.date][item.category] = item.value;
    });
    const formattedChartData = Object.values(chartDataByDate).sort((a, b) => a.date.localeCompare(b.date));

    return (
        <>
            <div className="card">
                <h3>Sales by Category Trend</h3>

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
                        <LineChart data={formattedChartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="date" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                            <Legend />

                            {/* Render a line for each category */}
                            {categories.map((category, idx) => (
                                <Line
                                    key={category}
                                    type="monotone"
                                    dataKey={category}
                                    stroke={colors[idx % colors.length]}
                                    name={`${category} (₹)`}
                                    strokeWidth={2}
                                    dot={{ r: 3 }}
                                />
                            ))}

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
                    <h3>Sales by Category Trend (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={formattedChartData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="date" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }} />
                                <Legend />
                                {categories.map((category, idx) => (
                                    <Line
                                        key={category}
                                        type="monotone"
                                        dataKey={category}
                                        stroke={colors[idx % colors.length]}
                                        name={`${category} (₹)`}
                                        strokeWidth={2}
                                        dot={{ r: 3 }}
                                    />
                                ))}
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
