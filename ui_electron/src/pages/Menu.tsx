import { useState, useEffect } from 'react';
import { endpoints } from '../api';

export default function Menu() {
    const [stats, setStats] = useState<any[]>([]);
    const [types, setTypes] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);

    // Filters
    const [search, setSearch] = useState('');
    const [type, setType] = useState('All');

    useEffect(() => {
        loadTypes();
        loadData();
    }, []);

    // Reload data when filters change (debounced for search could be better, but simple for now)
    useEffect(() => {
        const timeoutId = setTimeout(() => {
            loadData();
        }, 300);
        return () => clearTimeout(timeoutId);
    }, [search, type]);

    const loadTypes = async () => {
        try {
            const res = await endpoints.menu.types();
            setTypes(['All', ...res.data]);
        } catch (e) {
            console.error("Failed to load types", e);
        }
    };

    const loadData = async () => {
        setLoading(true);
        try {
            const res = await endpoints.menu.items({
                name_search: search || undefined,
                type_choice: type
            });
            setStats(res.data);
        } catch (e) {
            console.error("Failed to load menu stats", e);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="page-container">
            <h1>Menu Analytics</h1>

            <div className="filters-bar" style={{ display: 'flex', gap: '15px', marginBottom: '20px' }}>
                <input
                    type="text"
                    placeholder="Search Item..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    style={{ padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                />

                <select
                    value={type}
                    onChange={(e) => setType(e.target.value)}
                    style={{ padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#333', color: 'white' }}
                >
                    {types.map(t => <option key={t} value={t}>{t}</option>)}
                </select>

                <button onClick={() => loadData()} style={{ padding: '8px 16px', background: '#646cff', border: 'none', borderRadius: '4px', color: 'white', cursor: 'pointer' }}>
                    Refresh
                </button>
            </div>

            {loading ? (
                <div>Loading...</div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid #444', textAlign: 'left' }}>
                                <th style={{ padding: '10px' }}>Item Name</th>
                                <th style={{ padding: '10px' }}>Type</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Sold (Qty)</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Revenue</th>
                                <th style={{ padding: '10px', textAlign: 'right' }}>Reorder Rate</th>
                            </tr>
                        </thead>
                        <tbody>
                            {stats.map((row, idx) => (
                                <tr key={idx} style={{ borderBottom: '1px solid #333' }}>
                                    <td style={{ padding: '10px' }}>{row["Item Name"]}</td>
                                    <td style={{ padding: '10px' }}>{row["Type"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Total Sold (Qty)"]}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>₹{Math.round(row["Total Revenue"]).toLocaleString()}</td>
                                    <td style={{ padding: '10px', textAlign: 'right' }}>{row["Reorder Rate %"]}%</td>
                                </tr>
                            ))}
                            {stats.length === 0 && (
                                <tr>
                                    <td colSpan={5} style={{ padding: '20px', textAlign: 'center', color: '#888' }}>
                                        No items found
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
