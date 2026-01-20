/**
 * TopItemsChart Component
 * 
 * Extracted from Insights.tsx for reusability in AI Mode
 */

import { useEffect, useState } from 'react';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine, LabelList, Brush } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { INDIAN_HOLIDAYS } from '../../constants/holidays';

export function TopItemsChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.topItems();
            const topItems = res.data.items || res.data;  // Handle both old and new format
            const totalSystemRevenue = res.data.total_system_revenue || (Array.isArray(topItems) ? topItems.reduce((sum: number, item: any) => sum + (item.item_revenue || 0), 0) : 0);

            const dataSlice = Array.isArray(topItems) ? topItems.slice(0, 10) : [];

            // Calculate revenue percentage for each item using total system revenue
            const dataWithPct = dataSlice.map((item: any) => ({
                ...item,
                rev_pct: totalSystemRevenue > 0 ? (item.item_revenue / totalSystemRevenue) * 100 : 0,
                pct_label: totalSystemRevenue > 0 ? `${((item.item_revenue / totalSystemRevenue) * 100).toFixed(1)}%` : '0%',
                total_system_revenue: totalSystemRevenue  // Store for caption
            }));

            setData(dataWithPct);
        } catch (e) {
            console.error(e);
        }
    };

    // Get total system revenue from first item (they all have the same value)
    const totalRevenue = data.length > 0 ? data[0].total_system_revenue : 0;

    return (
        <>
            <div className="card">
                <h3>Top 10 Most Sold Items</h3>
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="name" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip
                                contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                formatter={(value: any, name: string | undefined) => {
                                    if (name === 'Quantity Sold') return Math.round(value);
                                    return value;
                                }}
                                labelFormatter={(label) => `Item: ${label}`}
                            />
                            <Bar dataKey="total_sold" fill="#8B5CF6" name="Quantity Sold">
                                <LabelList
                                    dataKey="pct_label"
                                    position="top"
                                    style={{ fill: '#fff', fontWeight: 'bold', fontSize: '12px' }}
                                />
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                </ResizableChart>
                <p style={{ marginTop: '10px', fontSize: '12px', color: '#888', fontStyle: 'italic' }}>
                    * Revenue % calculated against total system revenue: ₹{totalRevenue.toLocaleString()}
                </p>
            </div>

            <FullscreenModal isOpen={isFullscreen} onClose={() => setIsFullscreen(false)}>
                <div className="card" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                    <h3>Top 10 Most Sold Items (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="name" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip
                                    contentStyle={{ backgroundColor: '#2d2d2d', border: '1px solid #444' }}
                                    formatter={(value: any, name: string | undefined) => {
                                        if (name === 'Quantity Sold') return Math.round(value);
                                        return value;
                                    }}
                                    labelFormatter={(label) => `Item: ${label}`}
                                />
                                <Bar dataKey="total_sold" fill="#8B5CF6" name="Quantity Sold">
                                    <LabelList
                                        dataKey="pct_label"
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
