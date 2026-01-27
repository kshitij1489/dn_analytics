/**
 * RevenueByCategoryChart Component
 * 
 * Extracted from Insights.tsx for reusability in AI Mode
 */

import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { CHART_TOOLTIP_STYLE } from './chartStyles';

export function RevenueByCategoryChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const res = await endpoints.insights.revenueByCategory();
            const categories = res.data.categories || res.data;  // Handle both old and new format
            const totalSystemRevenue = res.data.total_system_revenue || (Array.isArray(categories) ? categories.reduce((sum: number, cat: any) => sum + (cat.revenue || 0), 0) : 0);

            // Calculate revenue percentage for each category
            const dataWithPct = (Array.isArray(categories) ? categories : []).map((cat: any) => ({
                ...cat,
                rev_pct: totalSystemRevenue > 0 ? (cat.revenue / totalSystemRevenue) * 100 : 0,
                pct_label: totalSystemRevenue > 0 ? `${((cat.revenue / totalSystemRevenue) * 100).toFixed(1)}%` : '0%',
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
                <h3>Revenue by Category</h3>
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="category" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                            <Bar dataKey="revenue" fill="#F59E0B" name="Revenue (₹)">
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
                    <h3>Revenue by Category (Fullscreen)</h3>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="category" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip contentStyle={CHART_TOOLTIP_STYLE} />
                                <Bar dataKey="revenue" fill="#F59E0B" name="Revenue (₹)">
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
