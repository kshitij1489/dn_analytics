import { useState, useEffect, useRef, type CSSProperties } from 'react';
import { endpoints } from '../api';
import { CollapsibleCard, ErrorPopup, SingleStoreOnly, TabButton } from '../components';
import type { PopupMessage } from '../components';
import type {
    GlobalMenuCatalogResponse,
    GlobalMenuPreview,
    GlobalMenuPreviewReference,
    GlobalMenuResolutionContext,
    GlobalMenuStatus,
} from '../types/api';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';
import { formatColumnHeader } from '../utils';
import { useStore } from '../contexts/StoreContext';
import {
    canUseCanonicalMenuControls,
    globalMenuViewLabels,
    hasGlobalMenuCapability,
    hasGlobalMenuMutationCapability,
    hasGlobalMenuResolutionCapability,
    hasGlobalMenuSharedPosCatalogCapability,
    isGroupOwnedMenuReady,
} from '../globalMenuCapabilities';

// --- Shared Components ---

const Card = ({ children, title }: { children: React.ReactNode, title: React.ReactNode }) => (
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
    addon_rows?: number;
    addon_qty?: number;
    suggestion_id?: string | null;
    suggestion_name?: string | null;
    suggestion_type?: string | null;
    suggested_variant_id?: string | null;
    suggested_variant_name?: string | null;
    resolution_kind?: 'addon_gap' | 'unverified_mapping' | 'global_identity_gap' | null;
    is_verified?: boolean | number | null;
}

interface SuspectMapping {
    anomaly_id: number;
    order_item_id: string;
    baseline_core_key?: string | null;
    core_key: string;
    name_raw: string;
    is_addon: boolean | number;
    current_menu_item_id?: string | null;
    current_mapped_name?: string | null;
    current_variant_id?: string | null;
    current_variant_name?: string | null;
    affected_rows?: number;
    affected_qty?: number;
    seen_at?: string;
}

interface MergeHistoryEntry {
    merge_id: number;
    source_id: string;
    target_id: string;
    source_name: string;
    target_name?: string | null;
    merged_at: string;
    variant_assignments?: MergeHistoryVariantAssignment[];
    global_mutation_id?: string | null;
    global_menu_group_id?: string | null;
    history_id?: string;
    source_kind?: 'legacy_restaurant_event' | 'global_menu_event';
    event_type?: string;
    origin_restaurant_id?: string | null;
    actor?: string | null;
    is_undoable?: boolean;
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
    global_menu?: GlobalMenuPreview;
}

interface GlobalMenuResolutionCommit {
    applied?: {
        global_item_id?: string;
        global_variant_id?: string;
    };
}

const globalPreviewReference = (
    preview?: GlobalMenuPreview,
): GlobalMenuPreviewReference => preview ? ({
    global_mutation_id: preview.mutation_id,
    global_preview_digest: preview.preview_digest,
    global_menu_group_id: preview.menu_group_id,
    global_preview_revision: preview.menu_group_revision,
    global_coverage_complete: preview.coverage_complete,
    global_conflicts: preview.conflicts,
    global_mutation_type: preview.mutation_type,
    global_mutation_payload: preview.payload,
}) : {};

const confirmGlobalImpact = (preview: GlobalMenuPreview, operation: string): boolean => {
    const conflictCount = preview.conflicts?.length ?? 0;
    if (!preview.commit_allowed) {
        window.alert(
            `Global menu commit is blocked for group ${preview.menu_group_id}. ` +
            `${conflictCount ? `${conflictCount} conflict${conflictCount === 1 ? '' : 's'} require resolution. ` : ''}` +
            `${preview.coverage_complete ? '' : 'Identity coverage is incomplete.'}`,
        );
        return false;
    }
    const affected = preview.impact?.restaurants?.length ?? preview.impact?.affected_restaurants;
    const assignments = preview.impact?.totals?.assignments ?? preview.impact?.existing_assignments;
    const ruleChanges = preview.impact?.totals?.mapping_rules ?? preview.impact?.mapping_rule_changes;
    const reconciliation = preview.payload?.variant_reconciliation ?? preview.impact?.variant_reconciliation;
    const reconciliationLabel = Array.isArray(reconciliation)
        ? `${reconciliation.length} variant reconciliation${reconciliation.length === 1 ? '' : 's'}`
        : reconciliation != null
            ? 'variant reconciliation included'
            : null;
    const summary = [
        affected != null ? `${affected} restaurant${affected === 1 ? '' : 's'}` : null,
        assignments != null ? `${assignments} existing assignment${assignments === 1 ? '' : 's'}` : null,
        ruleChanges != null ? `${ruleChanges} mapping-rule change${ruleChanges === 1 ? '' : 's'}` : null,
        reconciliationLabel,
        conflictCount ? `${conflictCount} conflict${conflictCount === 1 ? '' : 's'}` : null,
    ].filter(Boolean).join(', ');
    return window.confirm(
        `${operation} affects every restaurant in menu group ${preview.menu_group_id}` +
        `${summary ? ` (${summary})` : ''}. Continue?`,
    );
};

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
    price: number | string;
    is_active?: boolean;
    addon_eligible?: boolean;
    delivery_eligible?: boolean;
    is_verified?: boolean | number;
    menu_item_id?: string;
    variant_id?: string;
    mapping_count?: number;
    order_count?: number;
    rule_id?: string;
    locator_type?: 'pos_item' | 'pos_addon';
    locator_value?: string;
    global_menu_item_id?: string;
    global_variant_id?: string | null;
    unit?: string | null;
    value?: number | string | null;
    server_revision?: number;
}

const getApiErrorMessage = (error: unknown): string => {
    const err = error as {
        response?: { data?: { detail?: string | Record<string, unknown> } };
        message?: string;
    };
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') {
        return detail;
    }
    if (detail && typeof detail === 'object' && (
        typeof detail.message === 'string' || typeof detail.error === 'string'
    )) {
        const parts = [String(detail.message || detail.error)];
        const attribution = detail.attribution;
        if (Array.isArray(attribution) && attribution.length > 0) {
            const labels = attribution.map((entry) => {
                if (!entry || typeof entry !== 'object') return '';
                const employee = (entry as { employee?: { name?: string } }).employee;
                const device = (entry as {
                    device?: {
                        device_label?: string;
                        device_name?: string;
                        install_id?: string;
                    };
                }).device;
                const by = employee?.name;
                const from = device?.device_label || device?.device_name || device?.install_id;
                if (by && from) return `${by} (${from})`;
                return by || from || '';
            }).filter(Boolean);
            if (labels.length > 0) {
                parts.push(`Changed by: ${labels.join(', ')}`);
            }
        }
        return parts.join(' ');
    }
    return err.message || 'Something went wrong';
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
                    {assignment.source_variant_id === assignment.target_variant_id ? (
                        <>Variant: <span style={{ color: '#60A5FA' }}>{assignment.target_variant_name}</span></>
                    ) : (
                        <>
                            Variant: <span style={{ color: '#F59E0B' }}>{assignment.source_variant_name}</span>
                            {' → '}
                            <span style={{ color: '#60A5FA' }}>{assignment.target_variant_name}</span>
                        </>
                    )}
                </div>
            ))}
        </div>
    );
};

const isVerifyInPlaceEntry = (entry: MergeHistoryEntry) =>
    entry.source_id === entry.target_id &&
    (entry.variant_assignments || []).every(assignment => assignment.source_variant_id === assignment.target_variant_id);

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

const isUnverifiedFlag = (value?: boolean | number | null) => value === false || value === 0;

const verificationBadgeStyle: CSSProperties = {
    marginLeft: '8px',
    fontSize: '0.75em',
    fontWeight: 600,
    color: '#F59E0B',
    background: 'rgba(245, 158, 11, 0.12)',
    padding: '2px 8px',
    borderRadius: '999px',
    whiteSpace: 'nowrap',
};

const addonGapBadgeStyle: CSSProperties = {
    ...verificationBadgeStyle,
    color: '#8B5CF6',
    background: 'rgba(139, 92, 246, 0.12)',
};

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
const HISTORY_PAGE_SIZE = 20;

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
    const { isAllStores } = useStore();
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
                                    {!isAllStores && (
                                        <th onClick={() => handleSort('menu_item_id')}>Menu Item ID{renderSortIcon('menu_item_id')}</th>
                                    )}
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
                                        {!isAllStores && (
                                            <td style={{ fontSize: '0.8em', color: 'var(--text-secondary)' }}>{row["menu_item_id"]}</td>
                                        )}
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

// --- Group Catalog Tab ---

function GroupCatalogTab({ lastDbSync }: { lastDbSync?: number }) {
    const [catalog, setCatalog] = useState<GlobalMenuCatalogResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [popup, setPopup] = useState<PopupMessage | null>(null);

    useEffect(() => {
        let cancelled = false;
        endpoints.menu.globalCatalog()
            .then(response => {
                if (!cancelled) setCatalog(response.data);
            })
            .catch(error => {
                if (!cancelled) setPopup({ type: 'error', message: getApiErrorMessage(error) });
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => { cancelled = true; };
    }, [lastDbSync]);

    if (loading) return <div>Loading group catalog...</div>;

    return (
        <div>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>
                Canonical items and variants are owned by menu group <b>{catalog?.menu_group_id}</b>.
                Store sales and availability remain on the restaurant-specific analytics tabs.
            </p>
            <Card title={`Group Items (${catalog?.items.length || 0})`}>
                <div style={{ overflowX: 'auto' }}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th>Name</th><th>Type</th><th className="text-center">POS Rules</th>
                                <th className="text-center">Verified</th><th>Global ID</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(catalog?.items || []).map(item => (
                                <tr key={item.global_menu_item_id}>
                                    <td>{item.canonical_name}</td>
                                    <td>{item.canonical_type || '—'}</td>
                                    <td className="text-center">{item.active_pos_rules}</td>
                                    <td className="text-center">{item.is_verified ? '✅' : '❌'}</td>
                                    <td><code>{item.global_menu_item_id}</code></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </Card>
            <Card title={`Group Variants (${catalog?.variants.length || 0})`}>
                <div style={{ overflowX: 'auto' }}>
                    <table className="standard-table">
                        <thead>
                            <tr>
                                <th>Name</th><th>Unit</th><th>Value</th>
                                <th className="text-center">Verified</th><th>Global ID</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(catalog?.variants || []).map(variant => (
                                <tr key={variant.global_variant_id}>
                                    <td>{variant.canonical_name}</td>
                                    <td>{variant.unit || '—'}</td>
                                    <td>{variant.value ?? '—'}</td>
                                    <td className="text-center">{variant.is_verified ? '✅' : '❌'}</td>
                                    <td><code>{variant.global_variant_id}</code></td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </Card>
        </div>
    );
}

// --- Variants Tab ---

function VariantsTab({ lastDbSync }: { lastDbSync?: number }) {
    const { isAllStores, selectedStore } = useStore();
    const globalMenuAdvertised = hasGlobalMenuResolutionCapability(selectedStore);
    const [data, setData] = useState<any[]>([]);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('variant_name');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
    const [popup, setPopup] = useState<PopupMessage | null>(null);
    const [showAddForm, setShowAddForm] = useState(false);
    const [newVariantName, setNewVariantName] = useState('');
    const [newVariantDescription, setNewVariantDescription] = useState('');
    const [newVariantUnit, setNewVariantUnit] = useState('');
    const [newVariantValue, setNewVariantValue] = useState('');
    const [addSubmitting, setAddSubmitting] = useState(false);

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

    const displayColumns = isAllStores
        ? ['variant_name', 'description', 'unit', 'value', 'is_verified']
        : ['variant_id', 'variant_name', 'description', 'unit', 'value', 'is_verified', 'created_at', 'updated_at'];

    const resetAddForm = () => {
        setNewVariantName('');
        setNewVariantDescription('');
        setNewVariantUnit('');
        setNewVariantValue('');
    };

    const handleAddVariantType = async () => {
        const trimmedName = newVariantName.trim();
        if (!trimmedName) {
            setPopup({ type: 'error', message: 'Variant type name is required.' });
            return;
        }
        if (newVariantValue.trim() && Number.isNaN(Number(newVariantValue))) {
            setPopup({ type: 'error', message: 'Value must be a number.' });
            return;
        }

        setAddSubmitting(true);
        try {
            let globalPreview: GlobalMenuPreview | undefined;
            if (globalMenuAdvertised) {
                const previewResponse = await endpoints.menu.globalLocalPreview({
                    mutation_type: 'variant_create',
                    details: {
                        canonical_name: trimmedName,
                        description: newVariantDescription.trim() || null,
                        unit: newVariantUnit || null,
                        value: newVariantValue.trim() ? Number(newVariantValue) : null,
                    },
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Creating this canonical variant')) return;
            }
            const res = await endpoints.menu.variantsCreate({
                variant_name: trimmedName,
                description: newVariantDescription.trim() || undefined,
                unit: newVariantUnit || undefined,
                value: newVariantValue.trim() ? Number(newVariantValue) : undefined,
                ...globalPreviewReference(globalPreview),
            });
            const created = res.data;
            const metaSummary = created.unit
                ? ` (unit: ${created.unit}, value: ${created.value ?? '-'})`
                : '';
            setPopup({ type: 'success', message: `${created.message || 'Variant type created.'}${metaSummary}` });
            resetAddForm();
            setShowAddForm(false);
            await load();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setAddSubmitting(false);
        }
    };

    const addInputStyle: CSSProperties = {
        padding: '8px',
        background: 'var(--input-bg)',
        color: 'var(--text-color)',
        border: '1px solid var(--input-border)',
        borderRadius: '6px',
    };

    return (
        <div style={{ marginTop: '20px' }}>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
                <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>Variants</h3>
                {!isAllStores && (
                    <button
                        onClick={() => setShowAddForm(prev => !prev)}
                        style={{ padding: '8px 14px', background: showAddForm ? 'var(--card-bg)' : '#2563EB', color: showAddForm ? 'var(--text-color)' : 'white', border: showAddForm ? '1px solid var(--border-color)' : 'none', cursor: 'pointer', borderRadius: '8px', fontWeight: 700 }}
                    >
                        {showAddForm ? 'Cancel' : '+ Add Variant Type'}
                    </button>
                )}
            </div>
            {!isAllStores && showAddForm && (
                <div style={{ background: 'var(--card-bg)', padding: '16px', borderRadius: '12px', marginBottom: '15px', border: '1px solid var(--border-color)' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(200px, 2fr) minmax(200px, 2fr) minmax(120px, 1fr) minmax(120px, 1fr)', gap: '10px', alignItems: 'end' }}>
                        <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                            Name *
                            <input
                                value={newVariantName}
                                onChange={e => setNewVariantName(e.target.value)}
                                placeholder="e.g. FAMILY_TUB_725ML"
                                style={addInputStyle}
                            />
                        </label>
                        <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                            Description
                            <input
                                value={newVariantDescription}
                                onChange={e => setNewVariantDescription(e.target.value)}
                                placeholder="Optional"
                                style={addInputStyle}
                            />
                        </label>
                        <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                            Unit
                            <select
                                value={newVariantUnit}
                                onChange={e => setNewVariantUnit(e.target.value)}
                                style={addInputStyle}
                            >
                                <option value="">Auto-detect</option>
                                <option value="ML">ML</option>
                                <option value="GMS">GMS</option>
                                <option value="COUNT">COUNT</option>
                            </select>
                        </label>
                        <label style={{ display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85em', color: 'var(--text-secondary)' }}>
                            Value
                            <input
                                value={newVariantValue}
                                onChange={e => setNewVariantValue(e.target.value)}
                                placeholder="Auto-detect"
                                inputMode="decimal"
                                style={addInputStyle}
                            />
                        </label>
                    </div>
                    <p style={{ margin: '10px 0 12px', fontSize: '0.8em', color: 'var(--text-secondary)' }}>
                        The name is normalized to UPPER_SNAKE_CASE (e.g. "family tub 725ml" becomes FAMILY_TUB_725ML) so menu item
                        clustering reuses this variant type instead of creating a duplicate. Unit and value are auto-detected from the
                        name when left blank.
                    </p>
                    <button
                        onClick={handleAddVariantType}
                        disabled={addSubmitting || !newVariantName.trim()}
                        style={{ padding: '10px 16px', background: '#44aa44', color: 'white', border: 'none', cursor: addSubmitting || !newVariantName.trim() ? 'not-allowed' : 'pointer', borderRadius: '8px', fontWeight: 700, opacity: addSubmitting || !newVariantName.trim() ? 0.6 : 1 }}
                    >
                        {addSubmitting ? 'Creating...' : 'Create Variant Type'}
                    </button>
                </div>
            )}
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

function MatrixTab({
    lastDbSync,
    groupOwnedReady = false,
}: {
    lastDbSync?: number;
    groupOwnedReady?: boolean;
}) {
    const { isAllStores, selectedStore } = useStore();
    const globalMenuAdvertised = hasGlobalMenuMutationCapability(selectedStore);
    const globalMenuGroupAdvertised = hasGlobalMenuCapability(selectedStore);
    const canonicalControlsEnabled = canUseCanonicalMenuControls(selectedStore);
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
    const [matrixMode, setMatrixMode] = useState<'merge' | 'edit' | 'retype'>('merge');
    const [editTargetName, setEditTargetName] = useState('');
    const [editTargetVariantId, setEditTargetVariantId] = useState('');
    const [updating, setUpdating] = useState(false);
    const [menuTypes, setMenuTypes] = useState<string[]>([]);
    const [retypeTargetType, setRetypeTargetType] = useState('');
    const [retyping, setRetyping] = useState(false);
    const [priceDrafts, setPriceDrafts] = useState<Record<string, string>>({});
    const [updatingPriceRuleId, setUpdatingPriceRuleId] = useState<string | null>(null);

    // Client-Side Table State
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(50);
    const [sortKey, setSortKey] = useState('name');
    const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');
    const [search, setSearch] = useState('');

    useEffect(() => {
        void refreshData();
    }, [lastDbSync, isAllStores, selectedStore?.restaurant_id, groupOwnedReady]);

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
                    target_variant_id: targetVariantId || undefined,
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
    }, [sourceMenuItemId, sourceVariantId, targetMenuItemId, targetVariantId]);

    const refreshData = async () => {
        try {
            if (isAllStores) {
                const matrixRes = await endpoints.menu.matrix();
                setItems([]);
                setVariants([]);
                setMatrixData(matrixRes.data);
                setMergeHistory([]);
                setMenuTypes([]);
                return;
            }
            const [itemsRes, variantsRes, matrixRes, historyRes, typesRes] = await Promise.all([
                endpoints.menu.list(),
                endpoints.menu.variantsList(),
                groupOwnedReady ? endpoints.menu.globalMatrix() : endpoints.menu.matrix(),
                endpoints.menu.mergeHistory(),
                endpoints.menu.types(),
            ]);
            setItems(itemsRes.data);
            setVariants(variantsRes.data);
            setMatrixData(matrixRes.data);
            setMergeHistory(historyRes.data.entries);
            setMenuTypes(typesRes.data);
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
                count: row.mapping_count || 1,
            }))
            .sort((a, b) => a.variant_name.localeCompare(b.variant_name))
    );

    const handlePrefill = (row: MatrixRow, side: 'source' | 'target') => {
        if (side === 'source') {
            setSourceMenuItemId(row.menu_item_id || '');
            setSourceVariantId(row.variant_id || '');
            return;
        }
        if (matrixMode === 'edit') {
            setEditTargetName(row.name);
            setEditTargetVariantId(row.variant_id || '');
            return;
        }
        if (matrixMode === 'retype') {
            setRetypeTargetType(row.type);
            return;
        }
        setTargetMenuItemId(row.menu_item_id || '');
        setTargetVariantId(row.variant_id || '');
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
        const globalPreview = mergePreview?.global_menu;
        if (globalPreview && !globalPreview.commit_allowed) {
            setPopup({
                type: 'error',
                message: 'Global menu commit is blocked until identity coverage is complete and preview conflicts are resolved.',
            });
            return;
        }
        if (globalPreview && !confirmGlobalImpact(globalPreview, 'This menu merge')) return;

        try {
            setMerging(true);
            const res = await endpoints.menu.resolve({
                source_menu_item_id: sourceMenuItemId,
                source_variant_id: sourceVariantId,
                target_menu_item_id: targetMenuItemId,
                target_variant_id: targetVariantId,
                ...globalPreviewReference(globalPreview),
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

    const handleUpdate = async () => {
        const newName = editTargetName.trim();
        if (!sourceMenuItemId || !sourceVariantId || !newName || !editTargetVariantId) {
            setPopup({ type: 'error', message: 'Select source item + variant, enter a target item name, and select a target variant before updating.' });
            return;
        }
        const sourceItem = items.find(item => item.menu_item_id === sourceMenuItemId);
        if (!sourceItem) {
            setPopup({ type: 'error', message: 'Source menu item was not found. Refresh and try again.' });
            return;
        }
        if (
            editTargetVariantId === sourceVariantId &&
            newName.toLowerCase() === sourceItem.name.trim().toLowerCase()
        ) {
            setPopup({ type: 'error', message: 'New values match the current pair. Change the item name or variant before updating.' });
            return;
        }

        try {
            setUpdating(true);
            let globalPreview: GlobalMenuPreview | undefined;
            if (globalMenuAdvertised) {
                const previewResponse = await endpoints.menu.globalLocalPreview({
                    mutation_type: 'rename',
                    source_local_menu_item_id: sourceMenuItemId,
                    source_local_variant_id: sourceVariantId,
                    target_local_menu_item_id: sourceMenuItemId,
                    target_local_variant_id: editTargetVariantId,
                    details: { canonical_name: newName, canonical_type: sourceItem.type },
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Renaming this canonical menu pair')) return;
            }
            const res = await endpoints.menu.resolve({
                source_menu_item_id: sourceMenuItemId,
                source_variant_id: sourceVariantId,
                new_name: newName,
                new_type: sourceItem.type,
                target_variant_id: editTargetVariantId,
                ...globalPreviewReference(globalPreview),
            });
            setPopup({ type: 'success', message: res.data.message || 'Menu item + variant updated successfully.' });
            setSourceMenuItemId('');
            setSourceVariantId('');
            setEditTargetName('');
            setEditTargetVariantId('');
            await refreshData();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUpdating(false);
        }
    };

    const handleRetype = async () => {
        if (!sourceMenuItemId || !retypeTargetType) {
            setPopup({ type: 'error', message: 'Select a source menu item and a target type before updating.' });
            return;
        }
        const sourceItem = items.find(item => item.menu_item_id === sourceMenuItemId);
        if (!sourceItem) {
            setPopup({ type: 'error', message: 'Source menu item was not found. Refresh and try again.' });
            return;
        }
        if (retypeTargetType === sourceItem.type) {
            setPopup({ type: 'error', message: 'Target type matches the current type. Choose a different target type.' });
            return;
        }

        try {
            setRetyping(true);
            let globalPreview: GlobalMenuPreview | undefined;
            if (globalMenuAdvertised) {
                const previewResponse = await endpoints.menu.globalLocalPreview({
                    mutation_type: 'retype',
                    source_local_menu_item_id: sourceMenuItemId,
                    details: { canonical_type: retypeTargetType },
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Retyping this canonical menu item')) return;
            }
            const res = await endpoints.menu.retype({
                menu_item_id: sourceMenuItemId,
                new_type: retypeTargetType,
                ...globalPreviewReference(globalPreview),
            });
            setPopup({ type: 'success', message: res.data.message || 'Menu item type updated successfully.' });
            setSourceMenuItemId('');
            setSourceVariantId('');
            setRetypeTargetType('');
            await refreshData();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setRetyping(false);
        }
    };

    const handleUndo = async (mergeId: number) => {
        const entry = mergeHistory.find(candidate => candidate.merge_id === mergeId);
        let globalPreview: GlobalMenuPreview | undefined;
        if (globalMenuAdvertised) {
            if (!entry?.global_mutation_id) {
                setPopup({
                    type: 'error',
                    message: 'This legacy history row has no global mutation identity and cannot be undone in global mode.',
                });
                return;
            }
            try {
                const previewResponse = await endpoints.menu.globalPreview({
                    mutation_type: 'global_menu.undo',
                    payload: { undo_mutation_id: entry.global_mutation_id },
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Undoing this global menu change')) return;
            } catch (error) {
                setPopup({ type: 'error', message: getApiErrorMessage(error) });
                return;
            }
        } else if (!window.confirm('Undo this merge?')) return;

        setUndoingMergeId(mergeId);
        try {
            await endpoints.menu.undoMerge({
                merge_id: mergeId,
                ...globalPreviewReference(globalPreview),
            });
            setPopup({ type: 'success', message: 'Merge undone successfully.' });
            await refreshData();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUndoingMergeId(null);
        }
    };

    const handlePriceUpdate = async (row: MatrixRow) => {
        if (!row.rule_id || !row.locator_type || !row.locator_value) return;
        const rawPrice = (priceDrafts[row.rule_id] ?? String(row.price)).trim();
        if (!/^(?:0|[1-9]\d{0,7})(?:\.\d{1,2})?$/.test(rawPrice)) {
            setPopup({ type: 'error', message: 'Price must be from 0.00 to 99999999.99 with at most two decimals.' });
            return;
        }
        const [whole, fraction = ''] = rawPrice.split('.');
        const price = `${whole}.${fraction.padEnd(2, '0')}`;
        setUpdatingPriceRuleId(row.rule_id);
        try {
            const previewResponse = await endpoints.menu.globalPreview({
                mutation_type: 'global_locator.price_update',
                payload: {
                    locator_type: row.locator_type,
                    locator_value: row.locator_value,
                    price,
                },
            });
            const preview = previewResponse.data;
            if (!confirmGlobalImpact(preview, `Changing the shared catalog price to ₹${price}`)) return;
            await endpoints.menu.globalCommit(globalPreviewReference(preview));
            setPopup({ type: 'success', message: `Group price updated to ₹${price}.` });
            setPriceDrafts(previous => {
                const next = { ...previous };
                delete next[row.rule_id!];
                return next;
            });
            await refreshData();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUpdatingPriceRuleId(null);
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
    const trimmedEditName = editTargetName.trim();
    const selectedEditVariant = variants.find(variant => variant.variant_id === editTargetVariantId);
    const isUnchangedEdit = Boolean(
        selectedSourceItem &&
        editTargetVariantId &&
        editTargetVariantId === sourceVariantId &&
        trimmedEditName.toLowerCase() === selectedSourceItem.name.trim().toLowerCase()
    );
    const isUnchangedRetype = Boolean(
        selectedSourceItem &&
        retypeTargetType &&
        retypeTargetType === selectedSourceItem.type
    );
    const retypeExistingTarget = (selectedSourceItem && retypeTargetType && !isUnchangedRetype)
        ? items.find(item =>
            item.menu_item_id !== selectedSourceItem.menu_item_id &&
            item.type === retypeTargetType &&
            item.name.trim().toLowerCase() === selectedSourceItem.name.trim().toLowerCase()
        )
        : undefined;
    const normalizedSearch = search.trim().toLowerCase();
    const filteredMatrixData = matrixData.filter(row =>
        row.name.toLowerCase().includes(normalizedSearch)
    );

    // --- Client Side Sorting & Pagination Logic ---
    const getProcessedData = () => {
        const sorted = [...filteredMatrixData];
        if (sortKey) {
            sorted.sort((a, b) => {
                let aVal = (a[sortKey as keyof MatrixRow] ?? '') as string | number | boolean;
                let bVal = (b[sortKey as keyof MatrixRow] ?? '') as string | number | boolean;
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
            <SingleStoreOnly what="Menu changes">
            {canonicalControlsEnabled ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.45fr) minmax(300px, 1fr)', gap: '20px' }}>
                <CollapsibleCard title="Merge Menu Item + Variant" defaultCollapsed>
                    <div className="segmented-control" style={{ marginBottom: '15px' }}>
                        <TabButton active={matrixMode === 'merge'} onClick={() => setMatrixMode('merge')} variant="segmented">Merge</TabButton>
                        <TabButton active={matrixMode === 'edit'} onClick={() => setMatrixMode('edit')} variant="segmented">Edit Name/Variant</TabButton>
                        <TabButton active={matrixMode === 'retype'} onClick={() => setMatrixMode('retype')} variant="segmented">Edit Type</TabButton>
                    </div>
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
                            {matrixMode === 'retype' ? (
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Source Type</span>
                                <select
                                    value={selectedSourceItem ? selectedSourceItem.type : ''}
                                    disabled
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', opacity: 0.7 }}
                                >
                                    <option value="">Select a source menu item first</option>
                                    {menuTypes.map(type => (
                                        <option key={type} value={type}>{type}</option>
                                    ))}
                                </select>
                            </label>
                            ) : (
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
                            )}
                        </div>
                        {matrixMode === 'merge' ? (
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
                        ) : matrixMode === 'edit' ? (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Target Menu Item</span>
                                <input
                                    type="text"
                                    value={editTargetName}
                                    onChange={e => setEditTargetName(e.target.value)}
                                    placeholder="Enter new menu item name"
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                                />
                            </label>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Target Variant</span>
                                <select
                                    value={editTargetVariantId}
                                    onChange={e => setEditTargetVariantId(e.target.value)}
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                                >
                                    <option value="">Select target variant</option>
                                    {variants.map(variant => (
                                        <option key={variant.variant_id} value={variant.variant_id}>
                                            {variant.name}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        </div>
                        ) : (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Target Menu Item</span>
                                <select
                                    value={sourceMenuItemId}
                                    disabled
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)', opacity: 0.7 }}
                                >
                                    <option value="">Same as source menu item</option>
                                    {sourceSelectableItems.map(item => (
                                        <option key={item.menu_item_id} value={item.menu_item_id}>
                                            {item.name} ({item.type})
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <label style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                                <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>Target Type</span>
                                <select
                                    value={retypeTargetType}
                                    onChange={e => setRetypeTargetType(e.target.value)}
                                    disabled={!sourceMenuItemId}
                                    style={{ padding: '8px', background: 'var(--input-bg)', color: 'var(--text-color)', border: '1px solid var(--border-color)' }}
                                >
                                    <option value="">Select target type</option>
                                    {menuTypes.map(type => (
                                        <option key={type} value={type}>
                                            {type}{selectedSourceItem && type === selectedSourceItem.type ? ' (current)' : ''}
                                        </option>
                                    ))}
                                </select>
                            </label>
                        </div>
                        )}
                    </div>

                    {(selectedSourceItem || (
                        matrixMode === 'merge'
                            ? Boolean(selectedTargetItem)
                            : matrixMode === 'edit'
                                ? Boolean(trimmedEditName || editTargetVariantId)
                                : Boolean(retypeTargetType)
                    )) && (
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '12px', marginTop: '15px' }}>
                            <div style={{ padding: '12px', borderRadius: '8px', background: 'var(--input-bg)', color: 'var(--text-secondary)' }}>
                                <div style={{ fontSize: '0.8em', fontWeight: 700, color: '#EF4444', marginBottom: '6px' }}>Source</div>
                                {selectedSourceItem ? (
                                    <>
                                        <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>{selectedSourceItem.name}</div>
                                        <div>{selectedSourceItem.type}</div>
                                        <div>
                                            {matrixMode === 'retype'
                                                ? 'All variants'
                                                : (selectedSourceVariant ? selectedSourceVariant.variant_name : 'Select a source variant')}
                                        </div>
                                    </>
                                ) : (
                                    <div>{matrixMode === 'retype' ? 'Select a source menu item.' : 'Select a source menu item + variant.'}</div>
                                )}
                            </div>
                            <div style={{ padding: '12px', borderRadius: '8px', background: 'var(--input-bg)', color: 'var(--text-secondary)' }}>
                                <div style={{ fontSize: '0.8em', fontWeight: 700, color: '#10B981', marginBottom: '6px' }}>Target</div>
                                {matrixMode === 'merge' ? (
                                    selectedTargetItem ? (
                                        <>
                                            <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>{selectedTargetItem.name}</div>
                                            <div>{selectedTargetItem.type}</div>
                                            <div>{selectedTargetVariantLabel || 'Select a target variant'}</div>
                                        </>
                                    ) : (
                                        <div>Select a target menu item + variant.</div>
                                    )
                                ) : matrixMode === 'edit' ? (
                                    (trimmedEditName || editTargetVariantId) ? (
                                        <>
                                            <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>{trimmedEditName || 'Enter a new menu item name'}</div>
                                            <div>{selectedSourceItem ? selectedSourceItem.type : ''}</div>
                                            <div>{selectedEditVariant ? selectedEditVariant.name : 'Select a target variant'}</div>
                                        </>
                                    ) : (
                                        <div>Enter a new menu item name + select a target variant.</div>
                                    )
                                ) : (
                                    (selectedSourceItem && retypeTargetType) ? (
                                        <>
                                            <div style={{ color: 'var(--text-color)', fontWeight: 700 }}>{selectedSourceItem.name}</div>
                                            <div>{retypeTargetType}</div>
                                            <div>All variants</div>
                                        </>
                                    ) : (
                                        <div>Select a target type.</div>
                                    )
                                )}
                            </div>
                        </div>
                    )}

                    {matrixMode === 'merge' && (previewLoading || mergePreview) && (
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
                                    {mergePreview.global_menu && (
                                        <div style={{ color: '#F59E0B', fontSize: '0.9em', fontWeight: 700 }}>
                                            Global menu change: affects every restaurant in menu group {mergePreview.global_menu.menu_group_id}
                                            {mergePreview.global_menu.impact?.affected_restaurants != null
                                                ? ` (${mergePreview.global_menu.impact.affected_restaurants} restaurants)`
                                                : ''}.
                                        </div>
                                    )}
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

                    {matrixMode === 'merge' && isSameExactPair && (
                        <div style={{ marginTop: '12px', color: '#F59E0B', fontSize: '0.9em' }}>
                            Source and target pair are identical. Choose a different target variant or target item.
                        </div>
                    )}

                    {matrixMode === 'edit' && (
                        <div style={{ marginTop: '12px', color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                            Update renames the selected pair everywhere (cluster mappings, order history, analytics), keeps the item type, and syncs the change to the cloud. If the new pair already exists, the source pair is consolidated into it.
                        </div>
                    )}

                    {matrixMode === 'edit' && isUnchangedEdit && (
                        <div style={{ marginTop: '12px', color: '#F59E0B', fontSize: '0.9em' }}>
                            New values match the current pair. Change the item name or variant name before updating.
                        </div>
                    )}

                    {matrixMode === 'retype' && (
                        <div style={{ marginTop: '12px', color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                            Update changes the item's type everywhere (all variants, cluster mappings, order history, analytics), clears affected forecast caches, and syncs the change to the cloud. If an item with the same name and the target type already exists, their histories are consolidated.
                        </div>
                    )}

                    {matrixMode === 'retype' && isUnchangedRetype && (
                        <div style={{ marginTop: '12px', color: '#F59E0B', fontSize: '0.9em' }}>
                            Target type matches the current type. Choose a different target type before updating.
                        </div>
                    )}

                    {matrixMode === 'retype' && retypeExistingTarget && (
                        <div style={{ marginTop: '12px', color: '#F59E0B', fontSize: '0.9em' }}>
                            An item named '{retypeExistingTarget.name}' already exists with type '{retypeExistingTarget.type}'. Updating will consolidate both items' histories into it.
                        </div>
                    )}

                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '15px' }}>
                        {matrixMode === 'merge' ? (
                            <button
                                onClick={() => void handleMerge()}
                                disabled={merging || !sourceMenuItemId || !sourceVariantId || !targetMenuItemId || !targetVariantId || isSameExactPair || Boolean(mergePreview?.global_menu && !mergePreview.global_menu.commit_allowed)}
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
                        ) : matrixMode === 'edit' ? (
                            <button
                                onClick={() => void handleUpdate()}
                                disabled={updating || !sourceMenuItemId || !sourceVariantId || !trimmedEditName || !editTargetVariantId || isUnchangedEdit}
                                style={{
                                    background: '#2563EB',
                                    color: 'white',
                                    border: 'none',
                                    padding: '10px 16px',
                                    cursor: updating ? 'not-allowed' : 'pointer',
                                    opacity: updating || !sourceMenuItemId || !sourceVariantId || !trimmedEditName || !editTargetVariantId || isUnchangedEdit ? 0.7 : 1,
                                    borderRadius: '8px',
                                    fontWeight: 700,
                                }}
                            >
                                {updating ? 'Updating...' : 'Update'}
                            </button>
                        ) : (
                            <button
                                onClick={() => void handleRetype()}
                                disabled={retyping || !sourceMenuItemId || !retypeTargetType || isUnchangedRetype}
                                style={{
                                    background: '#2563EB',
                                    color: 'white',
                                    border: 'none',
                                    padding: '10px 16px',
                                    cursor: retyping ? 'not-allowed' : 'pointer',
                                    opacity: retyping || !sourceMenuItemId || !retypeTargetType || isUnchangedRetype ? 0.7 : 1,
                                    borderRadius: '8px',
                                    fontWeight: 700,
                                }}
                            >
                                {retyping ? 'Updating...' : 'Update'}
                            </button>
                        )}
                    </div>
                </CollapsibleCard>

                {!groupOwnedReady && <CollapsibleCard title="Recent Merge History" defaultCollapsed>
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
                </CollapsibleCard>}
            </div>
            ) : globalMenuGroupAdvertised ? (
                <Card title="Group-owned canonical menu">
                    <p style={{ margin: 0, color: 'var(--text-secondary)' }}>
                        Merge, rename, retype, variant-merge, undo and price controls remain locked while the
                        menu group is in shadow or aggregation review. Store Resolution stays available for
                        approved coverage repair.
                    </p>
                </Card>
            ) : null}
            </SingleStoreOnly>

            {/* Menu Matrix Table Container */}
            <div style={{ marginTop: '20px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', marginBottom: '15px', flexWrap: 'wrap' }}>
                    <h3 style={{ margin: 0, color: 'var(--accent-color)' }}>
                        Menu Matrix ({matrixData.length} unique pairs)
                    </h3>
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
                                {!isAllStores && canonicalControlsEnabled && <th>Action</th>}
                                <th onClick={() => handleSort('name')}>Item{renderSortIcon('name')}</th>
                                <th onClick={() => handleSort('type')}>Type{renderSortIcon('type')}</th>
                                <th onClick={() => handleSort('variant_name')}>Variant{renderSortIcon('variant_name')}</th>
                                {groupOwnedReady ? (
                                    <>
                                        <th>POS Kind</th>
                                        <th>POS Locator</th>
                                    </>
                                ) : (
                                    <>
                                        <th className="text-center" onClick={() => handleSort('mapping_count')}>Mappings{renderSortIcon('mapping_count')}</th>
                                        <th className="text-center" onClick={() => handleSort('order_count')}>Orders{renderSortIcon('order_count')}</th>
                                    </>
                                )}
                                <th className="text-right" onClick={() => handleSort('price')}>Price{renderSortIcon('price')}</th>
                                {!groupOwnedReady && <th className="text-center" onClick={() => handleSort('is_active')}>Active{renderSortIcon('is_active')}</th>}
                                {!groupOwnedReady && <th className="text-center" onClick={() => handleSort('addon_eligible')}>Addon{renderSortIcon('addon_eligible')}</th>}
                                {!groupOwnedReady && <th className="text-center" onClick={() => handleSort('delivery_eligible')}>Delivery{renderSortIcon('delivery_eligible')}</th>}
                                <th className="text-center" onClick={() => handleSort('is_verified')}>Verified{renderSortIcon('is_verified')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {displayData.map((r, i) => (
                                <tr key={i}>
                                    {!isAllStores && canonicalControlsEnabled && <td>
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
                                            {groupOwnedReady && globalMenuAdvertised && r.rule_id && (
                                                <button
                                                    onClick={() => void handlePriceUpdate(r)}
                                                    disabled={updatingPriceRuleId === r.rule_id}
                                                    style={{
                                                        color: '#2563EB', border: '1px solid rgba(37, 99, 235, 0.45)',
                                                        background: 'transparent', padding: '4px 8px', borderRadius: '6px',
                                                        cursor: updatingPriceRuleId === r.rule_id ? 'not-allowed' : 'pointer',
                                                    }}
                                                >
                                                    {updatingPriceRuleId === r.rule_id ? 'Saving…' : 'Save Price'}
                                                </button>
                                            )}
                                        </div>
                                    </td>}
                                    <td>
                                        <span>{r.name}</span>
                                        {isUnverifiedFlag(r.is_verified) && (
                                            <span style={verificationBadgeStyle}>Needs verification</span>
                                        )}
                                    </td>
                                    <td>{r.type}</td>
                                    <td>{r.variant_name}</td>
                                    {groupOwnedReady ? (
                                        <>
                                            <td>{r.locator_type === 'pos_addon' ? 'Addon' : 'Item'}</td>
                                            <td><code>{r.locator_value}</code></td>
                                        </>
                                    ) : (
                                        <>
                                    <td className="text-center">{r.mapping_count}</td>
                                    <td className="text-center">
                                        {(r.order_count || 0) > 0 ? r.order_count : (
                                            <span style={{ color: '#EF4444', fontWeight: 600 }} title="No order lines reference this item + variant in this install's data">0</span>
                                        )}
                                    </td>
                                        </>
                                    )}
                                    <td className="text-right">
                                        {groupOwnedReady && globalMenuAdvertised && r.rule_id ? (
                                            <input
                                                aria-label={`Price for ${r.locator_value}`}
                                                value={priceDrafts[r.rule_id] ?? String(r.price)}
                                                onChange={event => setPriceDrafts(previous => ({
                                                    ...previous,
                                                    [r.rule_id!]: event.target.value,
                                                }))}
                                                inputMode="decimal"
                                                style={{ width: '88px', textAlign: 'right', padding: '5px' }}
                                            />
                                        ) : `₹${r.price}`}
                                    </td>
                                    {!groupOwnedReady && <td className="text-center">{r.is_active ? "✅" : "❌"}</td>}
                                    {!groupOwnedReady && <td className="text-center">{r.addon_eligible ? "✅" : "❌"}</td>}
                                    {!groupOwnedReady && <td className="text-center">{r.delivery_eligible ? "✅" : "❌"}</td>}
                                    <td className="text-center">{isUnverifiedFlag(r.is_verified) ? "❌" : "✅"}</td>
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

const coreLabel = (coreKey?: string | null): string => {
    if (!coreKey) return '—';
    const parts = String(coreKey).split('|');
    return (parts.length > 1 ? parts.slice(1).join('|') : parts[0]).trim() || '—';
};

// Silent-reuse guard: PetPooja ids whose incoming name resolves to a different
// product than the id's verified mapping. See src/core/mapping_anomalies.py.
function SuspectMappingsCard({
    lookupItems,
    variantOptions,
    setPopup,
    onRemapped,
    lastDbSync,
}: {
    lookupItems: MenuLookupItem[];
    variantOptions: VariantOption[];
    setPopup: (p: PopupMessage | null) => void;
    onRemapped: () => void;
    lastDbSync?: number;
}) {
    const { selectedStore } = useStore();
    const globalMenuAdvertised = hasGlobalMenuResolutionCapability(selectedStore);
    const [suspects, setSuspects] = useState<SuspectMapping[]>([]);
    const [loading, setLoading] = useState(true);
    const [selection, setSelection] = useState<Record<number, { itemId: string; variantId: string }>>({});
    const [busyId, setBusyId] = useState<number | null>(null);

    const load = async () => {
        setLoading(true);
        try {
            const res = await endpoints.menu.suspectMappings();
            setSuspects(res.data);
        } catch (error) {
            setPopup({ type: 'error', message: `Failed to load suspect mappings. ${getApiErrorMessage(error)}` });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
    }, [lastDbSync]);

    const handleDismiss = async (anomalyId: number) => {
        setBusyId(anomalyId);
        try {
            await endpoints.menu.dismissSuspectMapping(anomalyId);
            setSuspects(prev => prev.filter(s => s.anomaly_id !== anomalyId));
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setBusyId(null);
        }
    };

    const handleRemap = async (row: SuspectMapping) => {
        const pick = selection[row.anomaly_id];
        if (!pick?.itemId || !pick?.variantId) {
            setPopup({ type: 'error', message: 'Choose a target item and variant before remapping.' });
            return;
        }
        setBusyId(row.anomaly_id);
        try {
            let globalPreview: GlobalMenuPreview | undefined;
            if (globalMenuAdvertised) {
                const previewResponse = await endpoints.menu.globalLocalPreview({
                    mutation_type: 'remap',
                    source_local_menu_item_id: row.current_menu_item_id || undefined,
                    source_local_variant_id: row.current_variant_id || undefined,
                    target_local_menu_item_id: pick.itemId,
                    target_local_variant_id: pick.variantId,
                    details: { restaurant_pos_assignment_key: row.order_item_id },
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Remapping this POS assignment')) return;
            }
            const res = await endpoints.menu.remap({
                order_item_id: row.order_item_id,
                new_menu_item_id: pick.itemId,
                new_variant_id: pick.variantId,
                ...globalPreviewReference(globalPreview),
            });
            setPopup({ type: 'success', message: res.data?.message || 'Order item remapped.' });
            setSuspects(prev => prev.filter(s => s.anomaly_id !== row.anomaly_id));
            onRemapped();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setBusyId(null);
        }
    };

    const verifiedItems = lookupItems.filter(i => i.is_verified);

    return (
        <CollapsibleCard
            title={`⚠️ Suspect Mappings${suspects.length ? ` (${suspects.length})` : ''}`}
            defaultCollapsed={suspects.length === 0}
        >
            <p style={{ marginTop: 0, color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                A PetPooja id here now carries a different product than its verified mapping — orders are being
                booked as the old product. Dismiss if it is only a relabel, or remap to the correct item.
            </p>
            {loading ? (
                <p style={{ color: 'var(--text-secondary)' }}>Loading…</p>
            ) : suspects.length === 0 ? (
                <p style={{ margin: 0, color: 'var(--text-secondary)' }}>No suspect mappings. 🎉</p>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px', maxHeight: '520px', overflowY: 'auto' }}>
                    {suspects.map(row => {
                        const pick = selection[row.anomaly_id] || { itemId: '', variantId: '' };
                        const busy = busyId === row.anomaly_id;
                        return (
                            <div
                                key={row.anomaly_id}
                                style={{ padding: '12px', border: '1px solid var(--border-color)', borderRadius: '10px' }}
                            >
                                <div style={{ color: 'var(--text-color)', marginBottom: '4px' }}>
                                    <span style={{ fontWeight: 700 }}>{row.name_raw}</span>
                                    {row.is_addon ? (
                                        <span style={{ marginLeft: 8, fontSize: '0.75em', color: 'var(--text-secondary)' }}>addon</span>
                                    ) : null}
                                </div>
                                <div style={{ fontSize: '0.88em', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                                    booked as <span style={{ color: '#EF4444' }}>{row.current_mapped_name || '—'}</span>
                                    {' · was '}<code>{coreLabel(row.baseline_core_key)}</code>
                                    {' → now '}<code>{coreLabel(row.core_key)}</code>
                                    {typeof row.affected_qty === 'number' && row.affected_qty > 0
                                        ? ` · ${row.affected_qty} sold under this label`
                                        : ''}
                                </div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center' }}>
                                    <input
                                        list={`suspect-items-${row.anomaly_id}`}
                                        placeholder="Correct item…"
                                        defaultValue=""
                                        onChange={e => {
                                            const match = verifiedItems.find(i => i.name === e.target.value);
                                            setSelection(prev => ({
                                                ...prev,
                                                [row.anomaly_id]: { ...pick, itemId: match ? match.menu_item_id : '' },
                                            }));
                                        }}
                                        style={{ padding: '8px', borderRadius: '6px', border: '1px solid var(--border-color)', minWidth: '200px' }}
                                    />
                                    <datalist id={`suspect-items-${row.anomaly_id}`}>
                                        {verifiedItems.map(i => (
                                            <option key={i.menu_item_id} value={i.name}>{i.type}</option>
                                        ))}
                                    </datalist>
                                    <select
                                        value={pick.variantId}
                                        onChange={e => setSelection(prev => ({
                                            ...prev,
                                            [row.anomaly_id]: { ...pick, variantId: e.target.value },
                                        }))}
                                        style={{ padding: '8px', borderRadius: '6px', border: '1px solid var(--border-color)' }}
                                    >
                                        <option value="">Variant…</option>
                                        {variantOptions.map(v => (
                                            <option key={v.variant_id} value={v.variant_id}>{v.name}</option>
                                        ))}
                                    </select>
                                    <button
                                        onClick={() => void handleRemap(row)}
                                        disabled={busy || !pick.itemId || !pick.variantId}
                                        style={{
                                            padding: '8px 14px', background: '#2563EB', color: 'white', border: 'none',
                                            borderRadius: '8px', cursor: busy ? 'not-allowed' : 'pointer',
                                            opacity: busy || !pick.itemId || !pick.variantId ? 0.6 : 1, fontWeight: 700,
                                        }}
                                    >
                                        {busy ? 'Working…' : 'Remap'}
                                    </button>
                                    <button
                                        onClick={() => void handleDismiss(row.anomaly_id)}
                                        disabled={busy}
                                        style={{
                                            padding: '8px 14px', background: '#444', color: 'white', border: 'none',
                                            borderRadius: '8px', cursor: busy ? 'not-allowed' : 'pointer',
                                        }}
                                    >
                                        Dismiss (relabel)
                                    </button>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </CollapsibleCard>
    );
}

function ResolutionsTab({
    lastDbSync,
    showHistory = true,
}: {
    lastDbSync?: number;
    showHistory?: boolean;
}) {
    const { selectedStore } = useStore();
    const globalMenuAdvertised = hasGlobalMenuMutationCapability(selectedStore);
    const globalResolutionAdvertised = hasGlobalMenuResolutionCapability(selectedStore);
    const globalResolutionOnly = globalResolutionAdvertised && !globalMenuAdvertised;
    const [items, setItems] = useState<ResolutionItem[]>([]);
    const [lookupItems, setLookupItems] = useState<MenuLookupItem[]>([]);
    const [variantOptions, setVariantOptions] = useState<VariantOption[]>([]);
    const [typeOptions, setTypeOptions] = useState<string[]>([]);
    const [mergeHistory, setMergeHistory] = useState<MergeHistoryEntry[]>([]);
    const [historyPage, setHistoryPage] = useState(1);
    const [historyTotal, setHistoryTotal] = useState(0);
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

    const loadTypeOptions = async () => {
        const res = await endpoints.menu.types();
        setTypeOptions(res.data);
        return res.data;
    };

    const loadHistory = async (page = historyPage) => {
        const res = await endpoints.menu.mergeHistory({
            limit: HISTORY_PAGE_SIZE,
            offset: (page - 1) * HISTORY_PAGE_SIZE,
        });
        setMergeHistory(res.data.entries);
        setHistoryTotal(res.data.total);
        // If the last item on the final page was undone, step back a page.
        const maxPage = Math.max(1, Math.ceil(res.data.total / HISTORY_PAGE_SIZE));
        if (page > maxPage) {
            setHistoryPage(maxPage);
        }
        return res.data.entries;
    };

    const removeResolvedItem = (menuItemId: string, sourceVariantId: string) => {
        setItems(prev => prev.filter(item =>
            !(item.menu_item_id === menuItemId && item.source_variant_id === sourceVariantId)
        ));
    };

    const refreshAll = async () => {
        setLoading(true);
        const results = await Promise.allSettled([loadItems(), loadLookupItems(), loadVariantOptions(), loadTypeOptions(), loadHistory()]);
        const failedRefreshes = results
            .map((result, index) => ({ result, label: ['items', 'lookup', 'variants', 'types', 'history'][index] }))
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

    const loadedHistoryPageRef = useRef(1);
    useEffect(() => {
        if (loadedHistoryPageRef.current === historyPage) return;
        loadedHistoryPageRef.current = historyPage;
        loadHistory(historyPage).catch(error => {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        });
    }, [historyPage]);

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

    const previewAndCommitResolutionAction = async (
        mutationType: string,
        payload: Record<string, unknown>,
        operation: string,
    ): Promise<GlobalMenuResolutionCommit | null> => {
        const previewResponse = await endpoints.menu.globalPreview({
            mutation_type: mutationType,
            payload,
        });
        const preview = previewResponse.data;
        if (!confirmGlobalImpact(preview, operation)) return null;
        const commitResponse = await endpoints.menu.globalCommit(
            globalPreviewReference(preview),
        );
        return commitResponse.data;
    };

    const loadGlobalResolutionContext = async (
        localMenuItemId: string,
        localVariantId?: string,
    ): Promise<GlobalMenuResolutionContext> => {
        const response = await endpoints.menu.globalResolutionContext({
            local_menu_item_id: localMenuItemId,
            local_variant_id: localVariantId,
        });
        return response.data;
    };

    const ensureGlobalResolutionItem = async (
        context: GlobalMenuResolutionContext,
        canonicalName: string,
        canonicalType: string,
    ): Promise<string | null> => {
        if (context.global_item_id) return context.global_item_id;
        const result = await previewAndCommitResolutionAction(
            'global_item.create',
            {
                canonical_name: canonicalName,
                canonical_type: canonicalType,
                is_verified: true,
            },
            `Creating “${canonicalName}” in the shared canonical menu`,
        );
        return result?.applied?.global_item_id || null;
    };

    const ensureGlobalResolutionVariant = async (
        context: GlobalMenuResolutionContext | null,
        fallbackName?: string,
    ): Promise<string | null> => {
        if (context?.global_variant_id) return context.global_variant_id;
        const canonicalName = context?.variant?.canonical_name || fallbackName?.trim();
        if (!canonicalName) return null;
        const result = await previewAndCommitResolutionAction(
            'global_variant.create',
            {
                canonical_name: canonicalName,
                dimension: context?.variant?.dimension || { unit: '', value: null },
            },
            `Creating the “${canonicalName}” canonical variant`,
        );
        return result?.applied?.global_variant_id || null;
    };

    const mapGlobalResolutionLocators = async (
        context: GlobalMenuResolutionContext,
        globalItemId: string,
        globalVariantId: string | null,
        label: string,
    ): Promise<boolean> => {
        if (!context.locators.length) {
            throw new Error('No trusted POS locator or approved alias is available for this resolution.');
        }
        for (const locator of context.locators) {
            const result = await previewAndCommitResolutionAction(
                'global_locator.map',
                {
                    ...locator,
                    global_item_id: globalItemId,
                    global_variant_id: globalVariantId || '',
                },
                `Mapping ${label} (${locator.locator_type}: ${locator.locator_value})`,
            );
            if (!result) return false;
        }
        return true;
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
            const sourceResolutionContext = globalResolutionAdvertised
                ? await loadGlobalResolutionContext(
                    modalItem.menu_item_id,
                    modalItem.source_variant_id,
                )
                : null;
            const useCoverageRepair = Boolean(
                sourceResolutionContext && (
                    globalResolutionOnly ||
                    !sourceResolutionContext.global_item_id ||
                    !sourceResolutionContext.global_variant_id
                ),
            );
            if (useCoverageRepair && sourceResolutionContext) {
                const sourceContext = sourceResolutionContext;
                const targetContext = selectedTargetVariant === '__new__'
                    ? await loadGlobalResolutionContext(selectedTargetId)
                    : await loadGlobalResolutionContext(selectedTargetId, selectedTargetVariant);
                const globalItemId = await ensureGlobalResolutionItem(
                    targetContext,
                    targetContext.canonical_name,
                    targetContext.canonical_type,
                );
                if (!globalItemId) return;
                const globalVariantId = await ensureGlobalResolutionVariant(
                    selectedTargetVariant === '__new__' ? null : targetContext,
                    selectedTargetVariant === '__new__'
                        ? (newVariantNames[selectedSourceVariant.variant_id] || '')
                        : undefined,
                );
                if (!globalVariantId) {
                    throw new Error('The target canonical variant could not be created or resolved.');
                }
                const targetNeedsMapping = !targetContext.global_item_id || (
                    selectedTargetVariant !== '__new__' && !targetContext.global_variant_id
                );
                if (targetNeedsMapping) {
                    const targetMapped = await mapGlobalResolutionLocators(
                        targetContext,
                        globalItemId,
                        selectedTargetVariant === '__new__' ? null : globalVariantId,
                        targetContext.canonical_name,
                    );
                    if (!targetMapped) return;
                }
                const sourceMapped = await mapGlobalResolutionLocators(
                    sourceContext,
                    globalItemId,
                    globalVariantId,
                    modalItem.display_name || modalItem.name,
                );
                if (!sourceMapped) return;
                removeResolvedItem(modalItem.menu_item_id, modalItem.source_variant_id);
                setPopup({ type: 'success', message: 'Global menu identity resolved successfully.' });
                closeResolutionModal();
                await refreshAll();
                return;
            }
            let globalPreview = mergePreview?.global_menu;
            if (globalMenuAdvertised) {
                const previewResponse = await endpoints.menu.globalLocalPreview({
                    mutation_type: 'variant_merge',
                    source_local_menu_item_id: modalItem.menu_item_id,
                    source_local_variant_id: modalItem.source_variant_id,
                    target_local_menu_item_id: selectedTargetId,
                    target_local_variant_id: selectedTargetVariant === '__new__'
                        ? undefined
                        : selectedTargetVariant,
                    details: selectedTargetVariant === '__new__'
                        ? { new_variant_name: (newVariantNames[selectedSourceVariant.variant_id] || '').trim() }
                        : undefined,
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Resolving this canonical menu pair')) return;
            }
            const res = await endpoints.menu.resolve({
                source_menu_item_id: modalItem.menu_item_id,
                source_variant_id: modalItem.source_variant_id,
                target_menu_item_id: selectedTargetId,
                target_variant_id: selectedTargetVariant === '__new__' ? undefined : selectedTargetVariant,
                new_variant_name: selectedTargetVariant === '__new__'
                    ? (newVariantNames[selectedSourceVariant.variant_id] || '').trim()
                    : undefined,
                ...globalPreviewReference(globalPreview),
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
            const sourceResolutionContext = globalResolutionAdvertised
                ? await loadGlobalResolutionContext(
                    modalItem.menu_item_id,
                    modalItem.source_variant_id,
                )
                : null;
            const useCoverageRepair = Boolean(
                sourceResolutionContext && (
                    globalResolutionOnly ||
                    !sourceResolutionContext.global_item_id ||
                    !sourceResolutionContext.global_variant_id
                ),
            );
            if (useCoverageRepair && sourceResolutionContext) {
                const sourceContext = sourceResolutionContext;
                const variantContext = await loadGlobalResolutionContext(
                    modalItem.menu_item_id,
                    renameVariantId,
                );
                if (sourceContext.global_item_id && (
                    sourceContext.canonical_name !== trimmedName ||
                    sourceContext.canonical_type !== trimmedType
                )) {
                    throw new Error(
                        'Renaming an existing canonical item requires active global-menu mutations. Choose its current canonical name/type or wait for activation.',
                    );
                }
                const globalItemId = await ensureGlobalResolutionItem(
                    sourceContext,
                    trimmedName,
                    trimmedType,
                );
                if (!globalItemId) return;
                const globalVariantId = await ensureGlobalResolutionVariant(variantContext);
                if (!globalVariantId) {
                    throw new Error('The selected canonical variant could not be created or resolved.');
                }
                const mapped = await mapGlobalResolutionLocators(
                    sourceContext,
                    globalItemId,
                    globalVariantId,
                    modalItem.display_name || modalItem.name,
                );
                if (!mapped) return;
                removeResolvedItem(modalItem.menu_item_id, modalItem.source_variant_id);
                setPopup({ type: 'success', message: 'Global menu identity resolved successfully.' });
                closeResolutionModal();
                await refreshAll();
                return;
            }
            let globalPreview: GlobalMenuPreview | undefined;
            if (globalMenuAdvertised) {
                const previewResponse = await endpoints.menu.globalLocalPreview({
                    mutation_type: 'verify_or_rename',
                    source_local_menu_item_id: modalItem.menu_item_id,
                    source_local_variant_id: modalItem.source_variant_id,
                    target_local_menu_item_id: modalItem.menu_item_id,
                    target_local_variant_id: renameVariantId,
                    details: { canonical_name: trimmedName, canonical_type: trimmedType },
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Verifying this canonical menu pair')) return;
            }
            const res = await endpoints.menu.resolve({
                source_menu_item_id: modalItem.menu_item_id,
                source_variant_id: modalItem.source_variant_id,
                new_name: trimmedName,
                new_type: trimmedType,
                target_variant_id: renameVariantId,
                ...globalPreviewReference(globalPreview),
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
        const entry = mergeHistory.find(candidate => candidate.merge_id === mergeId);
        let globalPreview: GlobalMenuPreview | undefined;
        if (globalMenuAdvertised) {
            if (!entry?.global_mutation_id) {
                setPopup({
                    type: 'error',
                    message: 'This legacy history row has no global mutation identity and cannot be undone in global mode.',
                });
                return;
            }
            try {
                const previewResponse = await endpoints.menu.globalPreview({
                    mutation_type: 'global_menu.undo',
                    payload: { undo_mutation_id: entry.global_mutation_id },
                });
                globalPreview = previewResponse.data;
                if (!confirmGlobalImpact(globalPreview, 'Undoing this global menu change')) return;
            } catch (error) {
                setPopup({ type: 'error', message: getApiErrorMessage(error) });
                return;
            }
        } else if (!window.confirm('Undo this resolution?')) return;

        setUndoingMergeId(mergeId);
        try {
            await endpoints.menu.undoMerge({
                merge_id: mergeId,
                ...globalPreviewReference(globalPreview),
            });
            setPopup({ type: 'success', message: 'Resolution undone successfully.' });
            await refreshAll();
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUndoingMergeId(null);
        }
    };

    // The item being resolved is always a valid target: picking it just moves this
    // source variant onto another variant type of the same item. It may not be
    // is_verified yet because sibling variants are still unresolved.
    const eligibleTargets = modalItem
        ? lookupItems.filter(candidate => candidate.is_verified || candidate.menu_item_id === modalItem.menu_item_id)
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
                Resolve each unclustered or globally unlinked menu item + variant pair by merging it into a canonical match, verifying it as a distinct pair, or manually renaming/searching for the right target.
            </p>
            <SuspectMappingsCard
                lookupItems={lookupItems}
                variantOptions={variantOptions}
                setPopup={setPopup}
                onRemapped={() => void refreshAll()}
                lastDbSync={lastDbSync}
            />
            {loading ? (
                <div>Loading...</div>
            ) : items.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '40px', color: 'var(--text-color)' }}>
                    <div style={{ fontSize: '3em', marginBottom: '10px' }}>✅</div>
                    <h3>All menu identities resolved!</h3>
                    <p style={{ color: 'var(--text-secondary)' }}>No unclustered or globally unlinked items found.</p>
                </div>
            ) : (
                items.map(item => (
                    <Card key={`${item.menu_item_id}-${item.source_variant_id}`} title={(
                        <span>
                            {formatResolutionTitle(item)}
                            {item.resolution_kind === 'addon_gap' && (
                                <span style={addonGapBadgeStyle}>Addon gap — needs confirmation</span>
                            )}
                            {item.resolution_kind === 'global_identity_gap' && (
                                <span style={addonGapBadgeStyle}>Global identity gap</span>
                            )}
                        </span>
                    )}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(280px, 420px)', gap: '20px' }}>
                            <div>
                                <p>Created: {new Date(item.created_at).toLocaleString()}</p>
                                <p style={{ color: 'var(--text-secondary)' }}>Type: {item.type}</p>
                                <p style={{ color: 'var(--text-secondary)' }}>Source variant: {item.source_variant_name}</p>
                                <p style={{ color: 'var(--text-secondary)' }}>
                                    Unresolved rows: {item.unresolved_mapping_rows || 0} mappings
                                    {item.order_item_rows ? `, ${item.order_item_rows} order rows` : ''}
                                    {item.order_item_qty ? `, ${item.order_item_qty} qty` : ''}
                                    {item.addon_rows ? `, ${item.addon_rows} addon rows` : ''}
                                    {item.addon_qty ? `, ${item.addon_qty} addon qty` : ''}
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
            {showHistory && <CollapsibleCard title="Resolution History" defaultCollapsed>
                {mergeHistory.length === 0 ? (
                    <p style={{ margin: 0, color: 'var(--text-secondary)' }}>No resolutions recorded yet.</p>
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
                                        {isVerifyInPlaceEntry(entry) ? (
                                            <>
                                                <span style={{ color: '#10B981' }}>✓ {entry.target_name || entry.source_name}</span>
                                                <span style={{ marginLeft: '8px', fontSize: '0.75em', fontWeight: 600, color: '#10B981', background: 'rgba(16, 185, 129, 0.12)', padding: '2px 8px', borderRadius: '999px' }}>
                                                    Verified in place
                                                </span>
                                            </>
                                        ) : entry.source_id === entry.target_id ? (
                                            <span style={{ color: '#10B981' }}>{entry.target_name || entry.source_name}</span>
                                        ) : (
                                            <>
                                                <span style={{ color: '#EF4444' }}>{entry.source_name}</span>
                                                {' → '}
                                                <span style={{ color: '#10B981' }}>{entry.target_name || 'Deleted target'}</span>
                                            </>
                                        )}
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
                {historyTotal > HISTORY_PAGE_SIZE && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '12px' }}>
                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>
                            Showing {(historyPage - 1) * HISTORY_PAGE_SIZE + 1} - {Math.min(historyPage * HISTORY_PAGE_SIZE, historyTotal)} of {historyTotal}
                        </span>
                        <div>
                            <button disabled={historyPage <= 1} onClick={() => setHistoryPage(p => p - 1)} style={{ marginRight: '5px', padding: '5px 10px', cursor: historyPage <= 1 ? 'not-allowed' : 'pointer' }}>&lt; Prev</button>
                            <span>Page {historyPage} of {Math.ceil(historyTotal / HISTORY_PAGE_SIZE)}</span>
                            <button disabled={historyPage >= Math.ceil(historyTotal / HISTORY_PAGE_SIZE)} onClick={() => setHistoryPage(p => p + 1)} style={{ marginLeft: '5px', padding: '5px 10px', cursor: historyPage >= Math.ceil(historyTotal / HISTORY_PAGE_SIZE) ? 'not-allowed' : 'pointer' }}>Next &gt;</button>
                        </div>
                    </div>
                )}
            </CollapsibleCard>}
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
                                                <div style={{ fontWeight: 700 }}>
                                                    {candidate.name}
                                                    {modalItem && candidate.menu_item_id === modalItem.menu_item_id && (
                                                        <span style={{ marginLeft: '8px', fontSize: '0.75em', fontWeight: 600, color: '#3B82F6', background: 'rgba(59, 130, 246, 0.12)', padding: '2px 8px', borderRadius: '999px' }}>
                                                            This item
                                                        </span>
                                                    )}
                                                </div>
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
                                                Sibling unresolved variants, if any, will remain separate. You can undo resolutions from Resolution History.
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
                                <select
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
                                >
                                    <option value="">Select type</option>
                                    {(renameType && !typeOptions.includes(renameType)
                                        ? [renameType, ...typeOptions]
                                        : typeOptions
                                    ).map(type => (
                                        <option key={type} value={type}>{type}</option>
                                    ))}
                                </select>
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

// --- Group History Tab ---

function GroupHistoryTab({ lastDbSync }: { lastDbSync?: number }) {
    const [entries, setEntries] = useState<MergeHistoryEntry[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [undoing, setUndoing] = useState<string | null>(null);
    const [popup, setPopup] = useState<PopupMessage | null>(null);

    const load = async (requestedPage = page) => {
        setLoading(true);
        try {
            const response = await endpoints.menu.mergeHistory({
                limit: HISTORY_PAGE_SIZE,
                offset: (requestedPage - 1) * HISTORY_PAGE_SIZE,
            });
            setEntries(response.data.entries);
            setTotal(response.data.total);
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { void load(page); }, [page, lastDbSync]);

    const handleUndo = async (entry: MergeHistoryEntry) => {
        if (!entry.is_undoable || !entry.global_mutation_id) return;
        setUndoing(entry.history_id || entry.global_mutation_id);
        try {
            const previewResponse = await endpoints.menu.globalPreview({
                mutation_type: 'global_menu.undo',
                payload: { undo_mutation_id: entry.global_mutation_id },
            });
            const preview = previewResponse.data;
            if (!confirmGlobalImpact(preview, 'Undoing this group menu change')) return;
            await endpoints.menu.globalCommit(globalPreviewReference(preview));
            setPopup({ type: 'success', message: 'Group menu change undone.' });
            await load(page);
        } catch (error) {
            setPopup({ type: 'error', message: getApiErrorMessage(error) });
        } finally {
            setUndoing(null);
        }
    };

    const pages = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
    return (
        <div>
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
            <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>
                One group-wide audit timeline. Legacy restaurant events remain visible but are never presented as undoable.
            </p>
            <Card title={`Group History (${total})`}>
                {loading ? <p>Loading...</p> : entries.length === 0 ? (
                    <p style={{ margin: 0, color: 'var(--text-secondary)' }}>No group history has been cached yet.</p>
                ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        {entries.map(entry => {
                            const busyKey = entry.history_id || entry.global_mutation_id || String(entry.merge_id);
                            return (
                                <div key={busyKey} style={{ padding: '12px 0', borderBottom: '1px solid var(--border-color)' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', alignItems: 'center' }}>
                                        <div>
                                            <div style={{ color: 'var(--text-color)' }}>
                                                <span style={{ color: '#EF4444' }}>{entry.source_name}</span>
                                                {' → '}
                                                <span style={{ color: '#10B981' }}>{entry.target_name || entry.source_name}</span>
                                            </div>
                                            <div style={{ color: 'var(--text-secondary)', fontSize: '0.84em', marginTop: '4px' }}>
                                                {entry.source_kind === 'legacy_restaurant_event' ? 'Legacy restaurant history' : 'Global menu mutation'}
                                                {entry.event_type ? ` · ${entry.event_type}` : ''}
                                                {entry.origin_restaurant_id ? ` · origin ${entry.origin_restaurant_id}` : ''}
                                                {entry.actor ? ` · by ${entry.actor}` : ''}
                                                {` · ${new Date(entry.merged_at).toLocaleString()}`}
                                            </div>
                                        </div>
                                        {entry.is_undoable && entry.global_mutation_id ? (
                                            <button
                                                onClick={() => void handleUndo(entry)}
                                                disabled={undoing === busyKey}
                                                style={{ padding: '8px 14px', background: '#444', color: 'white', border: 'none', borderRadius: '8px' }}
                                            >
                                                {undoing === busyKey ? 'Undoing…' : 'Undo'}
                                            </button>
                                        ) : (
                                            <span style={{ color: 'var(--text-secondary)', fontSize: '0.8em' }}>Not undoable</span>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
                {total > HISTORY_PAGE_SIZE && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '12px' }}>
                        <button disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Previous</button>
                        <span>Page {page} of {pages}</span>
                        <button disabled={page >= pages} onClick={() => setPage(value => value + 1)}>Next</button>
                    </div>
                )}
            </Card>
        </div>
    );
}

// --- Main Page ---

export default function Menu({ lastDbSync }: { lastDbSync?: number }) {
    const { isAllStores, selectedStore } = useStore();
    const [activeTab, setActiveTab] = useState<'summary' | 'catalog' | 'items' | 'variants' | 'matrix' | 'history' | 'resolutions'>('summary');
    const [globalStatusSelection, setGlobalStatusSelection] = useState<{
        restaurantId: string;
        syncToken?: number;
        status: GlobalMenuStatus;
    } | null>(null);
    const sharedPosAdvertised = hasGlobalMenuSharedPosCatalogCapability(selectedStore);
    const globalStatus = globalStatusSelection &&
        globalStatusSelection.restaurantId === selectedStore?.restaurant_id &&
        globalStatusSelection.syncToken === lastDbSync
        ? globalStatusSelection.status
        : null;
    const groupOwnedReady = isGroupOwnedMenuReady(selectedStore, globalStatus, isAllStores);
    const labels = globalMenuViewLabels(groupOwnedReady);

    useEffect(() => {
        let cancelled = false;
        if (isAllStores || !sharedPosAdvertised) return () => { cancelled = true; };
        endpoints.menu.globalStatus()
            .then(response => {
                if (!cancelled && selectedStore) {
                    setGlobalStatusSelection({
                        restaurantId: selectedStore.restaurant_id,
                        syncToken: lastDbSync,
                        status: response.data,
                    });
                }
            })
            .catch(() => {
                if (!cancelled) setGlobalStatusSelection(null);
            });
        return () => { cancelled = true; };
    }, [isAllStores, selectedStore?.restaurant_id, sharedPosAdvertised, lastDbSync]);
    const displayedActiveTab = !groupOwnedReady && (activeTab === 'catalog' || activeTab === 'history')
        ? 'summary'
        : activeTab;

    const menuTabs = [
        { id: 'summary' as const, label: '📊 Summary' },
        ...(groupOwnedReady ? [{ id: 'catalog' as const, label: `📚 ${labels.catalog}` }] : []),
        { id: 'items' as const, label: '📋 Menu Items' },
        { id: 'variants' as const, label: '📏 Variants' },
        { id: 'matrix' as const, label: `🕸️ ${labels.matrix}` },
        ...(groupOwnedReady ? [{ id: 'history' as const, label: `🕘 ${labels.history}` }] : []),
        { id: 'resolutions' as const, label: '✨ Resolutions' },
    ];

    return (
        <div className="page-container" style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>
            <div className="segmented-control segmented-page-tabs" style={{ marginBottom: '20px' }}>
                {menuTabs.map((tab) => (
                    <TabButton
                        key={tab.id}
                        active={displayedActiveTab === tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        variant="segmented"
                        size="large"
                    >
                        {tab.label}
                    </TabButton>
                ))}
            </div>

            {displayedActiveTab === 'summary' && <SummaryTab lastDbSync={lastDbSync} />}
            {displayedActiveTab === 'catalog' && groupOwnedReady && <GroupCatalogTab lastDbSync={lastDbSync} />}
            {displayedActiveTab === 'items' && <MenuItemsTab lastDbSync={lastDbSync} />}
            {displayedActiveTab === 'variants' && <VariantsTab lastDbSync={lastDbSync} />}
            {displayedActiveTab === 'matrix' && <MatrixTab lastDbSync={lastDbSync} groupOwnedReady={groupOwnedReady} />}
            {displayedActiveTab === 'history' && groupOwnedReady && <GroupHistoryTab lastDbSync={lastDbSync} />}
            {displayedActiveTab === 'resolutions' && (
                <SingleStoreOnly what="Menu resolutions">
                    <ResolutionsTab lastDbSync={lastDbSync} showHistory={!groupOwnedReady} />
                </SingleStoreOnly>
            )}
        </div>
    );
}
