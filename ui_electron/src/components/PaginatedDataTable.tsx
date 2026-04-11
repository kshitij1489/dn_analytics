import { useEffect, useState } from 'react';
import { formatColumnHeader } from '../utils';
import { exportToCSV } from '../utils/csv';
import { CustomerLink } from './CustomerLink';
import { ResizableTableWrapper } from './ResizableTableWrapper';

interface PaginatedDataTableProps {
    title: string;
    apiCall: (params: any) => Promise<any>;
    defaultSort?: string;
    lastDbSync?: number;
    leftContent?: React.ReactNode;
}

const DISPLAY_COLUMNS: Record<string, string[]> = {
    Orders: [
        'order_id', 'petpooja_order_id', 'stream_id', 'event_id', 'aggregate_id', 'customer_id',
        'restaurant_id', 'created_on', 'order_type', 'order_from', 'sub_order_type',
        'order_from_id', 'order_status', 'biller', 'assignee', 'table_no', 'token_no', 'no_of_persons',
        'customer_invoice_id', 'core_total', 'tax_total', 'discount_total', 'delivery_charges',
        'packaging_charge', 'service_charge', 'round_off', 'total', 'comment'
    ],
    'Order Items': [
        'order_item_id', 'order_id', 'created_on', 'menu_item_id', 'variant_id', 'petpooja_itemid', 'itemcode',
        'name_raw', 'category_name', 'quantity', 'unit_price', 'total_price', 'tax_amount',
        'discount_amount', 'specialnotes', 'sap_code', 'vendoritemcode', 'match_confidence',
        'match_method'
    ],
    Customers: [
        'customer_id', 'customer_identity_key', 'name', 'name_normalized', 'phone', 'address',
        'gstin', 'first_order_date', 'last_order_date', 'total_orders', 'total_spent',
        'is_verified', 'created_at', 'updated_at'
    ],
    Restaurants: [
        'restaurant_id', 'petpooja_restid', 'name', 'address', 'contact_information',
        'is_active', 'created_at', 'updated_at'
    ],
    Taxes: [
        'order_tax_id', 'order_id', 'created_on', 'tax_title', 'tax_rate', 'tax_type', 'tax_amount'
    ],
    Discounts: [
        'order_discount_id', 'order_id', 'created_on', 'discount_title', 'discount_type', 'discount_rate',
        'discount_amount'
    ]
};

const SEARCH_PLACEHOLDERS: Record<string, string> = {
    Orders: 'Search orders...',
    'Order Items': 'Search order items...',
    Customers: 'Search customers...',
    Taxes: 'Search taxes...',
    Discounts: 'Search discounts...',
};

export function PaginatedDataTable({
    title,
    apiCall,
    defaultSort = 'created_at',
    lastDbSync,
    leftContent
}: PaginatedDataTableProps) {
    const [data, setData] = useState<any[]>([]);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState(defaultSort);
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
    const [searchInput, setSearchInput] = useState('');
    const [appliedSearch, setAppliedSearch] = useState('');

    useEffect(() => {
        const timeoutId = window.setTimeout(() => {
            setAppliedSearch(searchInput.trim());
            setPage(1);
        }, 300);

        return () => window.clearTimeout(timeoutId);
    }, [searchInput]);

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            try {
                const res = await apiCall({
                    page,
                    page_size: pageSize,
                    sort_by: sortKey,
                    sort_desc: sortDirection === 'desc',
                    search: appliedSearch || undefined,
                });
                setData(res.data.data);
                setTotal(res.data.total);
            } catch (error) {
                console.error(error);
            } finally {
                setLoading(false);
            }
        };

        load();
    }, [apiCall, appliedSearch, lastDbSync, page, pageSize, sortDirection, sortKey]);

    const handleSort = (key: string) => {
        if (sortKey === key) {
            setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
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

    const displayColumns = DISPLAY_COLUMNS[title] || (data.length > 0 ? Object.keys(data[0]) : []);
    const finalColumns = displayColumns.filter((col) => data.length === 0 || Object.prototype.hasOwnProperty.call(data[0], col));
    const searchPlaceholder = SEARCH_PLACEHOLDERS[title];
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const startRow = total > 0 ? (page - 1) * pageSize + 1 : 0;
    const endRow = total > 0 ? Math.min(page * pageSize, total) : 0;

    const headerLeftContent = (searchPlaceholder || leftContent) ? (
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
            {searchPlaceholder && (
                <input
                    placeholder={searchPlaceholder}
                    value={searchInput}
                    onChange={(e) => setSearchInput(e.target.value)}
                    style={{
                        padding: '8px',
                        width: '300px',
                        background: 'var(--input-bg)',
                        color: 'var(--text-color)',
                        border: '1px solid var(--border-color)',
                        borderRadius: '4px'
                    }}
                />
            )}
            {leftContent}
        </div>
    ) : undefined;

    const renderCell = (row: Record<string, any>, col: string) => {
        const value = row[col];

        if (typeof value === 'boolean') {
            return value ? '✅' : '❌';
        }

        if (title === 'Customers' && col === 'name' && row.customer_id && value) {
            return <CustomerLink customerId={row.customer_id} name={String(value)} />;
        }

        if (value === null || value === undefined || value === '') {
            return '-';
        }

        const stringValue = String(value);
        return stringValue.length > 100 ? `${stringValue.substring(0, 100)}...` : stringValue;
    };

    return (
        <div style={{ marginTop: 0 }}>
            <ResizableTableWrapper
                onExportCSV={() => exportToCSV(data, title.toLowerCase().replace(/\s+/g, '_'), finalColumns)}
                leftContent={headerLeftContent}
            >
                {loading ? (
                    <div style={{ padding: '16px' }}>Loading...</div>
                ) : (
                    <table className="standard-table">
                        <thead>
                            <tr>
                                {finalColumns.map((col) => (
                                    <th key={col} onClick={() => handleSort(col)}>
                                        {formatColumnHeader(col)}{renderSortIcon(col)}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {data.map((row, index) => (
                                <tr key={index}>
                                    {finalColumns.map((col) => (
                                        <td key={col}>{renderCell(row, col)}</td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </ResizableTableWrapper>

            <div style={{ marginTop: '10px', display: 'flex', gap: '10px', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                    <select
                        value={pageSize}
                        onChange={(e) => {
                            setPageSize(Number(e.target.value));
                            setPage(1);
                        }}
                        style={{ padding: '5px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--input-border)', borderRadius: '4px' }}
                    >
                        <option value={20}>20 per page</option>
                        <option value={25}>25 per page</option>
                        <option value={50}>50 per page</option>
                        <option value={100}>100 per page</option>
                        <option value={200}>200 per page</option>
                    </select>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                        Showing {startRow} - {endRow} of {total}
                    </span>
                </div>
                <div>
                    <button
                        disabled={page <= 1}
                        onClick={() => setPage((prev) => prev - 1)}
                        style={{ marginRight: '5px', padding: '5px 10px', cursor: page <= 1 ? 'not-allowed' : 'pointer' }}
                    >
                        &lt; Prev
                    </button>
                    <span>Page {page} of {totalPages}</span>
                    <button
                        disabled={total === 0 || page >= totalPages}
                        onClick={() => setPage((prev) => prev + 1)}
                        style={{ marginLeft: '5px', padding: '5px 10px', cursor: total === 0 || page >= totalPages ? 'not-allowed' : 'pointer' }}
                    >
                        Next &gt;
                    </button>
                </div>
            </div>
        </div>
    );
}
