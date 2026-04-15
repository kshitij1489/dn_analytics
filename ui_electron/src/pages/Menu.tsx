import { useState, useEffect, useRef, type CSSProperties } from 'react';
import { endpoints } from '../api';
import { CollapsibleCard, ErrorPopup, TabButton } from '../components';
import type { PopupMessage } from '../components';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';
import { formatColumnHeader } from '../utils';

// --- Shared Components ---

const Card = ({ children, title }: { children: React.ReactNode, title: string }) => (
    <div style={{ background: 'var(--card-bg)', padding: '20px', borderRadius: '12px', marginBottom: '20px', border: '1px solid var(--border-color)', boxShadow: 'var(--shadow)' }}>
        <h3 style={{ marginTop: 0, marginBottom: '15px', color: 'var(--accent-color)' }}>{title}</h3>
        {children}
    </div>
);

interface MenuLookupItem {
    menu_item_id: string;
    name: string;
    type: string;
    is_verified: boolean;
}

interface VariantOption {
    variant_id: string;
    name: string;
}

interface ResolutionItem {
    menu_item_id: string;
    name: string;
    type: string;
    created_at: string;
    source_variant_id: string;
    source_variant_name: string;
    display_name?: string | null;
    sample_order_name?: string | null;
    unresolved_mapping_rows?: number;
    order_item_rows?: number;
    order_item_qty?: number;
    suggestion_id?: string | null;
    suggestion_name?: string | null;
    suggestion_type?: string | null;
    suggested_variant_id?: string | null;
    suggested_variant_name?: string | null;
}

interface MergeHistoryEntry {
    merge_id: number;
    source_name: string;
    target_name?: string | null;
    merged_at: string;
    variant_assignments?: MergeHistoryVariantAssignment[];
}

interface MergeHistoryVariantAssignment {
    source_variant_id: string;
    source_variant_name: string;
    target_variant_id: string;
    target_variant_name: string;
}

interface MergePreview {
    source: {
        menu_item_id: string;
        name: string;
        type: string;
        is_verified: boolean;
    };
    target: {
        menu_item_id: string;
        name: string;
        type: string;
        is_verified: boolean;
    };
    stats: {
        order_items_relinked: number;
        addon_items_relinked: number;
        mappings_updated: number;
        source_total_sold: number;
        source_total_revenue: number;
    };
    source_variants: MergePreviewVariant[];
    target_variants: MergePreviewVariant[];
}

interface MergePreviewVariant {
    variant_id: string;
    variant_name: string;
    order_item_rows: number;
    order_item_qty: number;
    addon_rows: number;
    addon_qty: number;
    mapping_rows: number;
    total_rows: number;
}

interface MatrixRow {
    name: string;
    type: string;
    variant_name: string;
    price: number;
    is_active: boolean;
    addon_eligible: boolean;
    delivery_eligible: boolean;
    menu_item_id: string;
    variant_id: string;
    mapping_count: number;
}

const getApiErrorMessage = (error: unknown): string => {
    const err = error as { response?: { data?: { detail?: string } }; message?: string };
    return err.response?.data?.detail || err.message || 'Something went wrong';
};

const renderVariantAssignments = (assignments?: MergeHistoryVariantAssignment[], compact = false) => {
    if (!assignments || assignments.length === 0) return null;

    return (
        <div style={{ marginTop: '6px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {assignments.map(assignment => (
                <div
                    key={`${assignment.source_variant_id}-${assignment.target_variant_id}`}
                    style={{
                        fontSize: compact ? '0.75em' : '0.82em',
                        color: 'var(--text-secondary)',
                    }}
                >
                    Variant: <span style={{ color: '#F59E0B' }}>{assignment.source_variant_name}</span>
                    {' → '}
                    <span style={{ color: '#60A5FA' }}>{assignment.target_variant_name}</span>
                </div>
            ))}
        </div>
    );
};

const getMergeSuggestionLabel = (item: ResolutionItem) => {
    if (!item.suggestion_name) return 'Merge with Suggested Item';
    if (!item.suggested_variant_name) return `Merge with ${item.suggestion_name}`;
    return `Merge with ${item.suggestion_name} (${item.suggested_variant_name})`;
};

const formatVariantDisplayName = (variantName?: string | null) => (
    variantName ? formatColumnHeader(variantName) : 'Unknown Variant'
);

const formatResolutionTitle = (item: ResolutionItem) => (
    `${item.name} (${formatVariantDisplayName(item.source_variant_name)})`
);

const formatDateInputValue = (date: Date) => {
    const localDate = new Date(date.getTime() - (date.getTimezoneOffset() * 60 * 1000));
    return localDate.toISOString().slice(0, 10);
};

const dateRangeLabelStyle: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '12px',
    color: 'var(--text-secondary)',
};

const dateRangeInputStyle: CSSProperties = {
    padding: '8px',
    height: '38px',
    boxSizing: 'border-box',
    borderRadius: '4px',
    border: '1px solid var(--border-color)',
    background: 'var(--input-bg)',
    color: 'var(--text-color)',
    fontSize: '12px',
};

// --- CSV Export Utility ---
function exportToCSV(data: any[], filename: string, headers?: string[]): boolean {
    if (!data || data.length === 0) {
        return false;
    }
    const csvHeaders = headers || Object.keys(data[0]);
    const csvRows = [];
    csvRows.push(csvHeaders.join(','));
    for (const row of data) {
        const values = csvHeaders.map(header => {
            const value = row[header];
            if (value == null) return '';
            const stringValue = String(value);
            if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
                return `"${stringValue.replace(/"/g, '""')}"`;
            }
            return stringValue;
        });
        csvRows.push(values.join(','));
    }
    const csvContent = csvRows.join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `${filename}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    return true;
}

// --- Resizable Table Wrapper ---
// Resizable Table Wrapper
function ResizableTableWrapper({
    children,
    headerContent,
    onExportCSV,
    defaultHeight = 600
}: {
    children: React.ReactNode;
    headerContent?: React.ReactNode;
    onExportCSV?: () => void;
    defaultHeight?: number;
}) {
    const [width, setWidth] = useState(1000);
    const [height, setHeight] = useState(defaultHeight);
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (containerRef.current) {
            setWidth(containerRef.current.offsetWidth);
        }
    }, []);

    const onResize = (_event: any, { size }: any) => {
        setHeight(size.height);
        setWidth(size.width);
    };

    return (
        <div ref={containerRef} style={{ width: '100%', marginBottom: '20px' }}>
            <div style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'flex-end',
                gap: '8px',
                marginBottom: '10px',
            }}>
                {headerContent}
                {onExportCSV && (
                    <button
                        onClick={onExportCSV}
                        title="Export to CSV"
                        style={{
                            background: '#3B82F6', // Blue
                            border: 'none',
                            borderRadius: '8px', // Curved edges
                            padding: '8px 16px',
                            color: 'white',
                            cursor: 'pointer',
                            fontSize: '13px',
                            fontWeight: 'bold'
                        }}
                    >
                        📥 Export CSV
                    </button>
                )}
            </div>

            <Resizable
                height={height}
                width={width}
                onResize={onResize}
                resizeHandles={['s', 'e', 'se']}
                minConstraints={[400, 300]}
                maxConstraints={[2400, 1200]}
                handle={(handleAxis, ref) => (
                    <div
                        ref={ref}
                        className={`react-resizable-handle react-resizable-handle-${handleAxis}`}
                        style={{
                            position: 'absolute',
                            userSelect: 'none',
                            width: '20px',
                            height: '20px',
                            bottom: 0,
                            right: 0,
                            cursor: 'se-resize',
                            zIndex: 10,
                            // Visual indication of resize handle
                            background: handleAxis === 'se' ? 'linear-gradient(135deg, transparent 50%, var(--accent-color) 50%)' : 'transparent',
                            borderRadius: '0 0 4px 0'
                        }}
                    />
                )}
            >
                <div style={{
                    width: width + 'px',
                    height: height + 'px',
                    position: 'relative',
                    border: '1px solid var(--border-color)',
                    borderRadius: '8px',
                    background: 'var(--card-bg)', // Ensure match with theme
                    boxShadow: 'var(--shadow)',
                    display: 'flex',   // Ensure inner takes full space
                    flexDirection: 'column'
                }}>
                    <div style={{
                        flex: 1,
                        overflow: 'auto',
                        width: '100%',
                        height: '100%',
                        paddingBottom: '10px' // Slight padding for content
                    }}>
                        {children}
                    </div>
                </div>
            </Resizable>
        </div>
    );
}

const SUMMARY_PERIOD_KEYS = [
    'day_1',
    'day_2',
    'day_3',
    'day_5',
    'day_7',
    'day_14',
    'month_1',
    'month_2',
    'lifetime',
] as const;

const SUMMARY_PERIOD_LABELS: Record<(typeof SUMMARY_PERIOD_KEYS)[number], string> = {
    day_1: '1-day',
    day_2: '2-day',
    day_3: '3-day',
    day_5: '5-day',
    day_7: '7-day',
    day_14: '14-day',
    month_1: '1-Month',
    month_2: '2-Month',
    lifetime: 'LifeTime',
};

const SEARCH_IDLE_DELAY_MS = 1500;

function formatSummaryNumber(value: unknown, volume: boolean): string {
    const n = typeof value === 'number' ? value : Number(value);
    if (!Number.isFinite(n)) return '—';
    if (volume) {
        if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
        return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
    }
    return Math.round(n).toLocaleString();
}

// --- Summary Tab (rolling windows) ---

function SummaryTab({ lastDbSync }: { lastDbSync?: number }) {
    const localToday = formatDateInputValue(new Date());
    const [subMode, setSubMode] = useState<'volume' | 'quantity'>('volume');
    const [useBackendBusinessDate, setUseBackendBusinessDate] = useState(true);
    const [asOfDate, setAsOfDate] = useState('');
    const [pickerMaxDate, setPickerMaxDate] = useState(localToday);
    const [searchInput, setSearchInput] = useState('');
    const [search, setSearch] = useState('');
    const [tableData, setTableData] = useState<Record<string, unknown>[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [loading, setLoading] = useState(false);
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const [sortKey, setSortKey] = useState<(typeof SUMMARY_PERIOD_KEYS)[number]>('lifetime');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
    const requestAsOfDate = useBackendBusinessDate ? '' : asOfDate;
    const trimmedSearchInput = searchInput.trim();

    useEffect(() => {
        if (trimmedSearchInput === search) return;

        const timeoutId = window.setTimeout(() => {
            setSearch(trimmedSearchInput);
            setPage(1);
        }, SEARCH_IDLE_DELAY_MS);

        return () => window.clearTimeout(timeoutId);
    }, [trimmedSearchInput, search]);

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            try {
                const res = await endpoints.menu.summary({
                    mode: subMode,
                    as_of_date: requestAsOfDate || undefined,
                    page,
                    page_size: pageSize,
                    name_search: search.trim() || undefined,
                    sort_by: sortKey,
                    sort_desc: sortDirection === 'desc',
                });
                setTableData(res.data.data);
                setTotal(res.data.total);
                if (typeof res.data.as_of_date === 'string' && res.data.as_of_date) {
                    setPickerMaxDate(prev => (prev === res.data.as_of_date ? prev : res.data.as_of_date));
                    if (useBackendBusinessDate) {
                        setAsOfDate(prev => (prev === res.data.as_of_date ? prev : res.data.as_of_date));
                    }
                }
            } catch (error) {
                setPopup({ type: 'error', message: getApiErrorMessage(error) });
            } finally {
                setLoading(false);
            }
        };
        void load();
    }, [subMode, requestAsOfDate, page, pageSize, search, sortKey, sortDirection, lastDbSync, useBackendBusinessDate]);

    const handleSummarySort = (key: (typeof SUMMARY_PERIOD_KEYS)[number]) => {
        if (sortKey === key) {
            setSortDirection(prev => (prev === 'asc' ? 'desc' : 'asc'));
        } else {
            setSortKey(key);
            setSortDirection('desc');
        }
        setPage(1);
    };

    const renderSummarySortIcon = (key: (typeof SUMMARY_PERIOD_KEYS)[number]) => {
        if (sortKey !== key) return <span style={{ opacity: 0.3 }}> ⇅</span>;
        return <span>{sortDirection === 'asc' ? ' ↑' : ' ↓'}</span>;
    };

    const exportRows = () => {
        const headers =
            subMode === 'volume'
                ? ['menu_item_id', 'name', 'unit', ...SUMMARY_PERIOD_KEYS]
                : ['menu_item_id', 'name', ...SUMMARY_PERIOD_KEYS];
        exportToCSV(tableData, `menu_summary_${subMode}`, [...headers]);
    };

    return (
        <div>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <p style={{ marginTop: 0, marginBottom: '16px', color: 'var(--text-secondary)', maxWidth: '900px' }}>
                {subMode === 'quantity' ? (
                    <>
                        Quantity uses the same line dedupe rules as Menu Items: each order line and add-on line counts once;
                        the cell is the sum of <strong>quantity</strong> (not distinct orders).
                    </>
                ) : (
                    <>
                        Volume is <strong>variant value × quantity</strong> for each line, grouped by normalized unit (ML, GMS,
                        PIECES, or other/UNKNOWN). The same menu item appearing on multiple rows with different units usually
                        indicates variant mapping noise to clean up in Variants / Resolutions.
                    </>
                )}
            </p>
            <div
                className="segmented-control"
                style={{
                    width: 'fit-content',
                    maxWidth: '100%',
                    flexWrap: 'wrap',
                    marginBottom: '20px',
                }}
            >
                {(
                    [
                        { id: 'volume' as const, label: 'Volume' },
                        { id: 'quantity' as const, label: 'Quantity' },
                    ]
                ).map((t) => (
                    <TabButton
                        key={t.id}
                        active={subMode === t.id}
                        onClick={() => {
                            setSubMode(t.id);
                            setPage(1);
                        }}
                        variant="segmented"
                    >
                        {t.label}
                    </TabButton>
                ))}
            </div>
            {loading ? (
                <div>Loading...</div>
            ) : (
                <ResizableTableWrapper
                    defaultHeight={560}
                    headerContent={(
                        <div
                            style={{
                                width: '100%',
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                gap: '12px',
                                flexWrap: 'wrap',
                            }}
                        >
                            <input
                                placeholder="Search name..."
                                value={searchInput}
                                onChange={e => setSearchInput(e.target.value)}
                                style={{
                                    padding: '8px',
                                    width: '280px',
                                    background: 'var(--input-bg)',
                                    color: 'var(--text-color)',
                                    border: '1px solid var(--border-color)',
                                    borderRadius: '4px',
                                }}
                            />
                            <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
                                <label style={dateRangeLabelStyle}>
                                    Through (business date):
                                    <input
                                        type="date"
                                        value={asOfDate}
                                        max={pickerMaxDate || localToday}
                                        onChange={e => {
                                            setUseBackendBusinessDate(false);
                                            setAsOfDate(e.target.value);
                                            setPage(1);
                                        }}
                                        style={dateRangeInputStyle}
                                    />
                                </label>
                                <button
                                    type="button"
                                    onClick={() => {
                                        setUseBackendBusinessDate(true);
                                        setAsOfDate(pickerMaxDate || localToday);
                                        setPage(1);
                                    }}
                                    disabled={useBackendBusinessDate}
                                    style={{
                                        padding: '8px 12px',
                                        borderRadius: '8px',
                                        border: '1px solid var(--border-color)',
                                        background: 'var(--input-bg)',
                                        color: 'var(--text-color)',
                                        cursor: useBackendBusinessDate ? 'default' : 'pointer',
                                        fontSize: '12px',
                                        opacity: useBackendBusinessDate ? 0.65 : 1,
                                    }}
                                >
                                    Use current
                                </button>
                            </div>
                        </div>
                    )}
                    onExportCSV={exportRows}
                >
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th style={{ minWidth: '200px' }}>Menu Item</th>
                                {subMode === 'volume' && (
                                    <th style={{ minWidth: '88px' }}>Unit</th>
                                )}
                                {SUMMARY_PERIOD_KEYS.map(k => (
                                    <th
                                        key={k}
                                        style={{ textAlign: 'right', whiteSpace: 'nowrap' }}
                                        onClick={() => handleSummarySort(k)}
                                    >
                                        {SUMMARY_PERIOD_LABELS[k]}
                                        {renderSummarySortIcon(k)}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {tableData.map((row, i) => (
                                <tr key={i}>
                                    <td>{String(row.name ?? '')}</td>
                                    {subMode === 'volume' && (
                                        <td style={{ fontWeight: 500 }}>{String(row.unit ?? '')}</td>
                                    )}
                                    {SUMMARY_PERIOD_KEYS.map(k => (
                                        <td key={k} style={{ textAlign: 'right' }}>
                                            {formatSummaryNumber(row[k], subMode === 'volume')}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            )}
            <div style={{ marginTop: '10px', display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <select
                        value={pageSize}
                        onChange={e => {
                            setPageSize(Number(e.target.value));
                            setPage(1);
                        }}
                        style={{
                            padding: '5px',
                            background: 'var(--input-bg)',
                            color: 'var(--text-color)',
                            border: '1px solid var(--border-color)',
                            borderRadius: '4px',
                        }}
                    >
                        <option value={20}>20 per page</option>
                        <option value={25}>25 per page</option>
                        <option value={50}>50 per page</option>
                        <option value={100}>100 per page</option>
                        <option value={200}>200 per page</option>
                    </select>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                        Showing {(page - 1) * pageSize + 1} - {Math.min(page * pageSize, total)} of {total}
                    </span>
                </div>
                <div>
                    <button
                        type="button"
                        disabled={page <= 1}
                        onClick={() => setPage(p => p - 1)}
                        style={{
                            marginRight: '5px',
                            padding: '5px 10px',
                            cursor: page <= 1 ? 'not-allowed' : 'pointer',
                        }}
                    >
                        &lt; Prev
                    </button>
                    <span>
                        Page {page} of {Math.max(1, Math.ceil(total / pageSize))}
                    </span>
                    <button
                        type="button"
                        disabled={page >= Math.ceil(total / pageSize)}
                        onClick={() => setPage(p => p + 1)}
                        style={{
                            marginLeft: '5px',
                            padding: '5px 10px',
                            cursor: page >= Math.ceil(total / pageSize) ? 'not-allowed' : 'pointer',
                        }}
                    >
                        Next &gt;
                    </button>
                </div>
            </div>
        </div>
    );
}

// --- Menu Items Tab ---

function MenuItemsTab({ lastDbSync }: { lastDbSync?: number }) {
    const defaultStartDate = '2025-01-01';
    const today = formatDateInputValue(new Date());

    // State for Table
    const [tableData, setTableData] = useState<any[]>([]);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('total_revenue');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
    const [total, setTotal] = useState(0);
    const [loadingTable, setLoadingTable] = useState(false);
    const [searchInput, setSearchInput] = useState('');
    const [search, setSearch] = useState('');
    const [startDate, setStartDate] = useState(defaultStartDate);
    const [endDate, setEndDate] = useState(today);
    const trimmedSearchInput = searchInput.trim();

    useEffect(() => {
        if (trimmedSearchInput === search) return;

        const timeoutId = window.setTimeout(() => {
            setSearch(trimmedSearchInput);
            setPage(1);
        }, SEARCH_IDLE_DELAY_MS);

        return () => window.clearTimeout(timeoutId);
    }, [trimmedSearchInput, search]);

    useEffect(() => {
        loadTable();
    }, [page, search, pageSize, sortKey, sortDirection, startDate, endDate, lastDbSync]);

    const loadTable = async () => {
        setLoadingTable(true);
        try {
            const filters = search ? JSON.stringify({ name: search }) : undefined;
            const res = await endpoints.menu.itemsView({
                page,
                page_size: pageSize,
                sort_by: sortKey,
                sort_desc: sortDirection === 'desc',
                filters,
                start_date: startDate || undefined,
                end_date: endDate || undefined,
            });
            setTableData(res.data.data);
            setTotal(res.data.total);
        } catch (e) { console.error(e); }
        finally { setLoadingTable(false); }
    };

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDirection('desc'); // Default to high-to-low for new metrics usually
        }
        setPage(1); // Reset to page 1 on sort change
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return <span style={{ opacity: 0.3 }}> ⇅</span>;
        return <span>{sortDirection === 'asc' ? ' ↑' : ' ↓'}</span>;
    };

    const handleStartDateChange = (nextStartDate: string) => {
        setStartDate(nextStartDate);
        if (endDate && nextStartDate > endDate) {
            setEndDate(nextStartDate);
        }
        setPage(1);
    };

    const handleEndDateChange = (nextEndDate: string) => {
        setEndDate(nextEndDate);
        if (startDate && nextEndDate < startDate) {
            setStartDate(nextEndDate);
        }
        setPage(1);
    };

    return (
        <div>
            <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>
                Item-level analytics stay here. Use <b style={{ color: 'var(--text-color)' }}>Menu Matrix</b> to merge or consolidate specific menu item + variant pairs.
            </p>

            {/* Menu Items Table Container */}
            <div style={{ marginTop: '20px' }}>
                {loadingTable ? <div>Loading...</div> : (
                    <ResizableTableWrapper
                        headerContent={(
                            <div style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                                <input
                                    placeholder="Search Name..."
                                    value={searchInput}
                                    onChange={e => setSearchInput(e.target.value)}
                                    style={{ padding: '8px', width: '300px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', borderRadius: '4px' }}
                                />
                                <div style={{ display: 'flex', gap: '12px', alignItems: 'center', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                                    <label style={dateRangeLabelStyle}>
                                        Begin:
                                        <input
                                            type="date"
                                            value={startDate}
                                            min={defaultStartDate}
                                            max={endDate || today}
                                            onChange={(e) => handleStartDateChange(e.target.value)}
                                            style={dateRangeInputStyle}
                                        />
                                    </label>
                                    <label style={dateRangeLabelStyle}>
                                        End:
                                        <input
                                            type="date"
                                            value={endDate}
                                            min={startDate}
                                            max={today}
                                            onChange={(e) => handleEndDateChange(e.target.value)}
                                            style={dateRangeInputStyle}
                                        />
                                    </label>
                                </div>
                            </div>
                        )}
                        onExportCSV={() => exportToCSV(tableData, 'menu_items')}
                    >
                        <table className="standard-table">
                            <thead>
                                <tr>
                                    <th onClick={() => handleSort('menu_item_id')}>Menu Item ID{renderSortIcon('menu_item_id')}</th>
                                    <th onClick={() => handleSort('name')}>Name{renderSortIcon('name')}</th>
                                    <th onClick={() => handleSort('type')}>Type{renderSortIcon('type')}</th>
                                    <th style={{ textAlign: 'right' }} onClick={() => handleSort('total_revenue')}>Total Revenue{renderSortIcon('total_revenue')}</th>
                                    <th style={{ textAlign: 'right' }} onClick={() => handleSort('total_sold')}>Total Sold{renderSortIcon('total_sold')}</th>
                                    <th style={{ textAlign: 'right' }} onClick={() => handleSort('sold_as_item')}>Sold as Item{renderSortIcon('sold_as_item')}</th>
                                    <th style={{ textAlign: 'right' }} onClick={() => handleSort('sold_as_addon')}>Sold as Addon{renderSortIcon('sold_as_addon')}</th>
                                    <th style={{ textAlign: 'center' }} onClick={() => handleSort('is_active')}>Active{renderSortIcon('is_active')}</th>
                                </tr>
                            </thead>
                            <tbody>
                                {tableData.map((row, i) => (
                                    <tr key={i}>
                                        <td style={{ fontSize: '0.8em', color: 'var(--text-secondary)' }}>{row["menu_item_id"]}</td>
                                        <td>{row["name"]}</td>
                                        <td>{row["type"]}</td>
                                        <td style={{ textAlign: 'right' }}>₹{Math.round(row["total_revenue"] || 0).toLocaleString()}</td>
                                        <td style={{ textAlign: 'right' }}>{row["total_sold"]}</td>
                                        <td style={{ textAlign: 'right' }}>{row["sold_as_item"]}</td>
                                        <td style={{ textAlign: 'right' }}>{row["sold_as_addon"]}</td>
                                        <td style={{ textAlign: 'center' }}>{row["is_active"] ? "✅" : "❌"}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </ResizableTableWrapper>
                )}
                <div style={{ marginTop: '10px', display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                        <select
                            value={pageSize}
                            onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}
                            style={{ padding: '5px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', borderRadius: '4px' }}
                        >
                            <option value={20}>20 per page</option>
                            <option value={25}>25 per page</option>
                            <option value={50}>50 per page</option>
                            <option value={100}>100 per page</option>
                            <option value={200}>200 per page</option>
                        </select>
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                            Showing {(page - 1) * pageSize + 1} - {Math.min(page * pageSize, total)} of {total}
                        </span>
                    </div>
                    <div>
                        <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} style={{ marginRight: '5px', padding: '5px 10px', cursor: page <= 1 ? 'not-allowed' : 'pointer' }}>&lt; Prev</button>
                        <span>Page {page} of {Math.ceil(total / pageSize)}</span>
                        <button disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage(p => p + 1)} style={{ marginLeft: '5px', padding: '5px 10px', cursor: page >= Math.ceil(total / pageSize) ? 'not-allowed' : 'pointer' }}>Next &gt;</button>
                    </div>
                </div>
            </div>
        </div>
    );
}

// --- Variants Tab ---

function VariantsTab({ lastDbSync }: { lastDbSync?: number }) {
    const [data, setData] = useState<any[]>([]);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('variant_name');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const res = await endpoints.menu.variantsView({
                page,
                page_size: pageSize,
                sort_by: sortKey,
                sort_desc: sortDirection === 'desc'
            });
            setData(res.data.data);
            setTotal(res.data.total);
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { load(); }, [page, pageSize, sortKey, sortDirection, lastDbSync]);

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDirection('asc');
        }
        setPage(1);
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return <span style={{ opacity: 0.3 }}> ⇅</span>;
        return <span>{sortDirection === 'asc' ? ' ↑' : ' ↓'}</span>;
    };

    const displayColumns = [
        'variant_id', 'variant_name', 'description', 'unit', 'value', 'is_verified', 'created_at', 'updated_at'
    ];

    return (
        <div style={{ marginTop: '20px' }}>
            <h3 style={{ marginTop: 0, marginBottom: '15px', color: 'var(--accent-color)' }}>Variants</h3>
            {loading ? <div>Loading...</div> : (
                <ResizableTableWrapper onExportCSV={() => exportToCSV(data, 'variants', displayColumns)}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                {displayColumns.map(col => (
                                    <th key={col} onClick={() => handleSort(col)}>
                                        {formatColumnHeader(col)}{renderSortIcon(col)}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {data.map((row, i) => (
                                <tr key={i}>
                                    {displayColumns.map(col => (
                                        <td key={col}>
                                            {typeof row[col] === 'boolean' ? (row[col] ? '✅' : '❌') :
                                                (String(row[col] || '').length > 100 ? String(row[col]).substring(0, 100) + '...' : String(row[col] || '-'))}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
            )}

            <div style={{ marginTop: '10px', display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <select
                        value={pageSize}
                        onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}
                        style={{ padding: '5px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--input-border)', borderRadius: '4px' }}
                    >
                        <option value={20}>20 per page</option>
                        <option value={25}>25 per page</option>
                        <option value={50}>50 per page</option>
                        <option value={100}>100 per page</option>
                        <option value={200}>200 per page</option>
                    </select>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                        Showing {(page - 1) * pageSize + 1} - {Math.min(page * pageSize, total)} of {total}
                    </span>
                </div>
                <div>
                    <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} style={{ marginRight: '5px', padding: '5px 10px', cursor: page <= 1 ? 'not-allowed' : 'pointer' }}>&lt; Prev</button>
                    <span>Page {page} of {Math.ceil(total / pageSize)}</span>
                    <button disabled={page >= Math.ceil(total / pageSize)} onClick={() => setPage(p => p + 1)} style={{ marginLeft: '5px', padding: '5px 10px', cursor: page >= Math.ceil(total / pageSize) ? 'not-allowed' : 'pointer' }}>Next &gt;</button>
                </div>
            </div>
        </div>
    );
}

// --- Matrix Tab ---

function MatrixTab({ lastDbSync }: { lastDbSync?: number }) {
    const [items, setItems] = useState<MenuLookupItem[]>([]);
    const [variants, setVariants] = useState<VariantOption[]>([]);
    const [matrixData, setMatrixData] = useState<MatrixRow[]>([]);
    const [mergeHistory, setMergeHistory] = useState<MergeHistoryEntry[]>([]);
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const [sourceMenuItemId, setSourceMenuItemId] = useState('');
    const [sourceVariantId, setSourceVariantId] = useState('');
    const [targetMenuItemId, setTargetMenuItemId] = useState('');
    const [targetVariantId, setTargetVariantId] = useState('');
    const [mergePreview, setMergePreview] = useState<MergePreview | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [merging, setMerging] = useState(false);
    const [undoingMergeId, setUndoingMergeId] = useState<number | null>(null);

    // Client-Side Table State
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('name');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
    const [search, setSearch] = useState('');

    useEffect(() => {
        void refreshData();
    }, [lastDbSync]);

    useEffect(() => {
        if (!sourceMenuItemId || !sourceVariantId || !targetMenuItemId) {
            setMergePreview(null);
            return;
        }

        let cancelled = false;

        const loadPreview = async () => {
            setPreviewLoading(true);
            try {
                const res = await endpoints.menu.mergePreview({
                    source_id: sourceMenuItemId,
                    target_id: targetMenuItemId,
                    source_variant_id: sourceVariantId,
                });
                if (!cancelled) {
                    setMergePreview(res.data);
                }
            } catch (error) {
                if (!cancelled) {
                    setMergePreview(null);
                    setPopup({ type: 'error', message: getApiErrorMessage(error) });
                }
            } finally {
                if (!cancelled) {
                    setPreviewLoading(false);
                }
            }
        };

        void loadPreview();

        return () => {
            cancelled = true;
        };
    }, [sourceMenuItemId, sourceVariantId, targetMenuItemId]);

    const refreshData = async () => {
        try {
            const [itemsRes, variantsRes, matrixRes, historyRes] = await Promise.all([
                endpoints.menu.list(),
                endpoints.menu.variantsList(),
                endpoints.menu.matrix(),
                endpoints.menu.mergeHistory(),
            ]);
            setItems(itemsRes.data);
            setVariants(variantsRes.data);
            setMatrixData(matrixRes.data);
            setMergeHistory(historyRes.data);
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        }
    };

    const getItemVariantOptions = (menuItemId: string) => (
        matrixData
            .filter(row => row.menu_item_id === menuItemId)
            .map(row => ({
                variant_id: row.variant_id,
                variant_name: row.variant_name,
                count: row.mapping_count,
            }))
            .sort((a, b) => a.variant_name.localeCompare(b.variant_name))
    );

    const handlePrefill = (row: MatrixRow, side: 'source' | 'target') => {
        if (side === 'source') {
            setSourceMenuItemId(row.menu_item_id);
            setSourceVariantId(row.variant_id);
            return;
        }
        setTargetMenuItemId(row.menu_item_id);
        setTargetVariantId(row.variant_id);
    };

    const handleMerge = async () => {
        if (!sourceMenuItemId || !sourceVariantId || !targetMenuItemId || !targetVariantId) {
            setPopup({ type: 'error', message: 'Select source item + variant and target item + variant before merging.' });
            return;
        }
        if (sourceMenuItemId === targetMenuItemId && sourceVariantId === targetVariantId) {
            setPopup({ type: 'error', message: 'Source and target pair cannot be identical.' });
            return;
        }

        try {
            setMerging(true);
            const res = await endpoints.menu.resolve({
                source_menu_item_id: sourceMenuItemId,
                source_variant_id: sourceVariantId,
                target_menu_item_id: targetMenuItemId,
                target_variant_id: targetVariantId,
            });
            setPopup({ type: 'success', message: res.data.message || 'Menu item + variant merged successfully.' });
            setSourceMenuItemId('');
            setSourceVariantId('');
            setTargetMenuItemId('');
            setTargetVariantId('');
            setMergePreview(null);
            await refreshData();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setMerging(false);
        }
    };

    const handleUndo = async (mergeId: number) => {
        if (!window.confirm('Undo this merge?')) return;

        setUndoingMergeId(mergeId);
        try {
            await endpoints.menu.undoMerge({ merge_id: mergeId });
            setPopup({ type: 'success', message: 'Merge undone successfully.' });
            await refreshData();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUndoingMergeId(null);
        }
    };

    const matrixBackedMenuItemIds = new Set(matrixData.map(row => row.menu_item_id));
    const sourceSelectableItems = items.filter(item => matrixBackedMenuItemIds.has(item.menu_item_id));
    const sourceVariantOptions = sourceMenuItemId ? getItemVariantOptions(sourceMenuItemId) : [];
    const targetCurrentVariantOptions = targetMenuItemId ? getItemVariantOptions(targetMenuItemId) : [];
    const targetCurrentVariantIds = new Set(targetCurrentVariantOptions.map(variant => variant.variant_id));
    const selectedSourceItem = items.find(item => item.menu_item_id === sourceMenuItemId);
    const selectedTargetItem = items.find(item => item.menu_item_id === targetMenuItemId);
    const selectedSourceVariant = sourceVariantOptions.find(variant => variant.variant_id === sourceVariantId);
    const selectedTargetVariant = variants.find(variant => variant.variant_id === targetVariantId)
        || targetCurrentVariantOptions.find(variant => variant.variant_id === targetVariantId);
    const selectedTargetVariantLabel = selectedTargetVariant
        ? ('name' in selectedTargetVariant ? selectedTargetVariant.name : selectedTargetVariant.variant_name)
        : '';
    const isSameExactPair = (
        Boolean(sourceMenuItemId) &&
        sourceMenuItemId === targetMenuItemId &&
        Boolean(sourceVariantId) &&
        sourceVariantId === targetVariantId
    );
    const normalizedSearch = search.trim().toLowerCase();
    const filteredMatrixData = matrixData.filter(row =>
        row.name.toLowerCase().includes(normalizedSearch)
    );

    // --- Client Side Sorting & Pagination Logic ---
    const getProcessedData = () => {
        const sorted = [...filteredMatrixData];
        if (sortKey) {
            sorted.sort((a, b) => {
                let aVal = a[sortKey as keyof MatrixRow] as string | number | boolean;
                let bVal = b[sortKey as keyof MatrixRow] as string | number | boolean;
                if (typeof aVal === 'string') aVal = aVal.toLowerCase();
                if (typeof bVal === 'string') bVal = bVal.toLowerCase();

                if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
                if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
                return 0;
            });
        }
        const start = (page - 1) * pageSize;
        return sorted.slice(start, start + pageSize);
    };

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortKey(key);
            setSortDirection('asc');
        }
        setPage(1);
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return <span style={{ opacity: 0.3 }}> ⇅</span>;
        return <span>{sortDirection === 'asc' ? ' ↑' : ' ↓'}</span>;
    };

    const displayData = getProcessedData();
    const total = filteredMatrixData.length;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const rangeStart = total === 0 ? 0 : (page - 1) * pageSize + 1;
    const rangeEnd = total === 0 ? 0 : Math.min(page * pageSize, total);

    useEffect(() => {
        if (page > totalPages) {
            setPage(totalPages);
        }
    }, [page, totalPages]);

    useEffect(() => {
        if (sourceMenuItemId && !matrixData.some(row => row.menu_item_id === sourceMenuItemId)) {
            setSourceMenuItemId('');
            setSourceVariantId('');
        }
    }, [sourceMenuItemId, matrixData]);

    return (
        <div>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.45fr) minmax(300px, 1fr)', gap: '20px' }}>
                <CollapsibleCard title="Merge Menu Item + Variant" defaultCollapsed>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Source Menu Item</span>
                                <select
                                    value={sourceMenuItemId}
                                    onChange={e => {
                                        setSourceMenuItemId(e.target.value);
                                        setSourceVariantId('');
                                    }}
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                                >
                                    <option value="">Select source menu item</option>
                                    {sourceSelectableItems.map(item => (
                                        <option key={item.menu_item_id} value={item.menu_item_id}>
                                            {item.name} ({item.type})
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Source Variant</span>
                                <select
                                    value={sourceVariantId}
                                    onChange={e => setSourceVariantId(e.target.value)}
                                    disabled={!sourceMenuItemId}
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                                >
                                    <option value="">Select source variant</option>
                                    {sourceVariantOptions.map(variant => (
                                        <option key={variant.variant_id} value={variant.variant_id}>
                                            {variant.variant_name} ({variant.count} cluster mappings)
                                        </option>
                                    ))}
                                </select>
                            </label>
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Target Menu Item</span>
                                <select
                                    value={targetMenuItemId}
                                    onChange={e => {
                                        setTargetMenuItemId(e.target.value);
                                        setTargetVariantId('');
                                    }}
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                                >
                                    <option value="">Select target menu item</option>
                                    {items.map(item => (
                                        <option key={item.menu_item_id} value={item.menu_item_id}>
                                            {item.name} ({item.type})
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Target Variant</span>
                                <select
                                    value={targetVariantId}
                                    onChange={e => setTargetVariantId(e.target.value)}
                                    disabled={!targetMenuItemId}
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                                >
                                    <option value="">Select target variant</option>
                                    {targetCurrentVariantOptions.length > 0 && (
                                        <optgroup label="Current target variants">
                                            {targetCurrentVariantOptions.map(variant => (
                                                <option key={variant.variant_id} value={variant.variant_id}>
                                                    {variant.variant_name} ({variant.count} current mappings)
                                                </option>
                                            ))}
                                        </optgroup>
                                    )}
                                    <optgroup label="All variants">
                                        {variants
                                            .filter(variant => !targetCurrentVariantIds.has(variant.variant_id))
                                            .map(variant => (
                                                <option key={variant.variant_id} value={variant.variant_id}>
                                                    {variant.name}
                                                </option>
                                            ))}
                                    </optgroup>
                                </select>
                            </label>
                        </div>
                    </div>

                    {(selectedSourceItem || selectedTargetItem) && (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '12px', marginTop: '15px' }}>
                            <div style={{ padding: '12px', borderRadius: '8px', background: 'var(--input-bg)', color: 'var(--text-secondary)' }}>
                                <div style={{ fontSize: '0.8em', fontWeight: 700, color: '#EF4444', marginBottom: '6px' }}>Source</div>
                                {selectedSourceItem ? (
                                    <>
                                        <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>{selectedSourceItem.name}</div>
                                        <div>{selectedSourceItem.type}</div>
                                        <div>{selectedSourceVariant ? selectedSourceVariant.variant_name : 'Select a source variant'}</div>
                                    </>
                                ) : (
                                    <div>Select a source menu item + variant.</div>
                                )}
                            </div>
                            <div style={{ padding: '12px', borderRadius: '8px', background: 'var(--input-bg)', color: 'var(--text-secondary)' }}>
                                <div style={{ fontSize: '0.8em', fontWeight: 700, color: '#10B981', marginBottom: '6px' }}>Target</div>
                                {selectedTargetItem ? (
                                    <>
                                        <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>{selectedTargetItem.name}</div>
                                        <div>{selectedTargetItem.type}</div>
                                        <div>{selectedTargetVariantLabel || 'Select a target variant'}</div>
                                    </>
                                ) : (
                                    <div>Select a target menu item + variant.</div>
                                )}
                            </div>
                        </div>
                    )}

                    {(previewLoading || mergePreview) && (
                        <div style={{ marginTop: '15px', padding: '14px', borderRadius: '10px', background: 'rgba(59, 130, 246, 0.08)', border: '1px solid rgba(59, 130, 246, 0.2)' }}>
                            {previewLoading ? (
                                <div style={{ color: 'var(--text-secondary)' }}>Loading merge preview...</div>
                            ) : mergePreview ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                    <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>
                                        {mergePreview.source.name} ({mergePreview.source.type}) → {mergePreview.target.name} ({mergePreview.target.type})
                                    </div>
                                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                        This will relink {mergePreview.stats.order_items_relinked} order items, {mergePreview.stats.addon_items_relinked} addon rows, and {mergePreview.stats.mappings_updated} cluster mappings for the selected source variant.
                                    </div>
                                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                        Selected source-variant totals: {mergePreview.stats.source_total_sold} sold, ₹{Math.round(mergePreview.stats.source_total_revenue).toLocaleString()} revenue.
                                    </div>
                                    {sourceMenuItemId === targetMenuItemId ? (
                                        <div style={{ color: '#F59E0B', fontSize: '0.9em' }}>
                                            Source and target item are the same. This will consolidate the selected source variant into the selected target variant inside one menu item.
                                        </div>
                                    ) : (
                                        <div style={{ color: '#F59E0B', fontSize: '0.9em' }}>
                                            Other variants on the source item will remain separate unless you merge them too.
                                        </div>
                                    )}
                                </div>
                            ) : null}
                        </div>
                    )}

                    {isSameExactPair && (
                        <div style={{ marginTop: '12px', color: '#F59E0B', fontSize: '0.9em' }}>
                            Source and target pair are identical. Choose a different target variant or target item.
                        </div>
                    )}

                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '15px' }}>
                        <button
                            onClick={() => void handleMerge()}
                            disabled={merging || !sourceMenuItemId || !sourceVariantId || !targetMenuItemId || !targetVariantId || isSameExactPair}
                            style={{
                                background: '#2563EB',
                                color: 'white',
                                border: 'none',
                                padding: '10px 16px',
                                cursor: merging ? 'not-allowed' : 'pointer',
                                opacity: merging || !sourceMenuItemId || !sourceVariantId || !targetMenuItemId || !targetVariantId || isSameExactPair ? 0.7 : 1,
                                borderRadius: '8px',
                                fontWeight: 700,
                            }}
                        >
                            {merging ? 'Merging...' : 'Merge Selected Pair'}
                        </button>
                    </div>
                </CollapsibleCard>

                <CollapsibleCard title="Recent Merge History" defaultCollapsed>
                    {mergeHistory.length === 0 ? (
                        <p style={{ margin: 0, color: 'var(--text-secondary)' }}>No recent merges to undo.</p>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '420px', overflowY: 'auto' }}>
                            {mergeHistory.map(entry => (
                                <div
                                    key={entry.merge_id}
                                    style={{
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center',
                                        gap: '12px',
                                        padding: '10px 0',
                                        borderBottom: '1px solid var(--border-color)',
                                    }}
                                >
                                    <div>
                                        <div style={{ color: 'var(--text-color)' }}>
                                            <span style={{ color: '#EF4444' }}>{entry.source_name}</span>
                                            {' → '}
                                            <span style={{ color: '#10B981' }}>{entry.target_name || 'Deleted target'}</span>
                                        </div>
                                        {renderVariantAssignments(entry.variant_assignments)}
                                        <div style={{ fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                                            {new Date(entry.merged_at).toLocaleString()}
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => handleUndo(entry.merge_id)}
                                        disabled={undoingMergeId === entry.merge_id}
                                        style={{ padding: '8px 14px', background: '#444', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer' }}
                                    >
                                        {undoingMergeId === entry.merge_id ? 'Undoing...' : 'Undo'}
                                    </button>
                                </div>
                            ))}
                        </div>
                    )}
                </CollapsibleCard>
            </div>

            {/* Menu Matrix Table Container */}
            <div style={{ marginTop: '20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', marginBottom: '15px', flexWrap: 'wrap' }}>
                    <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>Menu Matrix ({matrixData.length} unique pairs)</h3>
                    <input
                        placeholder="Search Name..."
                        value={search}
                        onChange={e => { setSearch(e.target.value); setPage(1); }}
                        style={{ padding: '8px', width: '300px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', borderRadius: '4px' }}
                    />
                </div>

                <ResizableTableWrapper onExportCSV={() => exportToCSV(filteredMatrixData, 'menu_matrix')}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th>Action</th>
                                <th onClick={() => handleSort('name')}>Item{renderSortIcon('name')}</th>
                                <th onClick={() => handleSort('type')}>Type{renderSortIcon('type')}</th>
                                <th onClick={() => handleSort('variant_name')}>Variant{renderSortIcon('variant_name')}</th>
                                <th className="text-center" onClick={() => handleSort('mapping_count')}>Mappings{renderSortIcon('mapping_count')}</th>
                                <th className="text-right" onClick={() => handleSort('price')}>Price{renderSortIcon('price')}</th>
                                <th className="text-center" onClick={() => handleSort('is_active')}>Active{renderSortIcon('is_active')}</th>
                                <th className="text-center" onClick={() => handleSort('addon_eligible')}>Addon{renderSortIcon('addon_eligible')}</th>
                                <th className="text-center" onClick={() => handleSort('delivery_eligible')}>Delivery{renderSortIcon('delivery_eligible')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {displayData.map((r, i) => (
                                <tr key={i}>
                                    <td>
                                        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                                            <button
                                                onClick={() => handlePrefill(r, 'source')}
                                                style={{
                                                    background: sourceMenuItemId === r.menu_item_id && sourceVariantId === r.variant_id ? 'rgba(239, 68, 68, 0.15)' : 'transparent',
                                                    color: '#EF4444',
                                                    border: '1px solid rgba(239, 68, 68, 0.45)',
                                                    padding: '4px 8px',
                                                    borderRadius: '6px',
                                                    cursor: 'pointer',
                                                }}
                                            >
                                                Source
                                            </button>
                                            <button
                                                onClick={() => handlePrefill(r, 'target')}
                                                style={{
                                                    background: targetMenuItemId === r.menu_item_id && targetVariantId === r.variant_id ? 'rgba(16, 185, 129, 0.15)' : 'transparent',
                                                    color: '#10B981',
                                                    border: '1px solid rgba(16, 185, 129, 0.45)',
                                                    padding: '4px 8px',
                                                    borderRadius: '6px',
                                                    cursor: 'pointer',
                                                }}
                                            >
                                                Target
                                            </button>
                                        </div>
                                    </td>
                                    <td>{r.name}</td>
                                    <td>{r.type}</td>
                                    <td>{r.variant_name}</td>
                                    <td className="text-center">{r.mapping_count}</td>
                                    <td className="text-right">₹{r.price}</td>
                                    <td className="text-center">{r.is_active ? "✅" : "❌"}</td>
                                    <td className="text-center">{r.addon_eligible ? "✅" : "❌"}</td>
                                    <td className="text-center">{r.delivery_eligible ? "✅" : "❌"}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </ResizableTableWrapper>
                <div style={{ marginTop: '10px', display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                        <select
                            value={pageSize}
                            onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}
                            style={{ padding: '5px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', borderRadius: '4px' }}
                        >
                            <option value={20}>20 per page</option>
                            <option value={25}>25 per page</option>
                            <option value={50}>50 per page</option>
                            <option value={100}>100 per page</option>
                            <option value={200}>200 per page</option>
                        </select>
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                            Showing {rangeStart} - {rangeEnd} of {total}
                        </span>
                    </div>
                    <div>
                        <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} style={{ marginRight: '5px', padding: '5px 10px', cursor: page <= 1 ? 'not-allowed' : 'pointer' }}>&lt; Prev</button>
                        <span>Page {page} of {totalPages}</span>
                        <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} style={{ marginLeft: '5px', padding: '5px 10px', cursor: page >= totalPages ? 'not-allowed' : 'pointer' }}>Next &gt;</button>
                    </div>
                </div>
            </div>
        </div>
    );
}

// --- Resolutions Tab ---

function ResolutionsTab({ lastDbSync }: { lastDbSync?: number }) {
    const [items, setItems] = useState<ResolutionItem[]>([]);
    const [lookupItems, setLookupItems] = useState<MenuLookupItem[]>([]);
    const [variantOptions, setVariantOptions] = useState<VariantOption[]>([]);
    const [mergeHistory, setMergeHistory] = useState<MergeHistoryEntry[]>([]);
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const [loading, setLoading] = useState(true);
    const [undoingMergeId, setUndoingMergeId] = useState<number | null>(null);
    const [modalItem, setModalItem] = useState<ResolutionItem | null>(null);
    const [modalEntryPoint, setModalEntryPoint] = useState<'search' | 'rename'>('search');
    const [targetSearch, setTargetSearch] = useState('');
    const [selectedTargetId, setSelectedTargetId] = useState('');
    const [mergePreview, setMergePreview] = useState<MergePreview | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [renameName, setRenameName] = useState('');
    const [renameType, setRenameType] = useState('');
    const [renameVariantId, setRenameVariantId] = useState('');
    const [mergeSubmitting, setMergeSubmitting] = useState(false);
    const [renameSubmitting, setRenameSubmitting] = useState(false);
    const [selectedTargetVariants, setSelectedTargetVariants] = useState<Record<string, string>>({});
    const [newVariantNames, setNewVariantNames] = useState<Record<string, string>>({});
    const renameSectionRef = useRef<HTMLDivElement>(null);

    const loadItems = async () => {
        const res = await endpoints.menu.unverified();
        setItems(res.data);
        return res.data;
    };

    const loadLookupItems = async () => {
        const res = await endpoints.menu.list();
        setLookupItems(res.data);
        return res.data;
    };

    const loadVariantOptions = async () => {
        const res = await endpoints.menu.variantsList();
        setVariantOptions(res.data);
        return res.data;
    };

    const loadHistory = async () => {
        const res = await endpoints.menu.mergeHistory();
        setMergeHistory(res.data);
        return res.data;
    };

    const removeResolvedItem = (menuItemId: string, sourceVariantId: string) => {
        setItems(prev => prev.filter(item =>
            !(item.menu_item_id === menuItemId && item.source_variant_id === sourceVariantId)
        ));
    };

    const refreshAll = async () => {
        setLoading(true);
        const results = await Promise.allSettled([loadItems(), loadLookupItems(), loadVariantOptions(), loadHistory()]);
        const failedRefreshes = results
            .map((result, index) => ({ result, label: ['items', 'lookup', 'variants', 'history'][index] }))
            .filter(({ result }) => result.status === 'rejected')
            .map(({ label, result }) => `${label}: ${getApiErrorMessage((result as PromiseRejectedResult).reason)}`);

        if (failedRefreshes.length > 0) {
            setPopup({
                type: 'error',
                message: `Some resolution data failed to refresh. ${failedRefreshes.join(' | ')}`,
            });
        }

        setLoading(false);
    };

    useEffect(() => {
        void refreshAll();
    }, [lastDbSync]);

    useEffect(() => {
        if (!modalItem || !selectedTargetId) {
            setMergePreview(null);
            return;
        }

        let cancelled = false;

        const loadPreview = async () => {
            setPreviewLoading(true);
            try {
                const res = await endpoints.menu.mergePreview({
                    source_id: modalItem.menu_item_id,
                    target_id: selectedTargetId,
                    source_variant_id: modalItem.source_variant_id,
                });
                if (!cancelled) {
                    setMergePreview(res.data);
                }
            } catch (error) {
                if (!cancelled) {
                    setMergePreview(null);
                    setPopup({ type: 'error', message: getApiErrorMessage(error) });
                }
            } finally {
                if (!cancelled) {
                    setPreviewLoading(false);
                }
            }
        };

        void loadPreview();

        return () => {
            cancelled = true;
        };
    }, [modalItem, selectedTargetId]);

    useEffect(() => {
        if (!mergePreview) {
            setSelectedTargetVariants({});
            setNewVariantNames({});
            return;
        }

        const nextSelectedTargetVariants: Record<string, string> = {};
        const nextNewVariantNames: Record<string, string> = {};
        const suggestedVariantExists = Boolean(
            modalItem?.suggested_variant_id &&
            variantOptions.some(variant => variant.variant_id === modalItem.suggested_variant_id)
        );

        mergePreview.source_variants.forEach(sourceVariant => {
            const matchingTargetVariant = mergePreview.target_variants.find(
                targetVariant => targetVariant.variant_id === sourceVariant.variant_id ||
                    targetVariant.variant_name === sourceVariant.variant_name
            );

            const shouldUseSuggestedVariant = mergePreview.source_variants.length === 1 && !matchingTargetVariant;
            nextSelectedTargetVariants[sourceVariant.variant_id] = matchingTargetVariant?.variant_id ||
                (shouldUseSuggestedVariant
                    ? (suggestedVariantExists
                        ? (modalItem?.suggested_variant_id || '')
                        : (modalItem?.suggested_variant_name ? '__new__' : ''))
                    : '');
            nextNewVariantNames[sourceVariant.variant_id] = shouldUseSuggestedVariant && modalItem?.suggested_variant_name
                ? modalItem.suggested_variant_name
                : sourceVariant.variant_name;
        });

        setSelectedTargetVariants(nextSelectedTargetVariants);
        setNewVariantNames(nextNewVariantNames);

        if (!renameVariantId && mergePreview.source_variants.length === 1) {
            setRenameVariantId(
                suggestedVariantExists
                    ? (modalItem?.suggested_variant_id || '')
                    : mergePreview.source_variants[0].variant_id
            );
        }
    }, [mergePreview, modalItem, renameVariantId, variantOptions]);

    useEffect(() => {
        if (!modalItem || modalEntryPoint !== 'rename') return;

        const timeoutId = window.setTimeout(() => {
            renameSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 0);

        return () => window.clearTimeout(timeoutId);
    }, [modalEntryPoint, modalItem]);

    const openResolutionModal = (
        item: ResolutionItem,
        initialTargetId?: string,
        entryPoint: 'search' | 'rename' = 'search'
    ) => {
        setModalItem(item);
        setModalEntryPoint(entryPoint);
        setTargetSearch(initialTargetId && item.suggestion_name ? item.suggestion_name : '');
        setSelectedTargetId(initialTargetId || '');
        setMergePreview(null);
        setRenameName(item.name);
        setRenameType(item.type);
        setRenameVariantId(item.suggested_variant_id || item.source_variant_id || '');
    };

    const closeResolutionModal = () => {
        setModalItem(null);
        setModalEntryPoint('search');
        setTargetSearch('');
        setSelectedTargetId('');
        setMergePreview(null);
        setRenameName('');
        setRenameType('');
        setRenameVariantId('');
        setMergeSubmitting(false);
        setRenameSubmitting(false);
        setSelectedTargetVariants({});
        setNewVariantNames({});
    };

    const handleVerifyAsNew = (item: ResolutionItem) => {
        openResolutionModal(item, undefined, 'rename');
    };

    const handleMerge = async () => {
        if (!modalItem || !selectedTargetId) {
            setPopup({ type: 'error', message: 'Select a verified target item first.' });
            return;
        }

        const sourceVariants = mergePreview?.source_variants || [];
        const selectedSourceVariant = sourceVariants[0];
        if (!selectedSourceVariant) {
            setPopup({ type: 'error', message: 'Unable to load the selected source variant preview.' });
            return;
        }

        const missingVariantMapping = sourceVariants.some(sourceVariant => {
            const selectedTargetVariant = selectedTargetVariants[sourceVariant.variant_id];
            if (selectedTargetVariant === '__new__') {
                return !(newVariantNames[sourceVariant.variant_id] || '').trim();
            }
            return !selectedTargetVariant;
        });

        if (missingVariantMapping) {
            setPopup({ type: 'error', message: 'Choose a target child variant or provide a new variant name for every source variant.' });
            return;
        }

        setMergeSubmitting(true);
        try {
            const selectedTargetVariant = selectedTargetVariants[selectedSourceVariant.variant_id];
            const res = await endpoints.menu.resolve({
                source_menu_item_id: modalItem.menu_item_id,
                source_variant_id: modalItem.source_variant_id,
                target_menu_item_id: selectedTargetId,
                target_variant_id: selectedTargetVariant === '__new__' ? undefined : selectedTargetVariant,
                new_variant_name: selectedTargetVariant === '__new__'
                    ? (newVariantNames[selectedSourceVariant.variant_id] || '').trim()
                    : undefined,
            });
            removeResolvedItem(modalItem.menu_item_id, modalItem.source_variant_id);
            setPopup({ type: 'success', message: res.data.message || 'Variant resolved successfully.' });
            closeResolutionModal();
            await refreshAll();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setMergeSubmitting(false);
        }
    };

    const handleRenameResolution = async () => {
        if (!modalItem) return;

        const trimmedName = renameName.trim();
        const trimmedType = renameType.trim();

        if (!trimmedName || !trimmedType) {
            setPopup({ type: 'error', message: 'Name and type are both required.' });
            return;
        }

        if (!renameVariantId) {
            setPopup({ type: 'error', message: 'Select a variant type before saving.' });
            return;
        }

        setRenameSubmitting(true);
        try {
            const res = await endpoints.menu.resolve({
                source_menu_item_id: modalItem.menu_item_id,
                source_variant_id: modalItem.source_variant_id,
                new_name: trimmedName,
                new_type: trimmedType,
                target_variant_id: renameVariantId,
            });
            removeResolvedItem(modalItem.menu_item_id, modalItem.source_variant_id);
            setPopup({ type: 'success', message: res.data.message || 'Resolution saved successfully.' });
            closeResolutionModal();
            await refreshAll();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setRenameSubmitting(false);
        }
    };

    const handleUndo = async (mergeId: number) => {
        if (!window.confirm('Undo this merge?')) return;

        setUndoingMergeId(mergeId);
        try {
            await endpoints.menu.undoMerge({ merge_id: mergeId });
            setPopup({ type: 'success', message: 'Merge undone successfully.' });
            await refreshAll();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUndoingMergeId(null);
        }
    };

    const eligibleTargets = modalItem
        ? lookupItems.filter(candidate => candidate.is_verified && candidate.menu_item_id !== modalItem.menu_item_id)
        : [];

    const filteredTargets = eligibleTargets.filter(candidate =>
        `${candidate.name} ${candidate.type}`.toLowerCase().includes(targetSearch.toLowerCase())
    );

    const renameCollisionTarget = modalItem
        ? lookupItems.find(candidate =>
            candidate.is_verified &&
            candidate.menu_item_id !== modalItem.menu_item_id &&
            candidate.name.trim().toLowerCase() === renameName.trim().toLowerCase() &&
            candidate.type.trim().toLowerCase() === renameType.trim().toLowerCase()
        )
        : undefined;

    const variantMappingsComplete = (mergePreview?.source_variants || []).every(sourceVariant => {
        const selectedTargetVariant = selectedTargetVariants[sourceVariant.variant_id];
        if (!selectedTargetVariant) return false;
        if (selectedTargetVariant === '__new__') {
            return Boolean((newVariantNames[sourceVariant.variant_id] || '').trim());
        }
        return true;
    });

    return (
        <div>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <h2 style={{ color: 'var(--text-color)' }}>✨ Unclustered Data Resolution</h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>
                Resolve each unclustered menu item + variant pair by merging it into a canonical match, verifying it as a distinct pair, or manually renaming/searching for the right target.
            </p>
            {loading ? (
                <div>Loading...</div>
            ) : items.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-color)' }}>
                    <div style={{ fontSize: '3em', marginBottom: '10px' }}>✅</div>
                    <h3>All items verified!</h3>
                    <p style={{ color: 'var(--text-secondary)' }}>No unclustered items found.</p>
                </div>
            ) : (
                items.map(item => (
                    <Card key={`${item.menu_item_id}-${item.source_variant_id}`} title={formatResolutionTitle(item)}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(280px, 420px)', gap: '20px' }}>
                            <div>
                                <p>Created: {new Date(item.created_at).toLocaleString()}</p>
                                <p style={{ color: 'var(--text-secondary)' }}>Type: {item.type}</p>
                                <p style={{ color: 'var(--text-secondary)' }}>Source variant: {item.source_variant_name}</p>
                                <p style={{ color: 'var(--text-secondary)' }}>
                                    Unresolved rows: {item.unresolved_mapping_rows || 0} mappings
                                    {item.order_item_rows ? `, ${item.order_item_rows} order rows` : ''}
                                    {item.order_item_qty ? `, ${item.order_item_qty} qty` : ''}
                                </p>
                                {item.suggestion_id ? (
                                    <div style={{ padding: '12px', background: 'rgba(59, 130, 246, 0.08)', border: '1px solid rgba(59, 130, 246, 0.2)', borderRadius: '10px' }}>
                                        <div style={{ fontSize: '0.8em', fontWeight: 700, color: '#3B82F6', marginBottom: '6px' }}>
                                            Suggested verified match
                                        </div>
                                        <div style={{ color: '#7C83FD' }}>
                                            {item.suggestion_name}
                                            {item.suggestion_type ? ` (${item.suggestion_type})` : ''}
                                        </div>
                                        {item.suggested_variant_name && (
                                            <div style={{ marginTop: '6px', fontSize: '0.85em', color: '#94A3B8' }}>
                                                Suggested variant: {item.suggested_variant_name}
                                            </div>
                                        )}
                                    </div>
                                ) : (
                                    <p style={{ color: 'var(--text-secondary)' }}>
                                        No suggestion available. Use Search &amp; Merge or verify this item as a new menu item.
                                    </p>
                                )}
                            </div>
                            <div style={{ display: 'flex', gap: '10px', flexDirection: 'column' }}>
                                {item.suggestion_id && item.suggestion_name && (
                                    <button
                                        onClick={() => openResolutionModal(item, item.suggestion_id || undefined)}
                                        style={{ padding: '12px', background: '#2563EB', color: 'white', border: 'none', cursor: 'pointer', borderRadius: '8px', fontWeight: 700 }}
                                    >
                                        {getMergeSuggestionLabel(item)}
                                    </button>
                                )}
                                <button
                                    onClick={() => handleVerifyAsNew(item)}
                                    style={{ padding: '12px', background: '#44aa44', color: 'white', border: 'none', cursor: 'pointer', borderRadius: '8px', fontWeight: 700 }}
                                >
                                    Verify as New Item
                                </button>
                                <p style={{ margin: 0, fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                                    Keeps this variant as its own verified menu item + variant pair.
                                </p>
                                <button
                                    onClick={() => openResolutionModal(item)}
                                    style={{ padding: '12px', background: 'var(--card-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', cursor: 'pointer', borderRadius: '8px', fontWeight: 700 }}
                                >
                                    Rename / Search
                                </button>
                            </div>
                        </div>
                    </Card>
                ))
            )}
            <CollapsibleCard title="Recent Merge History" defaultCollapsed>
                {mergeHistory.length === 0 ? (
                    <p style={{ margin: 0, color: 'var(--text-secondary)' }}>No recent merges to undo.</p>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {mergeHistory.map(entry => (
                            <div
                                key={entry.merge_id}
                                style={{
                                    display: 'flex',
                                    justifyContent: 'space-between',
                                    alignItems: 'center',
                                    gap: '12px',
                                    padding: '10px 0',
                                    borderBottom: '1px solid var(--border-color)',
                                }}
                            >
                                <div>
                                    <div style={{ color: 'var(--text-color)' }}>
                                        <span style={{ color: '#EF4444' }}>{entry.source_name}</span>
                                        {' → '}
                                        <span style={{ color: '#10B981' }}>{entry.target_name || 'Deleted target'}</span>
                                    </div>
                                    {renderVariantAssignments(entry.variant_assignments)}
                                    <div style={{ fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                                        {new Date(entry.merged_at).toLocaleString()}
                                    </div>
                                </div>
                                <button
                                    onClick={() => handleUndo(entry.merge_id)}
                                    disabled={undoingMergeId === entry.merge_id}
                                    style={{ padding: '8px 14px', background: '#444', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer' }}
                                >
                                    {undoingMergeId === entry.merge_id ? 'Undoing...' : 'Undo'}
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </CollapsibleCard>
            {modalItem && (
                <div
                    onClick={closeResolutionModal}
                    style={{
                        position: 'fixed',
                        inset: 0,
                        background: 'rgba(15, 23, 42, 0.65)',
                        zIndex: 2000,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        padding: '20px',
                    }}
                >
                    <div
                        onClick={(event) => event.stopPropagation()}
                        style={{
                            width: 'min(980px, 100%)',
                            maxHeight: '90vh',
                            overflowY: 'auto',
                            background: 'var(--card-bg)',
                            borderRadius: '16px',
                            border: '1px solid var(--border-color)',
                            boxShadow: 'var(--shadow)',
                            padding: '24px',
                        }}
                    >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', gap: '12px', marginBottom: '20px' }}>
                            <div>
                                <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>Resolve {formatResolutionTitle(modalItem)}</h3>
                                <p style={{ margin: '8px 0 0', color: 'var(--text-secondary)' }}>
                                    {modalEntryPoint === 'rename'
                                        ? 'Review the name, type, and target variant for this specific unresolved source variant.'
                                        : 'Choose an existing verified target for this specific unresolved source variant, or rename it before verifying it.'}
                                </p>
                            </div>
                            <button
                                onClick={closeResolutionModal}
                                style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '1.2em' }}
                            >
                                ✕
                            </button>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '20px' }}>
                            <div style={{ border: '1px solid var(--border-color)', borderRadius: '12px', padding: '16px' }}>
                                <h4 style={{ marginTop: 0, color: 'var(--text-color)' }}>Search &amp; Merge</h4>
                                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                    Move only this unresolved source variant into an existing verified menu item and target variant.
                                </p>
                                <input
                                    value={targetSearch}
                                    onChange={(event) => setTargetSearch(event.target.value)}
                                    placeholder="Search verified menu items"
                                    style={{
                                        width: '100%',
                                        padding: '10px 12px',
                                        borderRadius: '8px',
                                        border: '1px solid var(--border-color)',
                                        background: 'var(--input-bg)',
                                        color: 'var(--text-color)',
                                        marginBottom: '12px',
                                        boxSizing: 'border-box',
                                    }}
                                />
                                <div style={{ maxHeight: '220px', overflowY: 'auto', border: '1px solid var(--border-color)', borderRadius: '8px', marginBottom: '12px' }}>
                                    {filteredTargets.length === 0 ? (
                                        <div style={{ padding: '12px', color: 'var(--text-secondary)' }}>No verified targets match this search.</div>
                                    ) : (
                                        filteredTargets.slice(0, 40).map(candidate => (
                                            <button
                                                key={candidate.menu_item_id}
                                                onClick={() => setSelectedTargetId(candidate.menu_item_id)}
                                                style={{
                                                    width: '100%',
                                                    textAlign: 'left',
                                                    padding: '12px',
                                                    border: 'none',
                                                    borderBottom: '1px solid var(--border-color)',
                                                    background: selectedTargetId === candidate.menu_item_id ? 'rgba(37, 99, 235, 0.12)' : 'transparent',
                                                    color: 'var(--text-color)',
                                                    cursor: 'pointer',
                                                }}
                                            >
                                                <div style={{ fontWeight: 700 }}>{candidate.name}</div>
                                                <div style={{ fontSize: '0.85em', color: 'var(--text-secondary)' }}>{candidate.type}</div>
                                            </button>
                                        ))
                                    )}
                                </div>
                                <div style={{ border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px', minHeight: '140px', background: 'rgba(148, 163, 184, 0.08)' }}>
                                    {previewLoading ? (
                                        <div style={{ color: 'var(--text-secondary)' }}>Loading merge preview...</div>
                                    ) : mergePreview ? (
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                                            <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>
                                                {mergePreview.source.name} ({mergePreview.source.type}) → {mergePreview.target.name} ({mergePreview.target.type})
                                            </div>
                                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                                This will relink {mergePreview.stats.order_items_relinked} order items, {mergePreview.stats.addon_items_relinked} addon rows, and {mergePreview.stats.mappings_updated} item mappings for the selected source variant.
                                            </div>
                                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                                Selected source-variant totals to be absorbed: {mergePreview.stats.source_total_sold} sold, ₹{Math.round(mergePreview.stats.source_total_revenue).toLocaleString()} revenue.
                                            </div>
                                            <div style={{ color: '#F59E0B', fontSize: '0.9em' }}>
                                                Sibling unresolved variants, if any, will remain separate. You can undo target-changing moves from Recent Merge History.
                                            </div>
                                        </div>
                                    ) : (
                                        <div style={{ color: 'var(--text-secondary)' }}>
                                            Select a verified target to preview the merge before confirming it.
                                        </div>
                                    )}
                                </div>
                                {mergePreview && (
                                    <div style={{ marginTop: '12px', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px', background: 'rgba(37, 99, 235, 0.05)' }}>
                                        <div style={{ fontWeight: 700, color: 'var(--text-color)', marginBottom: '10px' }}>
                                            Target variant mapping
                                        </div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                            {mergePreview.source_variants.map(sourceVariant => (
                                                <div
                                                    key={sourceVariant.variant_id}
                                                    style={{
                                                        border: '1px solid var(--border-color)',
                                                        borderRadius: '8px',
                                                        padding: '12px',
                                                        background: 'var(--card-bg)',
                                                    }}
                                                >
                                                    <div style={{ fontWeight: 700, color: 'var(--text-color)' }}>
                                                        {sourceVariant.variant_name}
                                                    </div>
                                                    <div style={{ fontSize: '0.85em', color: 'var(--text-secondary)', margin: '6px 0 10px' }}>
                                                        {sourceVariant.order_item_rows} order rows / {sourceVariant.addon_rows} addon rows / {sourceVariant.mapping_rows} cluster mappings
                                                    </div>
                                                    <select
                                                        value={selectedTargetVariants[sourceVariant.variant_id] || ''}
                                                        onChange={(event) => {
                                                            const nextValue = event.target.value;
                                                            setSelectedTargetVariants(current => ({
                                                                ...current,
                                                                [sourceVariant.variant_id]: nextValue,
                                                            }));
                                                            if (nextValue === '__new__') {
                                                                setNewVariantNames(current => ({
                                                                    ...current,
                                                                    [sourceVariant.variant_id]: current[sourceVariant.variant_id] || sourceVariant.variant_name,
                                                                }));
                                                            }
                                                        }}
                                                        style={{
                                                            width: '100%',
                                                            padding: '10px 12px',
                                                            borderRadius: '8px',
                                                            border: '1px solid var(--border-color)',
                                                            background: 'var(--input-bg)',
                                                            color: 'var(--text-color)',
                                                            marginBottom: selectedTargetVariants[sourceVariant.variant_id] === '__new__' ? '10px' : 0,
                                                        }}
                                                    >
                                                        <option value="">Select variant type</option>
                                                        {variantOptions.map(targetVariant => (
                                                            <option key={targetVariant.variant_id} value={targetVariant.variant_id}>
                                                                {targetVariant.name}
                                                            </option>
                                                        ))}
                                                        <option value="__new__">Create new variant type...</option>
                                                    </select>
                                                    {selectedTargetVariants[sourceVariant.variant_id] === '__new__' && (
                                                        <input
                                                            value={newVariantNames[sourceVariant.variant_id] || ''}
                                                            onChange={(event) => setNewVariantNames(current => ({
                                                                ...current,
                                                                [sourceVariant.variant_id]: event.target.value,
                                                            }))}
                                                            placeholder="New child variant name"
                                                            style={{
                                                                width: '100%',
                                                                padding: '10px 12px',
                                                                borderRadius: '8px',
                                                                border: '1px solid var(--border-color)',
                                                                background: 'var(--input-bg)',
                                                                color: 'var(--text-color)',
                                                                boxSizing: 'border-box',
                                                            }}
                                                        />
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                        <div style={{ marginTop: '12px', fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                                            This dropdown includes every variant type currently available in the database.
                                        </div>
                                    </div>
                                )}
                                <button
                                    onClick={() => void handleMerge()}
                                    disabled={!selectedTargetId || previewLoading || mergeSubmitting || !variantMappingsComplete}
                                    style={{
                                        width: '100%',
                                        marginTop: '12px',
                                        padding: '12px',
                                        background: '#2563EB',
                                        color: 'white',
                                        border: 'none',
                                        borderRadius: '8px',
                                        cursor: !selectedTargetId || previewLoading || mergeSubmitting || !variantMappingsComplete ? 'not-allowed' : 'pointer',
                                        fontWeight: 700,
                                        opacity: !selectedTargetId || previewLoading || mergeSubmitting || !variantMappingsComplete ? 0.7 : 1,
                                    }}
                                >
                                    {mergeSubmitting ? 'Resolving...' : 'Confirm Variant Move'}
                                </button>
                            </div>

                            <div
                                ref={renameSectionRef}
                                style={{
                                    border: modalEntryPoint === 'rename' ? '2px solid #10B981' : '1px solid var(--border-color)',
                                    borderRadius: '12px',
                                    padding: '16px',
                                    boxShadow: modalEntryPoint === 'rename' ? '0 0 0 4px rgba(16, 185, 129, 0.12)' : 'none',
                                }}
                            >
                                <h4 style={{ marginTop: 0, color: 'var(--text-color)' }}>Rename / Verify</h4>
                                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                    Use this when the selected source variant should stay separate, but under a cleaner menu item name, type, or variant assignment.
                                </p>
                                <label style={{ display: 'block', fontSize: '0.85em', color: 'var(--text-secondary)', marginBottom: '6px' }}>Name</label>
                                <input
                                    value={renameName}
                                    onChange={(event) => setRenameName(event.target.value)}
                                    style={{
                                        width: '100%',
                                        padding: '10px 12px',
                                        borderRadius: '8px',
                                        border: '1px solid var(--border-color)',
                                        background: 'var(--input-bg)',
                                        color: 'var(--text-color)',
                                        marginBottom: '12px',
                                        boxSizing: 'border-box',
                                    }}
                                />
                                <label style={{ display: 'block', fontSize: '0.85em', color: 'var(--text-secondary)', marginBottom: '6px' }}>Type</label>
                                <input
                                    value={renameType}
                                    onChange={(event) => setRenameType(event.target.value)}
                                    style={{
                                        width: '100%',
                                        padding: '10px 12px',
                                        borderRadius: '8px',
                                        border: '1px solid var(--border-color)',
                                        background: 'var(--input-bg)',
                                        color: 'var(--text-color)',
                                        marginBottom: '12px',
                                        boxSizing: 'border-box',
                                    }}
                                />
                                <label style={{ display: 'block', fontSize: '0.85em', color: 'var(--text-secondary)', marginBottom: '6px' }}>Variant Type</label>
                                <select
                                    value={renameVariantId}
                                    onChange={(event) => setRenameVariantId(event.target.value)}
                                    style={{
                                        width: '100%',
                                        padding: '10px 12px',
                                        borderRadius: '8px',
                                        border: '1px solid var(--border-color)',
                                        background: 'var(--input-bg)',
                                        color: 'var(--text-color)',
                                        marginBottom: '12px',
                                        boxSizing: 'border-box',
                                    }}
                                >
                                    <option value="">Select variant type</option>
                                    {variantOptions.map(variant => (
                                        <option key={variant.variant_id} value={variant.variant_id}>
                                            {variant.name}
                                        </option>
                                    ))}
                                </select>
                                <div style={{ marginBottom: '12px', color: 'var(--text-secondary)', fontSize: '0.85em' }}>
                                    The selected variant will be applied only to this source variant&apos;s current rows when you save the resolution.
                                </div>
                                <div style={{ border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px', background: 'rgba(16, 185, 129, 0.08)', color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                    If the new name and type exactly match an existing verified item, saving here will move this source variant into that item and keep the selected variant assignment.
                                </div>
                                {renameCollisionTarget && (
                                    <div style={{ marginTop: '12px', border: '1px solid rgba(245, 158, 11, 0.35)', borderRadius: '8px', padding: '12px', background: 'rgba(245, 158, 11, 0.08)', color: '#D97706', fontSize: '0.9em' }}>
                                        Exact match found: {renameCollisionTarget.name} ({renameCollisionTarget.type}). Saving this rename will merge into that verified item.
                                    </div>
                                )}
                                <button
                                    onClick={() => void handleRenameResolution()}
                                    disabled={renameSubmitting}
                                    style={{
                                        width: '100%',
                                        marginTop: '12px',
                                        padding: '12px',
                                        background: '#10B981',
                                        color: 'white',
                                        border: 'none',
                                        borderRadius: '8px',
                                        cursor: renameSubmitting ? 'not-allowed' : 'pointer',
                                        fontWeight: 700,
                                        opacity: renameSubmitting ? 0.7 : 1,
                                    }}
                                >
                                    {renameSubmitting ? 'Saving...' : 'Save Variant Resolution'}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// --- Main Page ---

export default function Menu({ lastDbSync }: { lastDbSync?: number }) {
    const [activeTab, setActiveTab] = useState<'summary' | 'items' | 'variants' | 'matrix' | 'resolutions'>('summary');

    const menuTabs = [
        { id: 'summary' as const, label: '📊 Summary' },
        { id: 'items' as const, label: '📋 Menu Items' },
        { id: 'variants' as const, label: '📏 Variants' },
        { id: 'matrix' as const, label: '🕸️ Menu Matrix' },
        { id: 'resolutions' as const, label: '✨ Resolutions' },
    ];

    return (
        <div className="page-container" style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>
            <div className="segmented-control segmented-page-tabs" style={{ marginBottom: '20px' }}>
                {menuTabs.map((tab) => (
                    <TabButton
                        key={tab.id}
                        active={activeTab === tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        variant="segmented"
                        size="large"
                    >
                        {tab.label}
                    </TabButton>
                ))}
            </div>

            {activeTab === 'summary' && <SummaryTab lastDbSync={lastDbSync} />}
            {activeTab === 'items' && <MenuItemsTab lastDbSync={lastDbSync} />}
            {activeTab === 'variants' && <VariantsTab lastDbSync={lastDbSync} />}
            {activeTab === 'matrix' && <MatrixTab lastDbSync={lastDbSync} />}
            {activeTab === 'resolutions' && <ResolutionsTab lastDbSync={lastDbSync} />}
        </div>
    );
}
