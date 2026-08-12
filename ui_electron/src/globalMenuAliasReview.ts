import type { GlobalMenuAliasQueueRow } from './types/api';

const PRICE_REVIEW_REQUIRED = 'price_review_required';

export function authorityCanonicalPrice(row: GlobalMenuAliasQueueRow): string {
    return row.candidate?.canonical_price || '';
}

export function observedOutletPrices(row: GlobalMenuAliasQueueRow): string[] {
    return [...new Set(row.evidence.map(entry => entry.price))].sort(
        (left, right) => Number(left) - Number(right),
    );
}

export function requiresPriceDifferenceConfirmation(
    row: GlobalMenuAliasQueueRow,
): boolean {
    return row.conflicts.some(conflict => conflict.code === PRICE_REVIEW_REQUIRED);
}
