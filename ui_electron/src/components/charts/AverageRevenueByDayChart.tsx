import { useState, useEffect } from 'react';
import {
    BarChart,
    Bar,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    Cell
} from 'recharts';
import { endpoints } from '../../api';
import { CHART_TOOLTIP_STYLE } from './chartStyles';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';

export function AverageRevenueByDayChart() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const [isFullscreen, setIsFullscreen] = useState(false);

    // Default to empty (all time)
    const [startDate, setStartDate] = useState('');
    const [endDate, setEndDate] = useState('');

    useEffect(() => {
        loadData();
    }, [startDate, endDate]);

    const loadData = async () => {
        setLoading(true);
        try {
            const res = await endpoints.insights.avgRevenueByDay({
                start_date: startDate || undefined,
                end_date: endDate || undefined
            });
            setData(res.data);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    // Reusable chart content
    const renderChart = () => (
        <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 50 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                <XAxis
                    dataKey="day_name"
                    stroke="var(--text-secondary)"
                    tick={{ fill: 'var(--text-secondary)' }}
                    axisLine={{ stroke: 'var(--border-color)' }}
                />
                <YAxis
                    stroke="var(--text-secondary)"
                    tick={{ fill: 'var(--text-secondary)' }}
                    axisLine={{ stroke: 'var(--border-color)' }}
                    tickFormatter={(value) => `₹${value.toLocaleString()}`}
                />
                <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    formatter={(value: any) => [`₹${Math.round(Number(value) || 0).toLocaleString()}`, 'Avg Revenue']}
                />
                <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                    {data.map((_, index) => (
                        <Cell key={`cell-${index}`} fill="#3B82F6" />
                    ))}
                </Bar>
            </BarChart>
        </ResponsiveContainer>
    );

    return (
        <>
            <div className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                    <h3 style={{ margin: 0 }}>Average Revenue by Day of Week</h3>

                    {/* Filter Controls */}
                    <div style={{ display: 'flex', gap: '15px', alignItems: 'center' }}>
                        <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                            <label style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Begin:</label>
                            <input
                                type="date"
                                value={startDate}
                                onChange={e => setStartDate(e.target.value)}
                                className="styled-select"
                                style={{ minWidth: 'auto' }}
                            />
                        </div>
                        <div style={{ display: 'flex', gap: '5px', alignItems: 'center' }}>
                            <label style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>End:</label>
                            <input
                                type="date"
                                value={endDate}
                                onChange={e => setEndDate(e.target.value)}
                                className="styled-select"
                                style={{ minWidth: 'auto' }}
                            />
                        </div>
                    </div>
                </div>

                {/* Chart */}
                {loading ? (
                    <div style={{ height: '300px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        Loading...
                    </div>
                ) : (
                    <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                        {renderChart()}
                    </ResizableChart>
                )}
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Average Revenue by Day of Week (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        {renderChart()}
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}
