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
            const res = await endpoints.insights.orderSource(params);

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
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', flexWrap: 'wrap', gap: '15px' }}>
                    <h3 style={{ margin: 0 }}>Order Source Analysis</h3>
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
                        Orders for selected date range (business days 5:00 AM–4:59 AM IST)
                    </p>
                )}
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
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px', flexWrap: 'wrap', gap: '15px' }}>
                        <h3 style={{ margin: 0 }}>Order Source Analysis (Fullscreen)</h3>
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
