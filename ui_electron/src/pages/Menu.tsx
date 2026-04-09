import { useState, useEffect, useRef } from 'react';
import { endpoints } from '../api';
import { ErrorPopup } from '../components';
import type { PopupMessage } from '../components';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';
import { formatColumnHeader } from '../utils';

// --- Shared Components ---

const CollapsibleCard = ({ children, title, defaultCollapsed = false }: { children: React.ReactNode, title: string, defaultCollapsed?: boolean }) => {
    const [collapsed, setCollapsed] = useState(defaultCollapsed);
    return (
        <div style={{ background: 'var(--card-bg)', padding: '20px', borderRadius: '12px', marginBottom: '20px', border: '1px solid var(--border-color)', boxShadow: 'var(--shadow)' }}>
            <div
                onClick={() => setCollapsed(!collapsed)}
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer', marginBottom: collapsed ? 0 : '15px' }}
            >
                <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>{title}</h3>
                <span style={{ color: 'var(--text-secondary)', fontSize: '1.2em' }}>{collapsed ? '+' : '−'}</span>
            </div>
            {!collapsed && children}
        </div>
    );
};

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
    onExportCSV,
    defaultHeight = 600
}: {
    children: React.ReactNode;
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
                justifyContent: 'flex-end',
                marginBottom: '10px',
            }}>
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

// --- Menu Items Tab ---

function MenuItemsTab({ lastDbSync }: { lastDbSync?: number }) {
    // State for Merge Tool
    const [itemsList, setItemsList] = useState<any[]>([]);
    const [sourceId, setSourceId] = useState('');
    const [targetId, setTargetId] = useState('');
    const [mergeHistory, setMergeHistory] = useState<any[]>([]);
    const [loadingMerge, setLoadingMerge] = useState(false);
    const [popup, setPopup] = useState<PopupMessage | null>(null);

    // State for Table
    const [tableData, setTableData] = useState<any[]>([]);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('total_revenue');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
    const [total, setTotal] = useState(0);
    const [loadingTable, setLoadingTable] = useState(false);
    const [search, setSearch] = useState('');

    useEffect(() => {
        loadDropdowns();
        loadHistory();
        loadTable();
    }, []);

    useEffect(() => {
        loadTable();
    }, [page, search, pageSize, sortKey, sortDirection, lastDbSync]);

    const loadDropdowns = async () => {
        try {
            const res = await endpoints.menu.list();
            setItemsList(res.data);
        } catch (e) { console.error(e); }
    };

    const loadHistory = async () => {
        try {
            const res = await endpoints.menu.mergeHistory();
            setMergeHistory(res.data);
        } catch (e) { console.error(e); }
    };

    const loadTable = async () => {
        setLoadingTable(true);
        try {
            const filters = search ? JSON.stringify({ name: search }) : undefined;
            const res = await endpoints.menu.itemsView({
                page,
                page_size: pageSize,
                sort_by: sortKey,
                sort_desc: sortDirection === 'desc',
                filters
            });
            setTableData(res.data.data);
            setTotal(res.data.total);
        } catch (e) { console.error(e); }
        finally { setLoadingTable(false); }
    };

    const handleMerge = async () => {
        if (!sourceId || !targetId) { setPopup({ type: 'error', message: "Select both items" }); return; }
        if (sourceId === targetId) { setPopup({ type: 'error', message: "Cannot merge same item" }); return; }
        if (!confirm("Are you sure? Source item will be deleted.")) return;

        setLoadingMerge(true);
        try {
            await endpoints.menu.merge({ source_id: sourceId, target_id: targetId });
            setPopup({ type: 'success', message: "Merged successfully" });
            setSourceId(''); setTargetId('');
            loadDropdowns();
            loadHistory();
            loadTable();
        } catch (e: any) {
            setPopup({ type: 'error', message: e.response?.data?.detail || e.message });
        } finally {
            setLoadingMerge(false);
        }
    };

    const handleUndo = async (mergeId: number) => {
        if (!confirm("Undo this merge?")) return;
        try {
            await endpoints.menu.undoMerge({ merge_id: mergeId });
            loadHistory();
            loadTable();
        } catch (e: any) { setPopup({ type: 'error', message: e.response?.data?.detail || e.message }); }
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

    return (
        <div>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                <CollapsibleCard title="🛠️ Merge Menu Items" defaultCollapsed={true}>
                    <p style={{ fontSize: '0.9em', color: '#ccc' }}>Merge a duplicate item (Source) into a canonical item (Target).</p>
                    <div style={{ display: 'flex', gap: '10px', flexDirection: 'column' }}>
                        <select
                            value={sourceId}
                            onChange={e => setSourceId(e.target.value)}
                            style={{ padding: '8px', background: '#333', color: 'white', border: '1px solid #555' }}
                        >
                            <option value="">Select Source (To Delete)</option>
                            {itemsList.map(i => <option key={i.menu_item_id} value={i.menu_item_id}>{i.name} ({i.type})</option>)}
                        </select>
                        <select
                            value={targetId}
                            onChange={e => setTargetId(e.target.value)}
                            style={{ padding: '8px', background: '#333', color: 'white', border: '1px solid #555' }}
                        >
                            <option value="">Select Target (To Keep)</option>
                            {itemsList.filter(i => i.menu_item_id !== sourceId).map(i => <option key={i.menu_item_id} value={i.menu_item_id}>{i.name} ({i.type})</option>)}
                        </select>
                        <button
                            onClick={handleMerge}
                            disabled={loadingMerge}
                            style={{ padding: '10px', background: '#d93025', color: 'white', border: 'none', cursor: 'pointer', borderRadius: '4px' }}
                        >
                            {loadingMerge ? "Merging..." : "Merge Items"}
                        </button>
                    </div>
                </CollapsibleCard>

                <CollapsibleCard title="⏳ Merge History" defaultCollapsed={true}>
                    <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
                        {mergeHistory.length === 0 && <span style={{ color: '#888' }}>No history</span>}
                        {mergeHistory.map(h => (
                            <div key={h.merge_id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid #333' }}>
                                <div style={{ fontSize: '0.9em' }}>
                                    <span style={{ color: '#ff8888' }}>{h.source_name}</span> → <span style={{ color: '#88ff88' }}>{h.target_name}</span>
                                    {renderVariantAssignments(h.variant_assignments, true)}
                                    <div style={{ fontSize: '0.8em', color: '#888' }}>{new Date(h.merged_at).toLocaleString()}</div>
                                </div>
                                <button onClick={() => handleUndo(h.merge_id)} style={{ fontSize: '0.8em', background: '#444', color: 'white', border: 'none', padding: '4px 8px', borderRadius: '4px', cursor: 'pointer' }}>Undo</button>
                            </div>
                        ))}
                    </div>
                </CollapsibleCard>
            </div>

            {/* Menu Items Table Container */}
            <div style={{ marginTop: '20px' }}>
                <input
                    placeholder="Search Name..."
                    value={search}
                    onChange={e => { setSearch(e.target.value); setPage(1); }}
                    style={{ padding: '8px', marginBottom: '10px', width: '300px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', borderRadius: '4px' }}
                />

                {loadingTable ? <div>Loading...</div> : (
                    <ResizableTableWrapper onExportCSV={() => exportToCSV(tableData, 'menu_items')}>
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
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const [selectedMenuItemId, setSelectedMenuItemId] = useState('');
    const [currentVariantId, setCurrentVariantId] = useState('');
    const [newVariantId, setNewVariantId] = useState('');
    const [updating, setUpdating] = useState(false);

    // Client-Side Table State
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('name');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
    const [search, setSearch] = useState('');

    useEffect(() => {
        void loadMatrix();
        void loadLists();
    }, [lastDbSync]);

    const loadLists = async () => {
        try {
            const [itemsRes, variantsRes] = await Promise.all([
                endpoints.menu.list(),
                endpoints.menu.variantsList(),
            ]);
            setItems(itemsRes.data);
            setVariants(variantsRes.data);
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        }
    };

    const loadMatrix = async () => {
        try {
            const res = await endpoints.menu.matrix();
            setMatrixData(res.data);
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        }
    };

    const handlePrefill = (row: MatrixRow) => {
        setSelectedMenuItemId(row.menu_item_id);
        setCurrentVariantId(row.variant_id);
        setNewVariantId('');
    };

    const handleUpdateVariantMapping = async () => {
        if (!selectedMenuItemId || !currentVariantId || !newVariantId) {
            setPopup({ type: 'error', message: 'Select a menu item, current variant, and new variant before saving.' });
            return;
        }
        if (currentVariantId === newVariantId) {
            setPopup({ type: 'error', message: 'Current and new variant cannot be the same.' });
            return;
        }

        try {
            setUpdating(true);
            const res = await endpoints.menu.updateVariantMapping({
                menu_item_id: selectedMenuItemId,
                current_variant_id: currentVariantId,
                new_variant_id: newVariantId,
            });
            setPopup({ type: 'success', message: res.data.message || 'Variant mapping updated successfully.' });
            setCurrentVariantId('');
            setNewVariantId('');
            await loadMatrix();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUpdating(false);
        }
    };

    const currentVariantOptions = selectedMenuItemId
        ? Object.values(
            matrixData.reduce((acc, row) => {
                if (row.menu_item_id !== selectedMenuItemId) return acc;
                const existing = acc[row.variant_id];
                if (existing) {
                    existing.count += 1;
                    return acc;
                }
                acc[row.variant_id] = {
                    variant_id: row.variant_id,
                    variant_name: row.variant_name,
                    count: 1,
                };
                return acc;
            }, {} as Record<string, { variant_id: string; variant_name: string; count: number }>)
        ).sort((a, b) => a.variant_name.localeCompare(b.variant_name))
        : [];

    const selectedItem = items.find(item => item.menu_item_id === selectedMenuItemId);
    const selectedCurrentVariant = currentVariantOptions.find(variant => variant.variant_id === currentVariantId);
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

    return (
        <div>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <Card title="Update Menu Variant Mapping">
                <p style={{ marginTop: 0, marginBottom: '15px', color: 'var(--text-secondary)' }}>
                    Updates cluster mappings, historical order rows, addon rows, local backup files, and clears volume forecast cache because variant units can change downstream calculations.
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Menu Item</span>
                        <select
                            value={selectedMenuItemId}
                            onChange={e => {
                                setSelectedMenuItemId(e.target.value);
                                setCurrentVariantId('');
                                setNewVariantId('');
                            }}
                            style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                        >
                            <option value="">Select menu item</option>
                            {items.map(item => (
                                <option key={item.menu_item_id} value={item.menu_item_id}>
                                    {item.name} ({item.type})
                                </option>
                            ))}
                        </select>
                    </label>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Current Variant</span>
                        <select
                            value={currentVariantId}
                            onChange={e => setCurrentVariantId(e.target.value)}
                            disabled={!selectedMenuItemId}
                            style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                        >
                            <option value="">Select current variant</option>
                            {currentVariantOptions.map(variant => (
                                <option key={variant.variant_id} value={variant.variant_id}>
                                    {variant.variant_name} ({variant.count} cluster mappings)
                                </option>
                            ))}
                        </select>
                    </label>
                    <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>New Variant</span>
                        <select
                            value={newVariantId}
                            onChange={e => setNewVariantId(e.target.value)}
                            disabled={!currentVariantId}
                            style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                        >
                            <option value="">Select new variant</option>
                            {variants
                                .filter(variant => variant.variant_id !== currentVariantId)
                                .map(variant => (
                                    <option key={variant.variant_id} value={variant.variant_id}>
                                        {variant.name}
                                    </option>
                                ))}
                        </select>
                    </label>
                </div>
                {selectedItem && selectedCurrentVariant && (
                    <div style={{ marginTop: '15px', padding: '12px', borderRadius: '8px', background: 'var(--input-bg)', color: 'var(--text-secondary)' }}>
                        Updating <b style={{ color: 'var(--text-color)' }}>{selectedItem.name}</b> from{' '}
                        <b style={{ color: 'var(--text-color)' }}>{selectedCurrentVariant.variant_name}</b> across{' '}
                        <b style={{ color: 'var(--text-color)' }}>{selectedCurrentVariant.count}</b> cluster mappings that currently match this pair.
                    </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '15px' }}>
                    <button
                        onClick={handleUpdateVariantMapping}
                        disabled={updating || !selectedMenuItemId || !currentVariantId || !newVariantId}
                        style={{
                            background: 'var(--accent-color)',
                            color: 'white',
                            border: 'none',
                            padding: '8px 14px',
                            cursor: updating ? 'not-allowed' : 'pointer',
                            opacity: updating ? 0.7 : 1,
                        }}
                    >
                        {updating ? 'Updating...' : 'Update Mapping'}
                    </button>
                </div>
            </Card>

            {/* Menu Matrix Table Container */}
            <div style={{ marginTop: '20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', marginBottom: '15px', flexWrap: 'wrap' }}>
                    <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>Menu Matrix ({matrixData.length} entries)</h3>
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
                                        <button
                                            onClick={() => handlePrefill(r)}
                                            style={{
                                                background: 'transparent',
                                                color: 'var(--accent-color)',
                                                border: '1px solid var(--accent-color)',
                                                padding: '4px 8px',
                                                borderRadius: '6px',
                                                cursor: 'pointer',
                                            }}
                                        >
                                            Use
                                        </button>
                                    </td>
                                    <td>{r.name}</td>
                                    <td>{r.type}</td>
                                    <td>{r.variant_name}</td>
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

    const removeResolvedItem = (menuItemId: string) => {
        setItems(prev => prev.filter(item => item.menu_item_id !== menuItemId));
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
        setRenameVariantId('');
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
        const variantMappings = sourceVariants.map(sourceVariant => {
            const selectedTargetVariant = selectedTargetVariants[sourceVariant.variant_id];
            const newVariantName = (newVariantNames[sourceVariant.variant_id] || '').trim();

            if (selectedTargetVariant === '__new__') {
                return {
                    source_variant_id: sourceVariant.variant_id,
                    new_variant_name: newVariantName,
                };
            }

            return {
                source_variant_id: sourceVariant.variant_id,
                target_variant_id: selectedTargetVariant || undefined,
            };
        });

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
            const res = await endpoints.menu.merge({
                source_id: modalItem.menu_item_id,
                target_id: selectedTargetId,
                variant_mappings: variantMappings,
            });
            removeResolvedItem(modalItem.menu_item_id);
            setPopup({ type: 'success', message: res.data.message || 'Items merged successfully.' });
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
            const res = await endpoints.menu.verify({
                menu_item_id: modalItem.menu_item_id,
                new_name: trimmedName,
                new_type: trimmedType,
                new_variant_id: renameVariantId,
            });
            removeResolvedItem(modalItem.menu_item_id);
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
                Resolve each unclustered item by merging it into a canonical match, verifying it as a distinct item, or manually renaming/searching for the right target.
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
                    <Card key={item.menu_item_id} title={`${item.name} (${item.type})`}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(280px, 420px)', gap: '20px' }}>
                            <div>
                                <p>Created: {new Date(item.created_at).toLocaleString()}</p>
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
                                    Keeps this as a separate menu item.
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
            <Card title="Recent Merge History">
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
            </Card>
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
                                <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>Resolve {modalItem.name}</h3>
                                <p style={{ margin: '8px 0 0', color: 'var(--text-secondary)' }}>
                                    {modalEntryPoint === 'rename'
                                        ? 'Review the Rename / Verify fields first, then save this as a separate verified menu item.'
                                        : 'Choose an existing verified target to merge into, or rename this item before verifying it.'}
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
                                    Merge this unverified item into an existing verified menu item. The source item will be deleted after relinking.
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
                                                This will relink {mergePreview.stats.order_items_relinked} order items, {mergePreview.stats.addon_items_relinked} addon rows, and {mergePreview.stats.mappings_updated} item mappings.
                                            </div>
                                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                                Source totals to be absorbed: {mergePreview.stats.source_total_sold} sold, ₹{Math.round(mergePreview.stats.source_total_revenue).toLocaleString()} revenue.
                                            </div>
                                            <div style={{ color: '#F59E0B', fontSize: '0.9em' }}>
                                                The source item will be deleted. You can undo the merge from Recent Merge History.
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
                                            Variant mapping
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
                                    {mergeSubmitting ? 'Merging...' : 'Confirm Merge With Variant Mapping'}
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
                                    Use this when the current suggestion is wrong, but the item should stay as its own verified menu item under a cleaner name or type.
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
                                    The selected variant will be applied to this item&apos;s current rows when you save the resolution.
                                </div>
                                <div style={{ marginBottom: '12px', color: 'var(--text-secondary)', fontSize: '0.85em' }}>
                                    If this item contains multiple source variants, use Search &amp; Merge instead so each variant can be mapped explicitly.
                                </div>
                                <div style={{ border: '1px solid var(--border-color)', borderRadius: '8px', padding: '12px', background: 'rgba(16, 185, 129, 0.08)', color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                                    If the new name and type exactly match an existing verified item, saving here will merge into that item automatically and keep the selected variant assignment.
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
                                    {renameSubmitting ? 'Saving...' : 'Save Renamed Item'}
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
    const [activeTab, setActiveTab] = useState<'items' | 'variants' | 'matrix' | 'resolutions'>('items');

    return (
        <div className="page-container" style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>
            <div style={{ display: 'flex', gap: '5px', marginBottom: '30px', background: 'white', padding: '5px', borderRadius: '30px', boxShadow: '0 2px 10px rgba(0,0,0,0.1)' }}>
                {[
                    { id: 'items', label: '📋 Menu Items' },
                    { id: 'variants', label: '📏 Variants' },
                    { id: 'matrix', label: '🕸️ Menu Matrix' },
                    { id: 'resolutions', label: '✨ Resolutions' }
                ].map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id as any)}
                        style={{
                            flex: 1,
                            padding: '12px',
                            background: activeTab === tab.id ? '#3B82F6' : 'transparent',
                            border: 'none',
                            color: activeTab === tab.id ? 'white' : 'black',
                            cursor: 'pointer',
                            borderRadius: '25px',
                            transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                            fontWeight: activeTab === tab.id ? 600 : 500,
                            boxShadow: activeTab === tab.id ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
                        }}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {activeTab === 'items' && <MenuItemsTab lastDbSync={lastDbSync} />}
            {activeTab === 'variants' && <VariantsTab lastDbSync={lastDbSync} />}
            {activeTab === 'matrix' && <MatrixTab lastDbSync={lastDbSync} />}
            {activeTab === 'resolutions' && <ResolutionsTab lastDbSync={lastDbSync} />}
        </div>
    );
}
