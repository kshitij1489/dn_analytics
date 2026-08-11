import type { DebugLogEntry, CallTraceEntry, CacheCounter } from '../../../api';

interface DebugOverlayProps {
    entries: DebugLogEntry[];
    trace?: CallTraceEntry[];
    counters?: CacheCounter[];
    onClose: () => void;
}

const sectionTitle: React.CSSProperties = {
    color: '#808080',
    margin: '18px 0 8px',
    borderTop: '1px solid #333',
    paddingTop: '12px',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    fontSize: '0.75rem'
};

const th: React.CSSProperties = { textAlign: 'left', color: '#808080', fontWeight: 'normal', padding: '2px 10px 6px 0' };
const td: React.CSSProperties = { padding: '2px 10px 2px 0', color: '#a9b7c6', whiteSpace: 'nowrap' };

export function DebugOverlay({ entries, trace = [], counters = [], onClose }: DebugOverlayProps) {
    const totalHits = counters.reduce((s, c) => s + c.hits, 0);
    const totalMisses = counters.reduce((s, c) => s + c.misses, 0);
    const overall = totalHits + totalMisses;
    const hitRate = overall > 0 ? ((totalHits / overall) * 100).toFixed(1) : '—';

    return (
        <div
            style={{
                position: 'fixed',
                top: '80px',
                left: '50%',
                transform: 'translateX(-50%)',
                width: '80%',
                maxWidth: '800px',
                height: '600px',
                background: 'var(--card-bg)',
                border: '1px solid var(--border-color)',
                borderRadius: '12px',
                boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
                zIndex: 2000,
                padding: '20px',
                display: 'flex',
                flexDirection: 'column'
            }}
        >
            <div
                style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: '15px',
                    flexWrap: 'wrap',
                    gap: '8px'
                }}
            >
                <h2 style={{ margin: 0, fontSize: '1.2rem' }}>🐞 AI Debug & Telemetry</h2>
                <button
                    type="button"
                    onClick={onClose}
                    style={{ background: 'transparent', border: 'none', fontSize: '1.2rem', cursor: 'pointer' }}
                >
                    ✕
                </button>
            </div>
            <div
                style={{
                    flex: 1,
                    background: '#1e1e1e',
                    color: '#a9b7c6',
                    fontFamily: 'monospace',
                    padding: '15px',
                    borderRadius: '8px',
                    overflowY: 'auto',
                    fontSize: '0.85rem'
                }}
            >
                {/* Per-step log (cache hit/miss + previews) */}
                {entries.length === 0 ? (
                    <>
                        <p style={{ color: '#808080' }}>// Debug logs for the last chat request</p>
                        <p style={{ color: '#6a8759' }}>
                            Send a message to see: user question, cache hit/miss, and LLM or cache response per step.
                        </p>
                    </>
                ) : (
                    entries.map((entry, i) => (
                        <div
                            key={i}
                            style={{
                                marginBottom: '12px',
                                borderLeft: `3px solid ${
                                    entry.source === 'user' ? '#569cd6' : entry.source === 'cache' ? '#4ec9b0' : '#dcdcaa'
                                }`,
                                paddingLeft: '10px'
                            }}
                        >
                            <div style={{ color: '#808080', marginBottom: '4px' }}>
                                [{i + 1}] <strong style={{ color: '#9cdcfe' }}>{entry.step}</strong>
                                <span
                                    style={{
                                        marginLeft: '8px',
                                        color:
                                            entry.source === 'cache'
                                                ? '#4ec9b0'
                                                : entry.source === 'llm'
                                                  ? '#dcdcaa'
                                                  : '#569cd6'
                                    }}
                                >
                                    {' '}
                                    ← {entry.source}
                                </span>
                            </div>
                            {entry.input_preview && (
                                <div
                                    style={{
                                        color: '#ce9178',
                                        marginBottom: '4px',
                                        whiteSpace: 'pre-wrap',
                                        wordBreak: 'break-word'
                                    }}
                                >
                                    in: {entry.input_preview}
                                </div>
                            )}
                            {entry.output_preview && (
                                <div style={{ color: '#9cdcfe', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                                    out: {entry.output_preview}
                                </div>
                            )}
                        </div>
                    ))
                )}

                {/* §4 telemetry: per-step trace (latency + tokens) for this query */}
                <div style={sectionTitle}>Per-step trace (latency / tokens)</div>
                {trace.length === 0 ? (
                    <p style={{ color: '#6a8759', margin: 0 }}>No persisted trace for the latest query.</p>
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                            <thead>
                                <tr>
                                    <th style={th}>step</th>
                                    <th style={th}>source</th>
                                    <th style={th}>model</th>
                                    <th style={{ ...th, textAlign: 'right' }}>latency</th>
                                    <th style={{ ...th, textAlign: 'right' }}>prompt tok</th>
                                    <th style={{ ...th, textAlign: 'right' }}>compl tok</th>
                                </tr>
                            </thead>
                            <tbody>
                                {trace.map((t, i) => (
                                    <tr key={i}>
                                        <td style={{ ...td, color: '#9cdcfe' }}>{t.step}</td>
                                        <td style={{ ...td, color: t.source === 'cache' ? '#4ec9b0' : '#dcdcaa' }}>{t.source}</td>
                                        <td style={td}>{t.model ?? '—'}</td>
                                        <td style={{ ...td, textAlign: 'right' }}>{t.latency_ms == null ? '—' : `${t.latency_ms} ms`}</td>
                                        <td style={{ ...td, textAlign: 'right' }}>{t.prompt_tokens || 0}</td>
                                        <td style={{ ...td, textAlign: 'right' }}>{t.completion_tokens || 0}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}

                {/* §4 telemetry: global cache effectiveness */}
                <div style={sectionTitle}>Cache hit-rate (all-time)</div>
                {counters.length === 0 ? (
                    <p style={{ color: '#6a8759', margin: 0 }}>No cache activity recorded yet.</p>
                ) : (
                    <>
                        <p style={{ margin: '0 0 8px', color: '#4ec9b0' }}>
                            Overall: {totalHits} hits / {totalMisses} misses — {hitRate}% hit rate
                        </p>
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ borderCollapse: 'collapse', width: '100%' }}>
                                <thead>
                                    <tr>
                                        <th style={th}>call_id</th>
                                        <th style={{ ...th, textAlign: 'right' }}>hits</th>
                                        <th style={{ ...th, textAlign: 'right' }}>misses</th>
                                        <th style={{ ...th, textAlign: 'right' }}>hit rate</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {counters.map((c, i) => {
                                        const tot = c.hits + c.misses;
                                        const rate = tot > 0 ? `${((c.hits / tot) * 100).toFixed(0)}%` : '—';
                                        return (
                                            <tr key={i}>
                                                <td style={{ ...td, color: '#9cdcfe' }}>{c.call_id}</td>
                                                <td style={{ ...td, textAlign: 'right' }}>{c.hits}</td>
                                                <td style={{ ...td, textAlign: 'right' }}>{c.misses}</td>
                                                <td style={{ ...td, textAlign: 'right' }}>{rate}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
