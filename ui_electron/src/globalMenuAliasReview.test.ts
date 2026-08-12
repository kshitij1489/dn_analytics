import { describe, expect, it } from 'vitest';

import {
    authorityCanonicalPrice,
    observedOutletPrices,
    requiresPriceDifferenceConfirmation,
} from './globalMenuAliasReview';
import type { GlobalMenuAliasQueueRow } from './types/api';

function queueRow(overrides: Partial<GlobalMenuAliasQueueRow> = {}): GlobalMenuAliasQueueRow {
    return {
        locator_type: 'pos_item',
        locator_value: '8442',
        resolution_state: 'pending',
        observation_digest: 'digest',
        evidence: [{
            restaurant_id: 'supermart',
            restaurant_name: 'Super Mart',
            menu_item_id: 'm9',
            item_name: 'Vanilla Ice Cream',
            item_type: 'Dessert',
            price: '310.00',
        }],
        candidate: {
            global_item_id: 'global-vanilla',
            canonical_price: '290.00',
            reason: 'unique_itemcode',
        },
        conflicts: [{
            code: 'price_review_required',
            message: 'Outlet price differs from authority price.',
        }],
        ...overrides,
    };
}

describe('global menu alias price review', () => {
    it('uses only the authority candidate as the canonical price default', () => {
        expect(authorityCanonicalPrice(queueRow())).toBe('290.00');
        expect(authorityCanonicalPrice(queueRow({ candidate: null }))).toBe('');
    });

    it('keeps outlet prices separate and requires explicit difference review', () => {
        const base = queueRow();
        const row = queueRow({
            evidence: [
                ...base.evidence,
                { ...base.evidence[0], restaurant_id: 'dach', price: '290.00' },
            ],
        });
        expect(observedOutletPrices(row)).toEqual(['290.00', '310.00']);
        expect(requiresPriceDifferenceConfirmation(row)).toBe(true);
        expect(requiresPriceDifferenceConfirmation(queueRow({ conflicts: [] }))).toBe(false);
    });
});
