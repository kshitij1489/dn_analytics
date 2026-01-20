import { useState, useEffect, useRef } from 'react';
import { endpoints } from '../api';
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

// --- CSV Export Utility ---
function exportToCSV(data: any[], filename: string, headers?: string[]) {
    if (!data || data.length === 0) {
        alert('No data to export');
        return;
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
        if (!sourceId || !targetId) return alert("Select both items");
        if (sourceId === targetId) return alert("Cannot merge same item");
        if (!confirm("Are you sure? Source item will be deleted.")) return;

        setLoadingMerge(true);
        try {
            await endpoints.menu.merge({ source_id: sourceId, target_id: targetId });
            alert("Merged successfully");
            setSourceId(''); setTargetId('');
            loadDropdowns();
            loadHistory();
            loadTable();
        } catch (e: any) {
            alert("Error: " + (e.response?.data?.detail || e.message));
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
        } catch (e: any) { alert("Error: " + (e.response?.data?.detail || e.message)); }
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
    const [oid, setOid] = useState('');
    const [checkResult, setCheckResult] = useState<any>(null);
    const [items, setItems] = useState<any[]>([]);
    const [variants, setVariants] = useState<any[]>([]);
    const [targetItem, setTargetItem] = useState('');
    const [targetVariant, setTargetVariant] = useState('');
    const [matrixData, setMatrixData] = useState<any[]>([]);

    // Client-Side Table State
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('name');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

    useEffect(() => {
        loadMatrix();
        loadLists();
    }, [lastDbSync]);

    const loadLists = async () => {
        const i = await endpoints.menu.list();
        const v = await endpoints.menu.variantsList();
        setItems(i.data);
        setVariants(v.data);
    };

    const loadMatrix = async () => {
        const res = await endpoints.menu.matrix();
        setMatrixData(res.data);
    };

    const checkOid = async () => {
        if (!oid) return;
        const res = await endpoints.menu.remapCheck(oid);
        setCheckResult(res.data);
    };

    const handleRemap = async () => {
        try {
            await endpoints.menu.remap({ order_item_id: oid, new_menu_item_id: targetItem, new_variant_id: targetVariant });
            alert("Remapped successfully!");
            setOid(''); setCheckResult(null); loadMatrix();
        } catch (e: any) { alert("Error: " + (e.response?.data?.detail || e.message)); }
    };

    // --- Client Side Sorting & Pagination Logic ---
    const getProcessedData = () => {
        let sorted = [...matrixData];
        if (sortKey) {
            sorted.sort((a, b) => {
                let aVal = a[sortKey];
                let bVal = b[sortKey];
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
    const total = matrixData.length;

    return (
        <div>
            {/* Remap Order Item Card - Keep this as Card or change to flat? Keeping as Card for now as it's a form */}
            <Card title="🔄 Remap Order Item">
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <input value={oid} onChange={e => setOid(e.target.value)} placeholder="Order Item ID" style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }} />
                    <button onClick={checkOid}>Check</button>
                </div>
                {checkResult && checkResult.found && (
                    <div style={{ marginTop: '10px', background: 'var(--input-bg)', padding: '10px' }}>
                        <p>Currently: <b>{checkResult.current_item}</b> ({checkResult.current_variant})</p>
                        <div style={{ display: 'flex', gap: '10px', marginTop: '10px' }}>
                            <select value={targetItem} onChange={e => setTargetItem(e.target.value)} style={{ padding: '5px' }}>
                                <option>Select Item</option>
                                {items.map(i => <option key={i.menu_item_id} value={i.menu_item_id}>{i.name}</option>)}
                            </select>
                            <select value={targetVariant} onChange={e => setTargetVariant(e.target.value)} style={{ padding: '5px' }}>
                                <option>Select Variant</option>
                                {variants.map(v => <option key={v.variant_id} value={v.variant_id}>{v.name}</option>)}
                            </select>
                            <button onClick={handleRemap} style={{ background: 'var(--accent-color)', color: 'white', border: 'none', padding: '5px 10px' }}>Remap</button>
                        </div>
                    </div>
                )}
                {checkResult && !checkResult.found && <p style={{ color: 'orange' }}>Order ID not found in cluster map.</p>}
            </Card>

            {/* Menu Matrix Table Container */}
            <div style={{ marginTop: '20px' }}>
                <h3 style={{ marginTop: 0, marginBottom: '15px', color: 'var(--accent-color)' }}>Menu Matrix ({matrixData.length} entries)</h3>

                <ResizableTableWrapper onExportCSV={() => exportToCSV(matrixData, 'menu_matrix')}>
                    <table className="standard-table">
                        <thead>
                            <tr>
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

// --- Resolutions Tab ---

function ResolutionsTab({ lastDbSync }: { lastDbSync?: number }) {
    const [items, setItems] = useState<any[]>([]);

    const load = async () => {
        const res = await endpoints.menu.unverified();
        setItems(res.data);
    };

    useEffect(() => { load(); }, [lastDbSync]);

    const handleVerify = async (id: string, newName?: string, newType?: string) => {
        try {
            await endpoints.menu.verify({ menu_item_id: id, new_name: newName, new_type: newType });
            load();
        } catch (e: any) { alert("Error: " + e.message); }
    };

    return (
        <div>
            <h2 style={{ color: 'var(--text-color)' }}>✨ Unclustered Data Resolution</h2>
            {items.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-color)' }}>
                    <div style={{ fontSize: '3em', marginBottom: '10px' }}>✅</div>
                    <h3>All items verified!</h3>
                    <p style={{ color: 'var(--text-secondary)' }}>No unclustered items found.</p>
                </div>
            ) : (
                items.map(item => (
                    <Card key={item.menu_item_id} title={`${item.name} (${item.type})`}>
                        <div style={{ display: 'flex', gap: '20px' }}>
                            <div style={{ flex: 1 }}>
                                <p>Created: {new Date(item.created_at).toLocaleString()}</p>
                                {item.suggestion_id && <p style={{ color: '#a5a5f0' }}>💡 Suggestion: {item.suggestion_name}</p>}
                            </div>
                            <div style={{ flex: 1, display: 'flex', gap: '10px', flexDirection: 'column' }}>
                                <button onClick={() => handleVerify(item.menu_item_id)} style={{ padding: '10px', background: '#44aa44', color: 'white', border: 'none', cursor: 'pointer' }}>
                                    Verify as Is
                                </button>
                                {/* Rename logic would go here, effectively verify with new name */}
                            </div>
                        </div>
                    </Card>
                ))
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
