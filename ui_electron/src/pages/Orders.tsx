
import { useState, useEffect, useRef } from 'react';
import { endpoints } from '../api';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';
import { formatColumnHeader } from '../utils';
import { CustomerProfile } from '../components/CustomerProfile';


// --- Shared Components (Duplicated from Menu for now to keep independent) ---

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

// Resizable Table Wrapper
function ResizableTableWrapper({
    children,
    onExportCSV,
    leftContent,
    defaultHeight = 600
}: {
    children: React.ReactNode;
    onExportCSV?: () => void;
    leftContent?: React.ReactNode;
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
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '10px',
            }}>
                <div>{leftContent}</div>
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

// --- Generic Table Component ---
function GenericTable({ title, apiCall, defaultSort = 'created.at', lastDbSync, leftContent }: { title: string, apiCall: (params: any) => Promise<any>, defaultSort?: string, lastDbSync?: number, leftContent?: React.ReactNode }) {
    const [data, setData] = useState<any[]>([]);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState(defaultSort);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            const res = await apiCall({
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
            setSortDirection('desc');
        }
        setPage(1);
    };

    const renderSortIcon = (key: string) => {
        if (sortKey !== key) return <span style={{ opacity: 0.3 }}> ⇅</span>;
        return <span>{sortDirection === 'asc' ? ' ↑' : ' ↓'}</span>;
    };

    // Define explicit columns if title matches, otherwise fallback
    let displayColumns = data.length > 0 ? Object.keys(data[0]) : [];
    if (title === 'Orders') {
        displayColumns = [
            'order_id', 'petpooja_order_id', 'stream_id', 'event_id', 'aggregate_id', 'customer_id',
            'restaurant_id', 'created_on', 'order_type', 'order_from', 'sub_order_type',
            'order_from_id', 'order_status', 'biller', 'assignee', 'table_no', 'token_no', 'no_of_persons',
            'customer_invoice_id', 'core_total', 'tax_total', 'discount_total', 'delivery_charges',
            'packaging_charge', 'service_charge', 'round_off', 'total', 'comment'
        ];
    } else if (title === 'Order Items') {
        displayColumns = [
            'order_item_id', 'order_id', 'created_on', 'menu_item_id', 'variant_id', 'petpooja_itemid', 'itemcode',
            'name_raw', 'category_name', 'quantity', 'unit_price', 'total_price', 'tax_amount',
            'discount_amount', 'specialnotes', 'sap_code', 'vendoritemcode', 'match_confidence',
            'match_method'
        ];
    } else if (title === 'Customers') {
        displayColumns = [
            'customer_id', 'customer_identity_key', 'name', 'name_normalized', 'phone', 'address',
            'gstin', 'first_order_date', 'last_order_date', 'total_orders', 'total_spent',
            'is_verified', 'created_at', 'updated_at'
        ];
    } else if (title === 'Restaurants') {
        displayColumns = [
            'restaurant_id', 'petpooja_restid', 'name', 'address', 'contact_information',
            'is_active', 'created_at', 'updated_at'
        ];
    } else if (title === 'Taxes') {
        displayColumns = [
            'order_tax_id', 'order_id', 'created_on', 'tax_title', 'tax_rate', 'tax_type', 'tax_amount'
        ];
    } else if (title === 'Discounts') {
        displayColumns = [
            'order_discount_id', 'order_id', 'created_on', 'discount_title', 'discount_type', 'discount_rate',
            'discount_amount'
        ];
    }

    // Filter columns to only those that exist in data to avoid crashes, or show empty cells
    const finalColumns = displayColumns.filter(col => data.length === 0 || data[0].hasOwnProperty(col));

    return (
        <div style={{ marginTop: '0' }}>

            {loading ? <div>Loading...</div> : (
                <ResizableTableWrapper
                    onExportCSV={() => exportToCSV(data, title.toLowerCase().replace(' ', '_'), finalColumns)}
                    leftContent={leftContent}
                >
                    <table className="standard-table">
                        <thead>
                            <tr>
                                {finalColumns.map(col => (
                                    <th key={col} onClick={() => handleSort(col)}>
                                        {formatColumnHeader(col)}{renderSortIcon(col)}
                                    </th>
                                ))}
                            </tr>
                        </thead>

                        <tbody>
                            {data.map((row, i) => (
                                <tr key={i}>
                                    {finalColumns.map(col => (
                                        <td key={col}>
                                            {typeof row[col] === 'boolean' ? (row[col] ? '✅' : '❌') :
                                                (String(row[col] || '').length > 100 ? String(row[col]).substring(0, 100) + '...' : (row[col] || '-'))}
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

export default function Orders({ lastDbSync }: { lastDbSync?: number }) {
    const [activeTab, setActiveTab] = useState<'orders' | 'items' | 'customers' | 'restaurants' | 'taxes' | 'discounts'>('orders');
    const [customerViewMode, setCustomerViewMode] = useState<'overview' | 'profile'>('overview');


    const tabs = [
        { id: 'orders', label: '🛒 Orders' },
        { id: 'items', label: '📦 Order Items' },
        { id: 'customers', label: '👥 Customers' },
        { id: 'restaurants', label: '🍽️ Restaurants' },
        { id: 'taxes', label: '📊 Taxes' },
        { id: 'discounts', label: '💰 Discounts' },
    ];

    return (
        <div className="page-container" style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>


            <div style={{ display: 'flex', gap: '5px', marginBottom: '30px', background: 'white', padding: '5px', borderRadius: '30px', overflowX: 'auto', boxShadow: '0 2px 10px rgba(0,0,0,0.1)' }}>
                {tabs.map(tab => (
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
                            minWidth: '120px',
                            boxShadow: activeTab === tab.id ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
                        }}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>


            {activeTab === 'orders' && <GenericTable title="Orders" apiCall={endpoints.orders.orders} defaultSort="created_on" lastDbSync={lastDbSync} />}
            {activeTab === 'items' && <GenericTable title="Order Items" apiCall={endpoints.orders.items} defaultSort="created_at" lastDbSync={lastDbSync} />}
            {activeTab === 'customers' && (
                <div>
                    {customerViewMode === 'overview' ? (
                        <GenericTable
                            title="Customers"
                            apiCall={endpoints.orders.customers}
                            defaultSort="last_order_date"
                            lastDbSync={lastDbSync}
                            leftContent={
                                <div className="segmented-control">
                                    <button
                                        onClick={() => setCustomerViewMode('overview')}
                                        className="segmented-btn-active"
                                    >
                                        Overview
                                    </button>
                                    <button
                                        onClick={() => setCustomerViewMode('profile')}
                                        className="segmented-btn-inactive"
                                    >
                                        Profile Search
                                    </button>
                                </div>
                            }
                        />
                    ) : (
                        <CustomerProfile
                            headerActions={
                                <div className="segmented-control">
                                    <button
                                        onClick={() => setCustomerViewMode('overview')}
                                        className="segmented-btn-inactive"
                                    >
                                        Overview
                                    </button>
                                    <button
                                        onClick={() => setCustomerViewMode('profile')}
                                        className="segmented-btn-active"
                                    >
                                        Profile Search
                                    </button>
                                </div>
                            }
                        />
                    )}
                </div>
            )}


            {activeTab === 'restaurants' && <GenericTable title="Restaurants" apiCall={endpoints.orders.restaurants} defaultSort="restaurant_id" lastDbSync={lastDbSync} />}
            {activeTab === 'taxes' && <GenericTable title="Taxes" apiCall={endpoints.orders.taxes} defaultSort="created_at" lastDbSync={lastDbSync} />}
            {activeTab === 'discounts' && <GenericTable title="Discounts" apiCall={endpoints.orders.discounts} defaultSort="created_at" lastDbSync={lastDbSync} />}
        </div >
    );
}
