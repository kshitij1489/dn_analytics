/**
 * ClientSideDataTable Component
 * 
 * A reusable table component for displaying in-memory data with:
 * - Client-side pagination
 * - Client-side sorting
 * - Resizable container
 * - CSV export
 * - Optional custom cell renderers per column
 * 
 * Used by SQL Console, Telemetry, and other places where all data is loaded at once.
 */

import type React from 'react';
import { useState, useMemo } from 'react';
import { ResizableTableWrapper } from './ResizableTableWrapper';
import { exportToCSV } from '../utils/exportToCSV';
import { formatColumnHeader } from '../utils';

export type ColumnRenderFn = (value: any, row: Record<string, any>) => React.ReactNode;

interface ClientSideDataTableProps {
    data: Record<string, any>[];
    columns: string[];
    defaultPageSize?: number;
    defaultSortKey?: string;
    defaultSortDirection?: 'asc' | 'desc';
    filenamePrefix?: string;
    /** Optional per-column custom cell renderer. If provided for a column, it is used instead of default rendering. */
    columnRender?: Record<string, ColumnRenderFn>;
    /** Optional display labels for column headers. If provided for a column, used instead of formatColumnHeader(key). */
    columnLabels?: Record<string, string>;
    /** When true, the Export CSV button above the table is hidden (e.g. when the parent renders its own toolbar). */
    hideExportButton?: boolean;
    /** Override top margin of the table container (default 20px). Use 0 or a small value for tighter layout. */
    marginTop?: number;
}

export function ClientSideDataTable({
    data,
    columns,
    defaultPageSize = 50,
    defaultSortKey,
    defaultSortDirection = 'desc',
    filenamePrefix = 'export',
    columnRender,
    columnLabels,
    hideExportButton = false,
    marginTop = 20
}: ClientSideDataTableProps) {
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(defaultPageSize);
    const [sortKey, setSortKey] = useState<string | null>(defaultSortKey || (columns.length > 0 ? columns[0] : null));
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>(defaultSortDirection);

    // Sort data
    const sortedData = useMemo(() => {
        if (!sortKey) return data;
        return [...data].sort((a, b) => {
            const aVal = a[sortKey];
            const bVal = b[sortKey];

            // Handle nulls
            if (aVal === null || aVal === undefined) return sortDirection === 'asc' ? -1 : 1;
            if (bVal === null || bVal === undefined) return sortDirection === 'asc' ? 1 : -1;

            // Numeric comparison
            if (typeof aVal === 'number' && typeof bVal === 'number') {
                return sortDirection === 'asc' ? aVal - bVal : bVal - aVal;
            }

            // String comparison
            const aStr = String(aVal).toLowerCase();
            const bStr = String(bVal).toLowerCase();
            if (sortDirection === 'asc') {
                return aStr.localeCompare(bStr);
            }
            return bStr.localeCompare(aStr);
        });
    }, [data, sortKey, sortDirection]);

    // Paginate data
    const paginatedData = useMemo(() => {
        const start = (page - 1) * pageSize;
        return sortedData.slice(start, start + pageSize);
    }, [sortedData, page, pageSize]);

    const totalPages = Math.ceil(data.length / pageSize);

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDirection('desc');
        }
        setPage(1);
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return <span style={{ opacity: 0.3 }}> ⇅</span>;
        return <span>{sortDirection === 'asc' ? ' ↑' : ' ↓'}</span>;
    };

    const handleExportCSV = () => {
        const timestamp = new Date().toISOString().split('T')[0];
        exportToCSV(data, `${filenamePrefix}_${timestamp}`, columns);
    };

    if (!data.length || !columns.length) {
        return (
            <div style={{ padding: '20px', textAlign: 'center', color: '#666' }}>
                No data to display.
            </div>
        );
    }

    return (
        <div style={{ marginTop: `${marginTop}px` }}>
            <ResizableTableWrapper onExportCSV={hideExportButton ? undefined : handleExportCSV}>
                <table className="standard-table">
                    <thead>
                        <tr>
                            {columns.map(col => (
                                <th
                                    key={col}
                                    onClick={() => handleSort(col)}
                                    style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}
                                >
                                    {(columnLabels?.[col] ?? formatColumnHeader(col))}{renderSortIcon(col)}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {paginatedData.map((row, i) => (
                            <tr key={i}>
                                {columns.map(col => {
                                    const custom = columnRender?.[col];
                                    const cellContent = custom
                                        ? custom(row[col], row)
                                        : typeof row[col] === 'boolean'
                                            ? (row[col] ? '✅' : '❌')
                                            : row[col] === null || row[col] === undefined
                                                ? <span style={{ color: '#aaa', fontStyle: 'italic' }}>NULL</span>
                                                : String(row[col]).length > 100
                                                    ? String(row[col]).substring(0, 100) + '...'
                                                    : String(row[col]);
                                    return <td key={col}>{cellContent}</td>;
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </ResizableTableWrapper>

            {/* Pagination Controls */}
            <div style={{
                marginTop: '10px',
                display: 'flex',
                gap: '10px',
                alignItems: 'center',
                justifyContent: 'space-between'
            }}>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <select
                        value={pageSize}
                        onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}
                        style={{
                            padding: '5px',
                            background: 'var(--input-bg)',
                            color: 'var(--text-color)',
                            border: '1px solid var(--input-border)',
                            borderRadius: '4px'
                        }}
                    >
                        <option value={20}>20 per page</option>
                        <option value={25}>25 per page</option>
                        <option value={50}>50 per page</option>
                        <option value={100}>100 per page</option>
                        <option value={200}>200 per page</option>
                    </select>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                        Showing {(page - 1) * pageSize + 1} - {Math.min(page * pageSize, data.length)} of {data.length}
                    </span>
                </div>
                <div>
                    <button
                        disabled={page <= 1}
                        onClick={() => setPage(p => p - 1)}
                        style={{
                            marginRight: '5px',
                            padding: '5px 10px',
                            cursor: page <= 1 ? 'not-allowed' : 'pointer',
                            opacity: page <= 1 ? 0.5 : 1
                        }}
                    >
                        &lt; Prev
                    </button>
                    <span>Page {page} of {totalPages || 1}</span>
                    <button
                        disabled={page >= totalPages}
                        onClick={() => setPage(p => p + 1)}
                        style={{
                            marginLeft: '5px',
                            padding: '5px 10px',
                            cursor: page >= totalPages ? 'not-allowed' : 'pointer',
                            opacity: page >= totalPages ? 0.5 : 1
                        }}
                    >
                        Next &gt;
                    </button>
                </div>
            </div>
        </div>
    );
}
