import { useState, useEffect } from 'react';
import { endpoints } from '../api';

type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

export default function SQLConsole() {
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<any[]>([]);
    const [columns, setColumns] = useState<string[]>([]);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');

    useEffect(() => {
        checkConnection();
    }, []);

    const checkConnection = async (attempt = 1) => {
        const maxAttempts = 3;
        setConnectionStatus('connecting');

        try {
            await endpoints.health();
            setConnectionStatus('connected');
        } catch (err) {
            if (attempt < maxAttempts) {
                // Wait 1 second before retrying
                setTimeout(() => checkConnection(attempt + 1), 1000);
            } else {
                setConnectionStatus('disconnected');
            }
        }
    };

    const executeQuery = async () => {
        if (!query.trim()) return;

        setLoading(true);
        setError('');
        setResults([]);
        setColumns([]);

        try {
            const res = await endpoints.sql.query(query);
            if (res.data.error) {
                setError(res.data.error);
            } else {
                setResults(res.data.data || []);
                setColumns(res.data.columns || []);
            }
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to execute query');
        } finally {
            setLoading(false);
        }
    };

    const getStatusDisplay = () => {
        switch (connectionStatus) {
            case 'connected':
                return { text: '🟢 Connected', color: '#10B981' };
            case 'connecting':
                return { text: '🟡 Connecting...', color: '#F59E0B' };
            case 'disconnected':
                return { text: '🔴 Not Connected', color: '#EF4444' };
        }
    };

    const status = getStatusDisplay();

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', marginBottom: '20px' }}>
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    padding: '8px 16px',
                    borderRadius: '4px',
                    backgroundColor: '#333',
                    fontSize: '14px',
                    fontWeight: 'bold',
                    color: status.color
                }}>
                    {status.text}
                    {connectionStatus === 'disconnected' && (
                        <button
                            onClick={() => checkConnection()}
                            style={{
                                marginLeft: '10px',
                                padding: '4px 12px',
                                fontSize: '12px',
                                background: '#646cff',
                                color: 'white',
                                border: 'none',
                                borderRadius: '4px',
                                cursor: 'pointer'
                            }}
                        >
                            Retry
                        </button>
                    )}
                </div>
            </div>

            <div className="card">
                <textarea
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Enter SQL query..."
                    rows={8}
                    style={{
                        width: '100%',
                        padding: '12px',
                        fontFamily: 'monospace',
                        fontSize: '14px',
                        border: '1px solid #444',
                        borderRadius: '4px',
                        backgroundColor: '#1a1a1a',
                        color: 'white',
                        resize: 'vertical'
                    }}
                />
                <button
                    onClick={executeQuery}
                    disabled={loading || !query.trim() || connectionStatus === 'disconnected'}
                    style={{
                        marginTop: '10px',
                        padding: '10px 20px',
                        background: connectionStatus === 'disconnected' ? '#666' : '#646cff',
                        color: 'white',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: connectionStatus === 'disconnected' ? 'not-allowed' : 'pointer',
                        opacity: (loading || !query.trim() || connectionStatus === 'disconnected') ? 0.5 : 1
                    }}
                >
                    {loading ? 'Executing...' : 'Execute Query'}
                </button>
            </div>

            {error && (
                <div style={{
                    marginTop: '20px',
                    padding: '15px',
                    backgroundColor: '#3d1a1a',
                    border: '1px solid #991b1b',
                    borderRadius: '4px',
                    color: '#fca5a5'
                }}>
                    <strong>Error:</strong> {error}
                </div>
            )}

            {results.length > 0 && (
                <div className="card" style={{ marginTop: '20px', overflowX: 'auto' }}>
                    <p style={{ marginBottom: '10px', color: '#aaa' }}>
                        {results.length} row{results.length !== 1 ? 's' : ''} returned
                    </p>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9em' }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid #444' }}>
                                {columns.map((col, idx) => (
                                    <th key={idx} style={{ padding: '10px', textAlign: 'left', fontWeight: 'bold' }}>
                                        {col}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {results.map((row, idx) => (
                                <tr key={idx} style={{ borderBottom: '1px solid #333' }}>
                                    {columns.map((col, colIdx) => (
                                        <td key={colIdx} style={{ padding: '10px' }}>
                                            {row[col] !== null ? String(row[col]) : <span style={{ color: '#666' }}>NULL</span>}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
