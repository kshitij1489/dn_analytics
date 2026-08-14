import { describe, expect, it, vi } from 'vitest';
import {
    claimedCanonicalIdentity,
    GlobalIdentityMappedVerificationError,
    globalResolutionRoute,
    isEligibleMergeTarget,
    isEligibleMergeTargetVariant,
    mappedVerificationFailureMessage,
    repairGlobalIdentityCoverage,
    resolutionAttemptKey,
    runResolutionAttempt,
    targetCoveragePlan,
} from './menuResolutionRouting';

describe('merge target eligibility', () => {
    const linked = { menu_item_id: 'linked', is_verified: true, is_globally_linked: true };
    const unlinked = { menu_item_id: 'unlinked', is_verified: true, is_globally_linked: false };
    const unverified = { menu_item_id: 'unverified', is_verified: false, is_globally_linked: true };

    it('drops a verified but unlinked target when the source pair has complete identity', () => {
        expect(isEligibleMergeTarget(linked, 'source', true)).toBe(true);
        expect(isEligibleMergeTarget(unlinked, 'source', true)).toBe(false);
    });

    it('keeps an unlinked target when the source item or variant identity is missing', () => {
        // Coverage repair establishes identity for either side, so the target
        // must stay selectable for both kinds of source coverage gap.
        expect(isEligibleMergeTarget(unlinked, 'source', false)).toBe(true);
    });

    it('never offers an unverified target that is not the item being resolved', () => {
        expect(isEligibleMergeTarget(unverified, 'source', false)).toBe(false);
        expect(isEligibleMergeTarget(unverified, 'unverified', true)).toBe(true);
    });

    it('treats a missing link flag as unlinked rather than eligible', () => {
        expect(isEligibleMergeTarget(
            { menu_item_id: 'legacy', is_verified: true }, 'source', true,
        )).toBe(false);
    });
});

describe('merge target variant eligibility', () => {
    it('offers only linked variants to a direct global merge', () => {
        expect(isEligibleMergeTargetVariant({ is_globally_linked: true }, true)).toBe(true);
        expect(isEligibleMergeTargetVariant({ is_globally_linked: false }, true)).toBe(false);
        expect(isEligibleMergeTargetVariant({}, true)).toBe(false);
    });

    it('keeps all variants available while coverage repair can establish identity', () => {
        expect(isEligibleMergeTargetVariant({ is_globally_linked: false }, false)).toBe(true);
        expect(isEligibleMergeTargetVariant({}, false)).toBe(true);
    });
});

describe('mapped verification failure messaging', () => {
    it('does not promise that every unresolved identity heals on the next sync', () => {
        const message = mappedVerificationFailureMessage(
            'The POS assignment is ambiguous.',
            true,
        );

        expect(message).toContain('may project');
        expect(message).toContain('both an item and an addon');
        expect(message).not.toContain('on the next Sync DB');
    });
});

describe('merge target coverage repair', () => {
    const linkedItem = { globalItemId: 'global-item', globalVariantId: null };

    it('owes nothing when the target pair carries no POS evidence of its own', () => {
        // The dropdown lists every variant type in the database, so this pair
        // has no POS evidence and no link of its own to repair. Demanding a
        // locator map here failed the whole merge with "No POS locator or
        // itemcode is available for this resolution."
        expect(targetCoveragePlan({ ...linkedItem, locatorCount: 0 }, false)).toEqual({
            mapLocators: false,
            mapAtItemLevel: true,
        });
    });

    it('owes no variant link when the pair exists but holds only synthetic rows', () => {
        // A pair the target item genuinely carries still reports no locators
        // when its menu_item_variants rows are catalog stubs rather than POS
        // ids. There is nothing a variant-level map could be written against,
        // so the item link is the whole of what repair can establish.
        expect(targetCoveragePlan(
            { globalItemId: null, globalVariantId: null, locatorCount: 0 },
            false,
        )).toEqual({ mapLocators: true, mapAtItemLevel: true });
    });

    it('maps the target pair when it carries evidence but no variant link', () => {
        expect(targetCoveragePlan({ ...linkedItem, locatorCount: 2 }, false)).toEqual({
            mapLocators: true,
            mapAtItemLevel: false,
        });
    });

    it('leaves a fully linked target pair alone', () => {
        expect(targetCoveragePlan(
            { globalItemId: 'global-item', globalVariantId: 'global-variant', locatorCount: 2 },
            false,
        )).toEqual({ mapLocators: false, mapAtItemLevel: false });
    });

    it('maps an unlinked target item at item level on both new-variant routes', () => {
        // "Create new variant type" and an unassigned existing variant are the
        // same choice for the target: the item still owes a link, and its own
        // locators carry no variant identity yet.
        expect(targetCoveragePlan({ globalItemId: null, globalVariantId: null, locatorCount: 4 }, true))
            .toEqual({ mapLocators: true, mapAtItemLevel: true });
        expect(targetCoveragePlan({ globalItemId: null, globalVariantId: null, locatorCount: 0 }, false))
            .toEqual({ mapLocators: true, mapAtItemLevel: true });
    });
});

describe('canonical identity adoption', () => {
    it('adopts the row that already holds the identity key a create asked for', () => {
        // Without this the create an aborted attempt already committed blocks
        // every retry: the preview conflicts, so commit is refused, while the
        // local pair it was minted for still carries no link.
        expect(claimedCanonicalIdentity([{
            code: 'canonical_identity_taken',
            message: "'JUNIOR_SCOOP_60GMS' at GMS/60 is already global variant 'existing'.",
            global_variant_id: 'existing',
        }], 'global_variant_id')).toBe('existing');
    });

    it('ignores conflicts that name no identity of the kind being created', () => {
        expect(claimedCanonicalIdentity([
            { code: 'variant_dimension_mismatch', global_variant_id: 'other' },
            { code: 'canonical_identity_taken', global_item_id: 'item-only' },
        ], 'global_variant_id')).toBeNull();
        expect(claimedCanonicalIdentity(undefined, 'global_item_id')).toBeNull();
        expect(claimedCanonicalIdentity([], 'global_item_id')).toBeNull();
    });

    it('rejects a blank or non-string identity rather than adopting it', () => {
        expect(claimedCanonicalIdentity(
            [{ code: 'canonical_identity_taken', global_item_id: '   ' }], 'global_item_id',
        )).toBeNull();
        expect(claimedCanonicalIdentity(
            [{ code: 'canonical_identity_taken', global_item_id: 7 }], 'global_item_id',
        )).toBeNull();
    });
});

describe('global menu resolution routing', () => {
    it('verifies an unverified assignment whose global identity is already correct', () => {
        expect(globalResolutionRoute(
            'unverified_mapping',
            { global_item_id: 'global-item', global_variant_id: 'global-variant' },
            { global_item_id: 'global-item', global_variant_id: 'global-variant' },
        )).toBe('verify_assignment');
    });

    it('uses locator mapping when either global identity is missing', () => {
        expect(globalResolutionRoute(
            'unverified_mapping',
            { global_item_id: 'global-item', global_variant_id: null },
            { global_item_id: 'global-item', global_variant_id: 'global-variant' },
        )).toBe('locator_map');
    });

    it('uses locator mapping when the intended global identity changes', () => {
        expect(globalResolutionRoute(
            'unverified_mapping',
            { global_item_id: 'global-item', global_variant_id: 'old-variant' },
            { global_item_id: 'global-item', global_variant_id: 'new-variant' },
        )).toBe('locator_map');
    });

    it.each([
        ['merge/target', true],
        ['Verify as New/rename', false],
    ])('maps missing item and variant identity, then verifies on the %s path', async (_path, mapTarget) => {
        const calls: string[] = [];
        const result = await repairGlobalIdentityCoverage({
            ensureItem: async () => {
                calls.push('global_item.create');
                return 'global-item';
            },
            ensureVariant: async globalItemId => {
                expect(globalItemId).toBe('global-item');
                calls.push('global_variant.create');
                return 'global-variant';
            },
            mapTargetLocators: mapTarget
                ? async () => {
                    calls.push('target:global_locator.map');
                    return true;
                }
                : undefined,
            mapSourceLocators: async (globalItemId, globalVariantId) => {
                expect([globalItemId, globalVariantId]).toEqual([
                    'global-item',
                    'global-variant',
                ]);
                calls.push('source:global_locator.map');
                return true;
            },
            onIdentityMapped: (globalItemId, globalVariantId) => {
                expect([globalItemId, globalVariantId]).toEqual([
                    'global-item',
                    'global-variant',
                ]);
                calls.push('identity.cached');
            },
            verifyAssignment: async () => {
                calls.push('assignment.verify');
                return 'verified';
            },
        });

        expect(result).toBe('verified');
        expect(calls).toEqual([
            'global_item.create',
            'global_variant.create',
            ...(mapTarget ? ['target:global_locator.map'] : []),
            'source:global_locator.map',
            'identity.cached',
            'assignment.verify',
        ]);
    });

    it('preserves an existing item identity while creating and mapping a missing variant', async () => {
        const calls: string[] = [];
        await repairGlobalIdentityCoverage({
            ensureItem: async () => {
                calls.push('global_item.reuse');
                return 'existing-global-item';
            },
            ensureVariant: async () => {
                calls.push('global_variant.create');
                return 'new-global-variant';
            },
            mapSourceLocators: async () => {
                calls.push('global_locator.map');
                return true;
            },
            verifyAssignment: async () => {
                calls.push('assignment.verify');
                return 'verified';
            },
        });

        expect(calls).toEqual([
            'global_item.reuse',
            'global_variant.create',
            'global_locator.map',
            'assignment.verify',
        ]);
    });

    it('keeps a mapped row open on verification failure and retries verification without another map', async () => {
        let current: {
            global_item_id: string | null;
            global_variant_id: string | null;
        } = { global_item_id: null, global_variant_id: null };
        const mapSource = vi.fn(async () => {
            current = {
                global_item_id: 'global-item',
                global_variant_id: 'global-variant',
            };
            return true;
        });
        const verify = vi.fn()
            .mockRejectedValueOnce(new Error('central verification unavailable'))
            .mockResolvedValueOnce('verified');

        await expect(repairGlobalIdentityCoverage({
            ensureItem: async () => 'global-item',
            ensureVariant: async () => 'global-variant',
            mapSourceLocators: mapSource,
            verifyAssignment: verify,
        })).rejects.toBeInstanceOf(GlobalIdentityMappedVerificationError);

        expect(globalResolutionRoute(
            'unverified_mapping',
            current,
            { global_item_id: 'global-item', global_variant_id: 'global-variant' },
        )).toBe('verify_assignment');
        await verify();

        expect(mapSource).toHaveBeenCalledTimes(1);
        expect(verify).toHaveBeenCalledTimes(2);
    });

    it('returns success only after the refreshed resolution query no longer contains the row', async () => {
        let unresolved = true;
        const result = await repairGlobalIdentityCoverage({
            ensureItem: async () => 'global-item',
            ensureVariant: async () => 'global-variant',
            mapSourceLocators: async () => true,
            verifyAssignment: async () => {
                unresolved = false;
                const refreshedRows = unresolved ? ['assignment-1'] : [];
                if (refreshedRows.includes('assignment-1')) {
                    throw new Error('row remains');
                }
                return 'verified after refresh';
            },
        });

        expect(result).toBe('verified after refresh');
        expect(unresolved).toBe(false);
    });

    it('single-flights the complete create/map/verify workflow on double click', async () => {
        const inFlight = new Set<string>();
        const item = {
            menu_item_id: 'local-item',
            source_variant_id: 'local-variant',
            assignment_order_item_ids: ['assignment-2', 'assignment-1', 'assignment-1'],
        };
        const key = resolutionAttemptKey(item);
        let releaseMap!: () => void;
        const mapGate = new Promise<void>(resolve => {
            releaseMap = resolve;
        });
        const mutations: string[] = [];
        const workflow = () => runResolutionAttempt(inFlight, key, async () => (
            repairGlobalIdentityCoverage({
                ensureItem: async () => {
                    mutations.push('item.create');
                    return 'global-item';
                },
                ensureVariant: async () => {
                    mutations.push('variant.create');
                    return 'global-variant';
                },
                mapSourceLocators: async () => {
                    mutations.push('locator.map');
                    await mapGate;
                    return true;
                },
                verifyAssignment: async () => {
                    mutations.push('assignment.verify');
                    return 'verified';
                },
            })
        ));

        const first = workflow();
        await Promise.resolve();
        const second = await workflow();
        expect(second).toBeUndefined();
        releaseMap();
        expect(await first).toBe('verified');
        expect(mutations).toEqual([
            'item.create',
            'variant.create',
            'locator.map',
            'assignment.verify',
        ]);
        expect(key).toBe('local-item:local-variant:assignment-1:assignment-2');
    });
});
