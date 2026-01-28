import { useState, useEffect } from 'react';
import { endpoints } from '../api';
import { ClientSideDataTable } from '../components/ClientSideDataTable';
import { LLM_PROMPT_TEXT } from '../constants/prompts';

type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

export default function SQLConsole() {
    const [activeTab, setActiveTab] = useState<'query' | 'prompt'>('query');

    // Query Tab State
    const [query, setQuery] = useState('');
    const [results, setResults] = useState<any[]>([]);
    const [columns, setColumns] = useState<string[]>([]);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [queryExecuted, setQueryExecuted] = useState(false);
    const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');

    // Prompt Tab State
    const [copyFeedback, setCopyFeedback] = useState('');

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
        setQueryExecuted(false);

        try {
            const res = await endpoints.sql.query(query);
            if (res.data.error) {
                setError(res.data.error);
            } else {
                setResults(res.data.rows || []);
                setColumns(res.data.columns || []);
            }
        } catch (err: any) {
            setError(err.response?.data?.detail || 'Failed to execute query');
        } finally {
            setLoading(false);
            setQueryExecuted(true);
        }
    };





    const handleCopy = () => {
        navigator.clipboard.writeText(LLM_PROMPT_TEXT).then(() => {
            setCopyFeedback('Copied!');
            setTimeout(() => setCopyFeedback(''), 2000);
        });
    };

    const handleReset = async () => {
        if (!window.confirm("WARNING: This will completely wipe the database and recreate it from schema.sql. \n\nAre you sure you want to proceed?")) {
            return;
        }

        try {
            setLoading(true); // Reuse loading state to disable UI
            const res = await endpoints.system.reset();
            if (res.data.status === 'success') {
                alert("Database reset successfully!");
                // Re-check connection after reset as the connection might need re-establishing or just to be safe
                checkConnection();
            } else {
                alert("Failed to reset database: " + res.data.message);
            }
        } catch (err: any) {
            alert("Error resetting database: " + (err.response?.data?.detail || err.message));
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>
            {/* Header / Tabs */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
                <div style={{ display: 'flex', gap: '5px', background: 'white', padding: '5px', borderRadius: '30px', boxShadow: '0 2px 10px rgba(0,0,0,0.1)', minWidth: '300px' }}>
                    <button
                        onClick={() => setActiveTab('query')}
                        style={{
                            flex: 1,
                            padding: '12px',
                            background: activeTab === 'query' ? '#3B82F6' : 'transparent',
                            color: activeTab === 'query' ? 'white' : 'black',
                            border: 'none',
                            borderRadius: '25px',
                            cursor: 'pointer',
                            fontWeight: activeTab === 'query' ? 600 : 500,
                            transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                            boxShadow: activeTab === 'query' ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
                        }}
                    >
                        &gt;_ SQL Query
                    </button>
                    <button
                        onClick={() => setActiveTab('prompt')}
                        style={{
                            flex: 1,
                            padding: '12px',
                            background: activeTab === 'prompt' ? '#3B82F6' : 'transparent',
                            color: activeTab === 'prompt' ? 'white' : 'black',
                            border: 'none',
                            borderRadius: '25px',
                            cursor: 'pointer',
                            fontWeight: activeTab === 'prompt' ? 600 : 500,
                            transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                            boxShadow: activeTab === 'prompt' ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
                        }}
                    >
                        🤖 LLM Prompt
                    </button>
                </div>

                <button
                    onClick={handleReset}
                    disabled={loading}
                    style={{
                        padding: '12px 24px',
                        background: '#fee2e2',
                        color: '#dc2626',
                        border: 'none',
                        borderRadius: '25px',
                        cursor: loading ? 'not-allowed' : 'pointer',
                        fontWeight: 600,
                        transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                        boxShadow: '0 2px 5px rgba(220, 38, 38, 0.2)',
                        opacity: loading ? 0.7 : 1
                    }}
                >
                    {loading ? 'Resetting...' : '⚠️ Reset DB'}
                </button>
            </div>

            {/* Content Area */}
            {activeTab === 'query' ? (
                // SQL Query View
                <>
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
                                border: '1px solid #ddd',
                                borderRadius: '8px',
                                backgroundColor: '#f5f5f5', // Light Grey
                                color: '#333',
                                resize: 'vertical'
                            }}
                        />
                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '10px' }}>
                            <button
                                onClick={executeQuery}
                                disabled={loading || !query.trim() || connectionStatus === 'disconnected'}
                                style={{
                                    padding: '10px 20px',
                                    background: connectionStatus === 'disconnected' ? '#666' : '#3B82F6',
                                    color: 'white',
                                    border: 'none',
                                    borderRadius: '8px',
                                    cursor: connectionStatus === 'disconnected' ? 'not-allowed' : 'pointer',
                                    opacity: (loading || !query.trim() || connectionStatus === 'disconnected') ? 0.5 : 1,
                                    fontWeight: 'bold',
                                    boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
                                }}
                            >
                                {loading ? 'Executing...' : 'Execute Query'}
                            </button>
                        </div>
                    </div>

                    {error && (
                        <div style={{
                            marginTop: '20px',
                            padding: '15px',
                            backgroundColor: '#fee2e2',
                            border: '1px solid #ef4444',
                            borderRadius: '8px',
                            color: '#b91c1c'
                        }}>
                            <strong>Error:</strong> {error}
                        </div>
                    )}

                    {queryExecuted && !error && results.length > 0 && (
                        <ClientSideDataTable
                            data={results}
                            columns={columns}
                            filenamePrefix="sql_query_results"
                        />
                    )}

                    {queryExecuted && !error && results.length === 0 && (
                        <div style={{ marginTop: '20px', padding: '15px', color: '#666', fontStyle: 'italic', textAlign: 'center' }}>
                            {columns.length > 0 ? '0 rows returned (Empty Result Set)' : 'No results found.'}
                        </div>
                    )}
                </>
            ) : (
                // LLM Prompt View
                <div className="card" style={{ position: 'relative' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                        <h3 style={{ margin: 0, color: '#333' }}>Schema Context Prompt</h3>
                        <button
                            onClick={handleCopy}
                            style={{
                                padding: '4px 8px',
                                background: copyFeedback ? '#10B981' : '#E5E7EB', // Green if copied, otherwise Light Grey
                                color: copyFeedback ? 'white' : '#374151', // Dark grey text on light grey bg
                                border: '1px solid #D1D5DB', // Subtle border
                                borderRadius: '4px',
                                cursor: 'pointer',
                                fontWeight: '500',
                                transition: 'all 0.2s',
                                fontSize: '0.8em',
                                display: 'flex',
                                alignItems: 'center',
                                gap: '4px'
                            }}
                        >
                            {copyFeedback || '📋 Copy'}
                        </button>
                    </div>

                    <pre style={{
                        background: '#f5f5f5', // Light Grey
                        padding: '20px',
                        borderRadius: '8px',
                        border: '1px solid #ddd',
                        overflowX: 'auto',
                        whiteSpace: 'pre-wrap',
                        fontFamily: 'monospace',
                        fontSize: '0.9em',
                        color: '#333',
                        maxHeight: '70vh',
                        overflowY: 'auto',
                        lineHeight: '1.5'
                    }}>
                        {LLM_PROMPT_TEXT}
                    </pre>
                </div>
            )}
        </div>
    );
}
