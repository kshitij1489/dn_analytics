import { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { Card } from '..';
import { LoadingSpinner } from '..';
import { endpoints } from '../../api';

export function BrandAwarenessChart() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [granularity, setGranularity] = useState('sma'); // day, week, month, sma

    useEffect(() => {
        loadData();
    }, [granularity]);

    const loadData = async () => {
        setLoading(true);
        try {
            // For SMA, we fetch daily data and compute moving average locally
            const fetchGranularity = granularity === 'sma' ? 'day' : granularity;
            const response = await endpoints.insights.brandAwareness({ granularity: fetchGranularity });
            let rawData = response.data.data;

            if (granularity === 'sma') {
                // Compute 7-day SMA
                rawData = rawData.map((item: any, index: number, array: any[]) => {
                    if (index < 6) return { ...item, new_customers: item.new_customers }; // Not enough data for full window

                    const window = array.slice(index - 6, index + 1);
                    const sum = window.reduce((acc, curr) => acc + curr.new_customers, 0);
                    const avg = Math.round(sum / 7);
                    return { ...item, new_customers: avg };
                });
            }

            setData(rawData);
        } catch (error) {
            console.error(error);
        } finally {
            setLoading(false);
        }
    };

    return (
        <Card title="New Customer Growth (Brand Awareness)">
            <div style={{ marginBottom: '20px', display: 'flex', gap: '10px', alignItems: 'center' }}>
                <select
                    value={granularity}
                    onChange={(e) => setGranularity(e.target.value)}
                    style={{ padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                >
                    <option value="day">Daily</option>
                    <option value="week">Weekly</option>
                    <option value="month">Monthly</option>
                    <option value="sma">SMA (7 Day)</option>
                </select>
                <span style={{ color: '#888', fontSize: '0.9em' }}>
                    {granularity === 'sma' ? '7-Day Simple Moving Average' : 'Verified customers by first order date'}
                </span>
            </div>

            <div style={{ height: '400px', width: '100%' }}>
                {loading ? <LoadingSpinner /> : (
                    <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={data} margin={{ top: 10, right: 30, left: 20, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border-color)" />
                            <XAxis
                                dataKey="date"
                                tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                                tickFormatter={(val) => {
                                    if (granularity === 'month') return val;
                                    if (granularity === 'week') return val;
                                    return new Date(val).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
                                }}
                            />
                            <YAxis
                                tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                                axisLine={false}
                                allowDecimals={false}
                            />
                            <Tooltip
                                contentStyle={{ backgroundColor: 'var(--card-bg)', borderColor: 'var(--border-color)', color: 'var(--text-color)' }}
                            />
                            <Legend />
                            <Line
                                type="monotone"
                                dataKey="new_customers"
                                name={granularity === 'sma' ? "New Customers (7d SMA)" : "New Verified Customers"}
                                stroke="#10B981"
                                strokeWidth={3}
                                dot={{ r: 4, fill: '#10B981' }}
                                activeDot={{ r: 6 }}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                )}
            </div>
        </Card>
    );
}
