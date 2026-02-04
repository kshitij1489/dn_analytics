import type { DebugLogEntry } from '../../../api';

interface DebugOverlayProps {
    entries: DebugLogEntry[];
    onClose: () => void;
}

export function DebugOverlay({ entries, onClose }: DebugOverlayProps) {
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
                <h2 style={{ margin: 0, fontSize: '1.2rem' }}>🐞 AI Debug Logs</h2>
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
            </div>
        </div>
    );
}
