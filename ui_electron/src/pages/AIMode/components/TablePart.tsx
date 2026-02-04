import { ClientSideDataTable } from '../../../components/ClientSideDataTable';

export interface TablePartProps {
    rows: Record<string, unknown>[];
    explanation?: string;
}

const columnsFromRows = (rows: Record<string, unknown>[]) =>
    rows.length > 0 ? Object.keys(rows[0]) : [];

export function TablePart({ rows, explanation }: TablePartProps) {
    const columns = columnsFromRows(rows);
    const isSingleValue = rows.length === 1 && columns.length <= 2;
    const isSmallTable = rows.length <= 10;

    if (isSingleValue) {
        const value = rows[0][columns[columns.length - 1]];
        const label = columns.length === 2 ? rows[0][columns[0]] : columns[0];
        return (
            <div>
                {explanation && <div style={{ marginBottom: '10px' }}>{explanation}</div>}
                <div style={{ fontSize: '2rem', fontWeight: 'bold', color: 'var(--accent-color)', padding: '10px 0' }}>
                    {typeof value === 'number' ? value.toLocaleString() : String(value ?? '')}
                </div>
                {columns.length === 2 && (
                    <div style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>{String(label ?? '')}</div>
                )}
            </div>
        );
    }

    if (isSmallTable) {
        return (
            <div>
                {explanation && <div style={{ marginBottom: '15px' }}>{explanation}</div>}
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
                    <thead>
                        <tr>
                            {columns.map(col => (
                                <th
                                    key={col}
                                    style={{
                                        textAlign: 'left',
                                        padding: '8px 12px',
                                        borderBottom: '2px solid var(--border-color)',
                                        fontWeight: 600
                                    }}
                                >
                                    {col.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, i) => (
                            <tr key={i}>
                                {columns.map(col => (
                                    <td
                                        key={col}
                                        style={{
                                            padding: '8px 12px',
                                            borderBottom: '1px solid var(--border-color)'
                                        }}
                                    >
                                        {typeof row[col] === 'number'
                                            ? Number(row[col]).toLocaleString()
                                            : String(row[col] ?? '—')}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>
                    {rows.length} row{rows.length !== 1 ? 's' : ''}
                </div>
            </div>
        );
    }

    return (
        <div>
            {explanation && <div style={{ marginBottom: '15px' }}>{explanation}</div>}
            <ClientSideDataTable data={rows} columns={columns} filenamePrefix="ai_data" />
        </div>
    );
}
