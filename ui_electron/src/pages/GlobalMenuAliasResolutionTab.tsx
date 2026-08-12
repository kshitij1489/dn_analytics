import { useCallback, useEffect, useMemo, useState } from 'react';

import { endpoints } from '../api';
import {
    authorityCanonicalPrice,
    observedOutletPrices,
    requiresPriceDifferenceConfirmation,
} from '../globalMenuAliasReview';
import type {
    GlobalMenuAliasDecisionMethod,
    GlobalMenuAliasDecisionRequest,
    GlobalMenuAliasQueueRow,
    GlobalMenuAliasState,
    GlobalMenuAliasReconciliationStatus,
    GlobalMenuCatalogResponse,
} from '../types/api';

const PAGE_SIZE = 100;
const STALE_CODES = new Set([
    'global_menu_alias_observation_conflict',
    'global_menu_alias_revision_conflict',
    'global_menu_alias_preview_conflict',
]);

function errorDetail(error: unknown): { message: string; code?: string } {
    const value = error as {
        message?: string;
        response?: { data?: { detail?: string | { error?: string; message?: string; code?: string } } };
    };
    const detail = value.response?.data?.detail;
    if (typeof detail === 'string') return { message: detail };
    if (detail && typeof detail === 'object') {
        return {
            message: detail.error || detail.message || 'Alias resolution failed',
            code: detail.code,
        };
    }
    return { message: value.message || 'Alias resolution failed' };
}

function stateColor(state: GlobalMenuAliasState): string {
    if (state === 'approved' || state === 'applied') return '#18794e';
    if (state === 'stale' || state === 'quarantined') return '#b42318';
    return '#9a6700';
}

export function GlobalMenuAliasResolutionTab({ lastDbSync }: { lastDbSync?: number }) {
    const [statusFilter, setStatusFilter] = useState<GlobalMenuAliasState | 'all'>('pending');
    const [rows, setRows] = useState<GlobalMenuAliasQueueRow[]>([]);
    const [catalog, setCatalog] = useState<GlobalMenuCatalogResponse | null>(null);
    const [reconciliation, setReconciliation] = useState<GlobalMenuAliasReconciliationStatus | null>(null);
    const [groupRevision, setGroupRevision] = useState(0);
    const [cursor, setCursor] = useState<string | null>(null);
    const [hasMore, setHasMore] = useState(false);
    const [selected, setSelected] = useState<GlobalMenuAliasQueueRow | null>(null);
    const [targetItemId, setTargetItemId] = useState('');
    const [targetVariantId, setTargetVariantId] = useState('');
    const [canonicalPrice, setCanonicalPrice] = useState('');
    const [reason, setReason] = useState('');
    const [confirmed, setConfirmed] = useState(false);
    const [priceDifferenceConfirmed, setPriceDifferenceConfirmed] = useState(false);
    const [previewPriceReviewRequired, setPreviewPriceReviewRequired] = useState(false);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [notice, setNotice] = useState<{ kind: 'error' | 'success'; text: string } | null>(null);

    const load = useCallback(async (append = false) => {
        setLoading(true);
        setNotice(null);
        try {
            const [queueResponse, catalogResponse, reconciliationResponse] = await Promise.all([
                endpoints.menu.globalAliasQueue({
                    status: statusFilter,
                    limit: PAGE_SIZE,
                    ...(append && cursor ? { after: cursor } : {}),
                }),
                endpoints.menu.globalCatalog(),
                endpoints.menu.globalAliasReconciliationStatus(),
            ]);
            const queue = queueResponse.data;
            setRows(current => append ? [...current, ...queue.rows] : queue.rows);
            setCatalog(catalogResponse.data);
            setReconciliation(reconciliationResponse.data);
            setGroupRevision(queue.menu_group_revision);
            setCursor(queue.next_cursor || null);
            setHasMore(queue.has_more);
            if (!append) setSelected(null);
        } catch (error) {
            setNotice({ kind: 'error', text: errorDetail(error).message });
        } finally {
            setLoading(false);
        }
    }, [cursor, statusFilter]);

    useEffect(() => {
        void load(false);
        // Cursor changes only when loading a later page and must not reload page one.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [statusFilter, lastDbSync]);

    const chooseRow = (row: GlobalMenuAliasQueueRow) => {
        setSelected(row);
        setTargetItemId(row.candidate?.global_item_id || '');
        setTargetVariantId(row.candidate?.global_variant_id || '');
        setCanonicalPrice(authorityCanonicalPrice(row));
        setReason('');
        setConfirmed(false);
        setPriceDifferenceConfirmed(false);
        setPreviewPriceReviewRequired(false);
        setNotice(null);
    };

    const targetItem = useMemo(
        () => catalog?.items.find(item => item.global_menu_item_id === targetItemId),
        [catalog, targetItemId],
    );
    const targetVariant = useMemo(
        () => catalog?.variants.find(variant => variant.global_variant_id === targetVariantId),
        [catalog, targetVariantId],
    );
    const priceDifferenceRequiresReview = selected
        ? requiresPriceDifferenceConfirmation(selected) || previewPriceReviewRequired
        : false;

    const save = async (requestedStatus: 'pending' | 'approved') => {
        if (!selected) return;
        if (
            requestedStatus === 'approved'
            && (
                !targetItemId
                || !canonicalPrice.trim()
                || !confirmed
                || (priceDifferenceRequiresReview && !priceDifferenceConfirmed)
            )
        ) {
            setNotice({
                kind: 'error',
                text: 'Select a canonical item and price, then explicitly confirm the mapping and any outlet price difference.',
            });
            return;
        }
        if (!reason.trim()) {
            setNotice({ kind: 'error', text: 'Record a reviewer reason before previewing.' });
            return;
        }
        const method: GlobalMenuAliasDecisionMethod =
            selected.candidate?.global_item_id === targetItemId && selected.candidate?.reason
                ? selected.candidate.reason
                : 'manual';
        const request: GlobalMenuAliasDecisionRequest = {
            schema_version: 1,
            expected_menu_group_revision: groupRevision,
            locator_type: selected.locator_type,
            locator_value: selected.locator_value,
            expected_observation_digest: selected.observation_digest,
            requested_status: requestedStatus,
            global_item_id: targetItemId || null,
            global_variant_id: targetVariantId || null,
            canonical_price: canonicalPrice.trim() || null,
            decision_method: method,
            reason: reason.trim(),
        };
        setSaving(true);
        setNotice(null);
        try {
            const preview = (await endpoints.menu.globalAliasPreview(request)).data;
            if (!preview.commit_allowed) {
                const summary = preview.conflicts.map(conflict => conflict.message || conflict.code).filter(Boolean).join('; ');
                setNotice({ kind: 'error', text: summary || 'Central review did not allow this decision.' });
                return;
            }
            const previewCanonicalPrice = preview.target?.canonical_price?.trim() || '';
            const previewRequiresPriceReview = preview.conflicts.some(
                conflict => conflict.code === 'price_review_required',
            );
            const previewChangedPrice = Boolean(
                previewCanonicalPrice && previewCanonicalPrice !== canonicalPrice.trim(),
            );
            if (
                requestedStatus === 'approved'
                && (
                    previewChangedPrice
                    || (previewRequiresPriceReview && !priceDifferenceConfirmed)
                )
            ) {
                if (previewCanonicalPrice) setCanonicalPrice(previewCanonicalPrice);
                setPreviewPriceReviewRequired(true);
                setPriceDifferenceConfirmed(false);
                setNotice({
                    kind: 'error',
                    text: 'Central preview returned the current authority price or a price difference. Review it and confirm the price before approving.',
                });
                return;
            }
            const mutationId = globalThis.crypto.randomUUID();
            await endpoints.menu.globalAliasCommit({
                ...request,
                mutation_id: mutationId,
                preview_digest: preview.preview_digest,
            });
            setNotice({ kind: 'success', text: `${requestedStatus === 'approved' ? 'Approval' : 'Draft'} saved centrally.` });
            await load(false);
        } catch (error) {
            const detail = errorDetail(error);
            setNotice({
                kind: 'error',
                text: STALE_CODES.has(detail.code || '')
                    ? 'The source evidence or group revision changed. Nothing was saved; the queue has been refreshed.'
                    : detail.message,
            });
            if (STALE_CODES.has(detail.code || '')) await load(false);
        } finally {
            setSaving(false);
        }
    };

    return (
        <div>
            <div style={{ marginBottom: 16, color: 'var(--text-secondary)' }}>
                Review private outlet POS evidence against the central canonical catalog. Saving a decision does not alter local menu rows, assignments, history, or facts.
            </div>
            {notice && (
                <div role="alert" style={{ marginBottom: 12, color: notice.kind === 'error' ? '#b42318' : '#18794e' }}>
                    {notice.text}
                </div>
            )}
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
                <label>
                    State{' '}
                    <select value={statusFilter} onChange={event => setStatusFilter(event.target.value as GlobalMenuAliasState | 'all')}>
                        {['pending', 'approved', 'stale', 'applied', 'quarantined', 'all'].map(state => (
                            <option key={state} value={state}>{state}</option>
                        ))}
                    </select>
                </label>
                <button onClick={() => void load(false)} disabled={loading}>Refresh</button>
            </div>
            {reconciliation && (
                <div style={{ marginBottom: 12, padding: 10, border: '1px solid var(--border-color)', borderRadius: 8 }}>
                    Policy: {reconciliation.policy.alias_configured ? 'alias configured' : 'not configured'} · Initial reconciliation: {String(reconciliation.initial_reconciliation.status || 'not started')} · Observed {reconciliation.alias_counts.observed} · Approved {reconciliation.alias_counts.approved} · Pending {reconciliation.alias_counts.pending} · Stale {reconciliation.alias_counts.stale} · Quarantined {reconciliation.alias_counts.quarantined}
                </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 0.9fr) minmax(420px, 1.1fr)', gap: 16 }}>
                <section style={{ border: '1px solid var(--border-color)', borderRadius: 10, padding: 12 }}>
                    <h3 style={{ marginTop: 0 }}>Outlet evidence ({rows.length})</h3>
                    {loading && rows.length === 0 ? <p>Loading alias queue…</p> : rows.map(row => (
                        <button
                            key={`${row.locator_type}:${row.locator_value}`}
                            onClick={() => chooseRow(row)}
                            style={{
                                display: 'block', width: '100%', textAlign: 'left', padding: 10, marginBottom: 8,
                                borderRadius: 8, border: selected === row ? '2px solid var(--accent-color)' : '1px solid var(--border-color)',
                                background: 'var(--card-bg)', color: 'inherit', cursor: 'pointer',
                            }}
                        >
                            <b>{row.locator_type}:{row.locator_value}</b>{' '}
                            <span style={{ color: stateColor(row.resolution_state) }}>{row.resolution_state}</span>
                            <div>{row.evidence.map(entry => `${entry.restaurant_name}: ${entry.item_name}`).join(' · ')}</div>
                            <small>Suggestion: {row.candidate?.canonical_name || 'none'} ({row.candidate?.reason || 'manual review'})</small>
                        </button>
                    ))}
                    {hasMore && <button onClick={() => void load(true)} disabled={loading}>Load more</button>}
                </section>
                <section style={{ border: '1px solid var(--border-color)', borderRadius: 10, padding: 12 }}>
                    <h3 style={{ marginTop: 0 }}>Canonical decision</h3>
                    {!selected ? <p>Select one locator to review.</p> : (
                        <>
                            <div style={{ padding: 10, background: 'var(--secondary-bg)', borderRadius: 8, marginBottom: 12 }}>
                                {selected.evidence.map(entry => (
                                    <div key={entry.restaurant_id} style={{ marginBottom: 8 }}>
                                        <b>{entry.restaurant_name}</b> · {selected.locator_type}:{selected.locator_value}<br />
                                        {entry.item_name} · {entry.item_type} · itemcode {entry.itemcode || '—'}<br />
                                        Variant {entry.variant_name || '—'} {entry.variant_value || ''} {entry.variant_unit || ''} · ₹{entry.price}
                                    </div>
                                ))}
                                {selected.conflicts.map((conflict, index) => (
                                    <div key={index} style={{ color: '#b42318' }}>{conflict.code}: {conflict.message || 'Review required'}</div>
                                ))}
                            </div>
                            <label style={{ display: 'block', marginBottom: 10 }}>
                                Canonical item
                                <select
                                    value={targetItemId}
                                    onChange={event => {
                                        setTargetItemId(event.target.value);
                                        setPriceDifferenceConfirmed(false);
                                        setPreviewPriceReviewRequired(false);
                                    }}
                                    style={{ display: 'block', width: '100%' }}
                                >
                                    <option value="">No target (pending draft)</option>
                                    {(catalog?.items || []).map(item => (
                                        <option key={item.global_menu_item_id} value={item.global_menu_item_id}>{item.canonical_name} · {item.canonical_type}</option>
                                    ))}
                                </select>
                            </label>
                            <label style={{ display: 'block', marginBottom: 10 }}>
                                Canonical variant
                                <select
                                    value={targetVariantId}
                                    onChange={event => {
                                        setTargetVariantId(event.target.value);
                                        setPriceDifferenceConfirmed(false);
                                        setPreviewPriceReviewRequired(false);
                                    }}
                                    style={{ display: 'block', width: '100%' }}
                                >
                                    <option value="">No variant</option>
                                    {(catalog?.variants || []).map(variant => (
                                        <option key={variant.global_variant_id} value={variant.global_variant_id}>{variant.canonical_name} · {variant.value ?? '—'} {variant.unit || ''}</option>
                                    ))}
                                </select>
                            </label>
                            <div style={{ marginBottom: 10 }}>
                                Target: <b>{targetItem?.canonical_name || 'pending'}</b>{targetVariant ? ` · ${targetVariant.canonical_name}` : ''}
                            </div>
                            <div style={{ marginBottom: 10 }}>
                                Authority/current canonical price:{' '}
                                <b>{selected.candidate?.canonical_price ? `₹${selected.candidate.canonical_price}` : 'not available'}</b>
                                <br />
                                Outlet observed price{observedOutletPrices(selected).length === 1 ? '' : 's'}:{' '}
                                {observedOutletPrices(selected).map(price => `₹${price}`).join(', ') || 'none'}
                            </div>
                            <label style={{ display: 'block', marginBottom: 10 }}>
                                Approved canonical price
                                <input
                                    value={canonicalPrice}
                                    onChange={event => {
                                        setCanonicalPrice(event.target.value);
                                        setPriceDifferenceConfirmed(false);
                                    }}
                                    inputMode="decimal"
                                    style={{ display: 'block', width: '100%' }}
                                />
                            </label>
                            {priceDifferenceRequiresReview && (
                                <label style={{ display: 'block', marginBottom: 12 }}>
                                    <input
                                        type="checkbox"
                                        checked={priceDifferenceConfirmed}
                                        onChange={event => setPriceDifferenceConfirmed(event.target.checked)}
                                    />{' '}
                                    I reviewed the outlet price difference and confirm the authority/current canonical price.
                                </label>
                            )}
                            <label style={{ display: 'block', marginBottom: 10 }}>
                                Reviewer reason
                                <textarea value={reason} onChange={event => setReason(event.target.value)} rows={3} style={{ display: 'block', width: '100%' }} />
                            </label>
                            <label style={{ display: 'block', marginBottom: 12 }}>
                                <input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{' '}
                                I reviewed the raw evidence and confirm this exact canonical target.
                            </label>
                            <div style={{ display: 'flex', gap: 8 }}>
                                <button disabled={saving} onClick={() => void save('pending')}>Preview & save draft</button>
                                <button
                                    disabled={
                                        saving
                                        || !confirmed
                                        || !targetItemId
                                        || !canonicalPrice.trim()
                                        || (priceDifferenceRequiresReview && !priceDifferenceConfirmed)
                                    }
                                    onClick={() => void save('approved')}
                                >
                                    Preview & approve
                                </button>
                            </div>
                        </>
                    )}
                </section>
            </div>
        </div>
    );
}
