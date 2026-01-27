/**
 * OrderSourceChart Component
 * 
 * Extracted from Insights.tsx for reusability in AI Mode
 */

import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { CHART_TOOLTIP_STYLE } from './chartStyles';

export function OrderSourceChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.orderSource();

            // Add formatted revenue labels
            const dataWithLabels = (res.data || []).map((item: any) => ({
                ...item,
                revenue_label: `₹${Math.round(item.revenue || 0).toLocaleString()}`
            }));

            setData(dataWithLabels);
        } catch (e) {
            console.error(e);
        }
    };

    return (
        <>
            <div className="card">
                <h3>Order Source Analysis</h3>
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="order_from" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                            <Bar dataKey="count" fill="#06B6D4" name="Orders">
                                <LabelList
                                    dataKey="revenue_label"
                                    position="top"
                                    style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                />
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ResizableChart>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Order Source Analysis (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="order_from" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                                <Bar dataKey="count" fill="#06B6D4" name="Orders">
                                    <LabelList
                                        dataKey="revenue_label"
                                        position="top"
                                        style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                    />
                                </Bar>
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </FullscreenModal>
        </>
    );
}
