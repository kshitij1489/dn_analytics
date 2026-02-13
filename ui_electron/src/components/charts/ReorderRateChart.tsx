
/**
 * Reorder Rate Chart Component
 * 
 * Displays the percentage of orders that are reorders from verified customers.
 */

import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Brush } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { CHART_TOOLTIP_STYLE } from './chartStyles';
import { TabButton } from '../TabButton';

export function ReorderRateChart() {
    const [data, setData] = useState<any[]>([]);
    const [granularity, setGranularity] = useState('day'); // day, week, month
    const [metric, setMetric] = useState<'orders' | 'customers'>('orders'); // orders, customers
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [isLoading, setIsLoading] = useState(false);

    useEffect(() => {
        loadData();
    }, [granularity, metric]);

    const loadData = async () => {
        setIsLoading(true);
        try {
            const res = await endpoints.insights.reorderRateTrend({ granularity, metric });
            setData(res.data || []);
        } catch (e) {
            console.error(e);
            setData([]);
        } finally {
            setIsLoading(false);
        }
    };

    const getXAxisLabel = (tick: string) => {
        if (!tick) return '';
        if (granularity === 'month' && tick.length >= 7) return tick.substring(0, 7); // YYYY-MM
        return tick;
    };

    const getMetricLabel = () => metric === 'orders' ? 'Repeat Order Rate' : 'Repeat Customer Rate';

    const renderChart = () => (
        <ResponsiveContainer width="100%" height="100%">
            {data.length > 0 ? (
                <LineChart data={data}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                    <XAxis
                        dataKey="date"
                        stroke="#aaa"
                        tickFormatter={getXAxisLabel}
                        minTickGap={30}
                    />
                    <YAxis stroke="#aaa" unit="%" domain={[0, 100]} />
                    <Tooltip
                        contentStyle={CHART_TOOLTIP_STYLE}
                        formatter={(value: any, _name: string | undefined, props: any) => {
                            const payload = props.payload;
                            return [
                                `${value}%`,
                                metric === 'orders' ? `Total Orders: ${payload.total_orders}` : `Total Customers: ${payload.total_orders}`,
                                metric === 'orders' ? `Reordered: ${payload.reordered_orders}` : `Returning: ${payload.reordered_orders}`
                            ];
                        }}
                        labelFormatter={(label) => `Period: ${label}`}
                    />
                    <Legend />
                    <Line
                        type="monotone"
                        dataKey="value"
                        stroke="#10B981"
                        name={getMetricLabel()}
                        strokeWidth={2}
                        dot={{ r: 3 }}
                        activeDot={{ r: 6 }}
                    />
                    <Brush dataKey="date" height={30} stroke="#646cff" />
                </LineChart>
            ) : (
                <div style={{
                    height: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#aaa',
                    flexDirection: 'column',
                    gap: '10px'
                }}>
                    {isLoading ? 'Loading...' : 'No data available for this period'}
                </div>
            )}
        </ResponsiveContainer>
    );

    return (
        <>
            <div className="card">
                <h3>{getMetricLabel()}</h3>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '15px', flexWrap: 'wrap', gap: '10px' }}>
                    {/* Metric Toggle */}
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Metric</label>
                        <div className="segmented-control" style={{ width: 'fit-content' }}>
                            <TabButton
                                active={metric === 'orders'}
                                onClick={() => setMetric('orders')}
                                variant="segmented"
                                size="small"
                            >
                                Repeat Orders
                            </TabButton>
                            <TabButton
                                active={metric === 'customers'}
                                onClick={() => setMetric('customers')}
                                variant="segmented"
                                size="small"
                            >
                                Repeat Customers
                            </TabButton>
                        </div>
                    </div>

                    {/* Time Window */}
                    <div>
                        <label style={{ display: 'block', marginBottom: '5px', fontSize: '14px', color: '#aaa' }}>Time Window</label>
                        <div className="segmented-control" style={{ width: 'fit-content' }}>
                            {['Day', 'Week', 'Month'].map(opt => {
                                const val = opt.toLowerCase();
                                return (
                                    <TabButton
                                        key={val}
                                        active={granularity === val}
                                        onClick={() => setGranularity(val)}
                                        variant="segmented"
                                        size="small"
                                    >
                                        {opt}
                                    </TabButton>
                                );
                            })}
                        </div>
                    </div>
                </div>

                {/* Chart */}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    {renderChart()}
                </ResizableChart>

                <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                    {metric === 'orders'
                        ? '* Orders placed by non-first-time customers'
                        : '* % of active customers who had at least one prior order.'}
                </p>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>{getMetricLabel()} (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        {renderChart()}
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}

