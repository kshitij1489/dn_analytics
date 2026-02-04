import type { LlmCacheEntry } from '../../../api';
import { ClientSideDataTable, type ColumnRenderFn } from '../../../components';
import { exportToCSV } from '../../../utils';
import { actionButtonStyle } from '../styles';

const COLUMNS = ['is_incorrect', 'call_id', 'value_preview', 'last_used_at', 'created_at', 'key_hash'] as const;

interface TelemetryViewProps {
    entries: LlmCacheEntry[];
    loading: boolean;
    onRefresh: () => void;
    onMarkIncorrect: (keyHash: string, isIncorrect: boolean) => void | Promise<void>;
}

export function TelemetryView({ entries, loading, onRefresh, onMarkIncorrect }: TelemetryViewProps) {
    const columnRender: Record<string, ColumnRenderFn> = {
        key_hash: (val, row) => (
            <span style={{ fontFamily: 'monospace', fontSize: '0.8rem' }} title={row.key_hash}>
                {String(val ?? '').slice(0, 12)}…
            </span>
        ),
        value_preview: (val, row) => (
            <span
                style={{
                    maxWidth: '280px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    fontFamily: 'monospace',
                    fontSize: '0.8rem'
                }}
                title={row.value_preview}
            >
                {String(val ?? '')}
            </span>
        ),
        last_used_at: (val) => (val == null ? '—' : String(val)),
        is_incorrect: (val, row) => (
            <input
                type="checkbox"
                checked={!!val}
                onChange={() => void Promise.resolve(onMarkIncorrect(row.key_hash, !val))}
                title="Mark as incorrect (human feedback for cloud learning)"
                style={{ cursor: 'pointer' }}
            />
        )
    };

    const handleExportCSV = () => {
        const timestamp = new Date().toISOString().split('T')[0];
        exportToCSV(entries as Record<string, any>[], `llm_cache_entries_${timestamp}`, [...COLUMNS]);
    };

    return (
        <div
            style={{
                display: 'flex',
                flexDirection: 'column',
                padding: '1px 16px 16px 16px',
                width: '100%'
            }}
        >
            <div
                style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: '6px',
                    flexWrap: 'wrap',
                    gap: '4px'
                }}
            >
                <h3 style={{ margin: 0, fontSize: '1rem', color: 'var(--text-secondary)' }}>
                    LLM cache entries
                </h3>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <button
                        type="button"
                        onClick={onRefresh}
                        disabled={loading}
                        style={{
                            ...actionButtonStyle,
                            background: 'white',
                            color: 'var(--text-color)',
                            borderColor: 'var(--border-color)',
                            cursor: loading ? 'not-allowed' : 'pointer'
                        }}
                    >
                        ↻ Refresh
                    </button>
                    <button
                        type="button"
                        onClick={handleExportCSV}
                        disabled={entries.length === 0}
                        title="Export to CSV"
                        style={{
                            ...actionButtonStyle,
                            background: '#3B82F6',
                            borderColor: 'transparent',
                            padding: '7px 14px',
                            fontSize: '13px',
                            cursor: entries.length === 0 ? 'not-allowed' : 'pointer',
                            opacity: entries.length === 0 ? 0.6 : 1
                        }}
                    >
                        📥 Export CSV
                    </button>
                </div>
            </div>
            <div>
                {loading && entries.length === 0 ? (
                    <div
                        style={{
                            padding: '24px',
                            textAlign: 'center',
                            color: 'var(--text-secondary)'
                        }}
                    >
                        Loading cache entries…
                    </div>
                ) : entries.length === 0 ? (
                    <div
                        style={{
                            padding: '24px',
                            textAlign: 'center',
                            color: 'var(--text-secondary)'
                        }}
                    >
                        No LLM cache entries. Send a message in Assistant mode to populate the cache.
                    </div>
                ) : (
                    <ClientSideDataTable
                        data={entries as Record<string, any>[]}
                        columns={[...COLUMNS]}
                        defaultPageSize={25}
                        defaultSortKey="last_used_at"
                        defaultSortDirection="desc"
                        filenamePrefix="llm_cache_entries"
                        columnRender={columnRender}
                        columnLabels={{ last_used_at: 'Most recent use' }}
                        hideExportButton
                        marginTop={0}
                    />
                )}
            </div>
        </div>
    );
}
