/**
 * TopItemsChart Component
 * 
 * Extracted from Insights.tsx for reusability in AI Mode
 */

import { useEffect, useState } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LabelList } from 'recharts';
import { endpoints } from '../../api';
import { ResizableChart } from '../ResizableChart';
import { FullscreenModal } from './FullscreenModal';
import { CHART_TOOLTIP_STYLE } from './chartStyles';

export function TopItemsChart() {
    const [data, setData] = useState<any[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [beginDate, setBeginDate] = useState('');
    const [endDate, setEndDate] = useState('');

    useEffect(() => {
        loadData();
    }, [beginDate, endDate]);

    const loadData = async () => {
        try {
            const params: { start_date?: string; end_date?: string } = {};
            if (beginDate && endDate && beginDate <= endDate) {
                params.start_date = beginDate;
                params.end_date = endDate;
            }
            const res = await endpoints.insights.topItems(params);
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
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', flexWrap: 'wrap', gap: '15px' }}>
                    <h3 style={{ margin: 0 }}>Top 10 Most Sold Items</h3>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                            Begin:
                            <input
                                type="date"
                                value={beginDate}
                                onChange={(e) => setBeginDate(e.target.value)}
                                style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid var(--border-color)', background: 'var(--input-bg)', color: 'var(--text-color)', fontSize: '12px' }}
                            />
                        </label>
                        <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                            End:
                            <input
                                type="date"
                                value={endDate}
                                onChange={(e) => setEndDate(e.target.value)}
                                style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid var(--border-color)', background: 'var(--input-bg)', color: 'var(--text-color)', fontSize: '12px' }}
                            />
                        </label>
                        {(beginDate || endDate) && (
                            <button
                                type="button"
                                style={{ padding: '6px 10px', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '12px', background: '#9CA3AF', color: 'white' }}
                                onClick={() => { setBeginDate(''); setEndDate(''); }}
                            >
                                Clear range
                            </button>
                        )}
                    </div>
                </div>
                {(beginDate && endDate && beginDate <= endDate) && (
                    <p style={{ margin: '0 0 12px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                        Quantity and revenue for selected date range (business days 5:00 AM–4:59 AM IST)
                    </p>
                )}
                <ResizableChart onFullscreen={() => setIsFullscreen(true)}>
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={data}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey="name" stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip
                                contentStyle={CHART_TOOLTIP_STYLE}
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
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', flexWrap: 'wrap', gap: '15px' }}>
                        <h3 style={{ margin: 0 }}>Top 10 Most Sold Items (Fullscreen)</h3>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                Begin:
                                <input
                                    type="date"
                                    value={beginDate}
                                    onChange={(e) => setBeginDate(e.target.value)}
                                    style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid var(--border-color)', background: 'var(--input-bg)', color: 'var(--text-color)', fontSize: '12px' }}
                                />
                            </label>
                            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                                End:
                                <input
                                    type="date"
                                    value={endDate}
                                    onChange={(e) => setEndDate(e.target.value)}
                                    style={{ padding: '6px 8px', borderRadius: '4px', border: '1px solid var(--border-color)', background: 'var(--input-bg)', color: 'var(--text-color)', fontSize: '12px' }}
                                />
                            </label>
                            {(beginDate || endDate) && (
                                <button
                                    type="button"
                                    style={{ padding: '6px 10px', border: 'none', borderRadius: '6px', cursor: 'pointer', fontSize: '12px', background: '#9CA3AF', color: 'white' }}
                                    onClick={() => { setBeginDate(''); setEndDate(''); }}
                                >
                                    Clear range
                                </button>
                            )}
                        </div>
                    </div>
                    <div style={{ flex: 1 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                                <XAxis dataKey="name" stroke="#aaa" />
                                <YAxis stroke="#aaa" />
                                <Tooltip
                                    contentStyle={CHART_TOOLTIP_STYLE}
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
