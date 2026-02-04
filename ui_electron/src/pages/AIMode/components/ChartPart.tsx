import {
    BarChart, Bar, LineChart, Line, PieChart, Pie, Cell,
    XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from 'recharts';
import { CHART_COLORS } from '../constants';

export interface ChartPartConfig {
    data?: unknown[];
    chart_type?: 'bar' | 'line' | 'pie';
    x_key?: string;
    y_key?: string;
    title?: string;
}

const tooltipStyle = {
    backgroundColor: 'rgba(0,0,0,0.85)',
    border: '1px solid #444',
    borderRadius: '8px',
    color: '#fff'
};

export function ChartPart({ data = [], chart_type = 'bar', x_key = 'label', y_key = 'value', title = 'Chart' }: ChartPartConfig) {
    const chartData = (Array.isArray(data) ? data : []) as Record<string, unknown>[];

    return (
        <div>
            <div style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: '15px' }}>{title}</div>
            <div style={{ height: '300px', width: '100%' }}>
                <ResponsiveContainer width="100%" height="100%">
                    {chart_type === 'bar' ? (
                        <BarChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey={x_key} stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={tooltipStyle} />
                            <Legend />
                            <Bar dataKey={y_key} fill="#3B82F6" name={y_key} />
                        </BarChart>
                    ) : chart_type === 'line' ? (
                        <LineChart data={chartData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#444" />
                            <XAxis dataKey={x_key} stroke="#aaa" />
                            <YAxis stroke="#aaa" />
                            <Tooltip contentStyle={tooltipStyle} />
                            <Legend />
                            <Line type="monotone" dataKey={y_key} stroke="#3B82F6" strokeWidth={2} dot={{ r: 4 }} name={y_key} />
                        </LineChart>
                    ) : (
                        <PieChart>
                            <Pie
                                data={chartData}
                                dataKey={y_key}
                                nameKey={x_key}
                                cx="50%"
                                cy="50%"
                                outerRadius={100}
                                label={({ name, percent }: { name?: string; percent?: number }) =>
                                    `${name ?? 'Unknown'}: ${((percent ?? 0) * 100).toFixed(0)}%`
                                }
                            >
                                {chartData.map((_: unknown, index: number) => (
                                    <Cell key={`cell-${index}`} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                                ))}
                            </Pie>
                            <Tooltip contentStyle={tooltipStyle} />
                            <Legend />
                        </PieChart>
                    )}
                </ResponsiveContainer>
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '10px' }}>
                {chartData.length} data point{chartData.length !== 1 ? 's' : ''}
            </div>
        </div>
    );
}
