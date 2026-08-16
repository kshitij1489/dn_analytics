import { describe, expect, it, vi } from 'vitest';
import {
    canonicalItemRenameConflicts,
    canonicalItemSelectionId,
    canonicalVariantLabel,
    canonicalVariantSelectionId,
    claimedCanonicalIdentity,
    findCanonicalVariantByIdentity,
    filterCanonicalItemTargets,
    GlobalIdentityMappedVerificationError,
    globalResolutionRoute,
    isEligibleMergeTarget,
    isResolutionTargetSelectionMissing,
    mappedVerificationFailureMessage,
    repairGlobalIdentityCoverage,
    resolutionAttemptKey,
    runResolutionAttempt,
} from './menuResolutionRouting';

describe('global-first canonical target presentation', () => {
    const items = [
        {
            global_menu_item_id: 'global-vanilla',
            local_menu_item_id: 'local-vanilla',
            canonical_name: 'Vanilla Ice Cream',
            canonical_type: 'Ice Cream',
            is_verified: true,
        },
        {
            global_menu_item_id: 'global-coffee',
            local_menu_item_id: 'local-coffee',
            canonical_name: 'Cold Coffee',
            canonical_type: 'Beverage',
            is_verified: true,
        },
        {
            global_menu_item_id: 'global-draft',
            local_menu_item_id: 'local-draft',
            canonical_name: 'Draft Sundae',
            canonical_type: 'Ice Cream',
            is_verified: false,
        },
    ];

    it('searches canonical group names and types rather than local projection labels', () => {
        expect(filterCanonicalItemTargets(items, 'beverage').map(item => item.global_menu_item_id))
            .toEqual(['global-coffee']);
        expect(filterCanonicalItemTargets(items, 'vanilla').map(item => item.global_menu_item_id))
            .toEqual(['global-vanilla']);
    });

    it('keeps active catalog rows visible regardless of catalog verification flag', () => {
        expect(filterCanonicalItemTargets(items, '').map(item => item.global_menu_item_id))
            .toEqual(['global-vanilla', 'global-coffee', 'global-draft']);
    });

    it('shows canonical variant dimensions without leaking projection IDs', () => {
        const variant = {
            global_variant_id: 'global-regular',
            local_variant_id: 'local-collision-suffix',
            canonical_name: 'Regular Tub',
            unit: 'GMS',
            value: 300,
            is_verified: true,
        };
        expect(canonicalVariantLabel(variant)).toBe('Regular Tub · GMS 300');
        expect(canonicalVariantSelectionId(variant)).toBe('global-regular');
    });

    it('stores global item identity even when a local projection handle exists', () => {
        expect(canonicalItemSelectionId(items[0])).toBe('global-vanilla');
    });

    it('preselects variants by identity when canonical labels collide', () => {
        const variants = [
            {
                global_variant_id: 'global-regular-300',
                local_variant_id: 'local-regular-300',
                canonical_name: 'Regular Tub',
                unit: 'GMS',
                value: 300,
                is_verified: true,
            },
            {
                global_variant_id: 'global-regular-500',
                local_variant_id: 'local-regular-500',
                canonical_name: 'Regular Tub',
                unit: 'GMS',
                value: 500,
                is_verified: true,
            },
        ];

        expect(findCanonicalVariantByIdentity(
            variants,
            'global-regular-500',
            'global-regular-300',
        )?.global_variant_id).toBe('global-regular-500');
        expect(findCanonicalVariantByIdentity(variants, null, null)).toBeUndefined();
    });

    it('uses resolution context as the rename guard when the catalog is unavailable', () => {
        const context = {
            global_item_id: 'global-vanilla',
            canonical_name: 'Vanilla Ice Cream',
            canonical_type: 'Ice Cream',
        };

        expect(canonicalItemRenameConflicts(
            context,
            [],
            'Renamed Vanilla',
            'Ice Cream',
        )).toBe(true);
        expect(canonicalItemRenameConflicts(
            context,
            [],
            'Vanilla Ice Cream',
            'Ice Cream',
        )).toBe(false);
    });

    it('prefers active catalog metadata over stale local context metadata', () => {
        const context = {
            global_item_id: 'global-vanilla',
            canonical_name: 'Old Local Name',
            canonical_type: 'Dessert',
        };

        expect(canonicalItemRenameConflicts(
            context,
            [items[0]],
            'Vanilla Ice Cream',
            'Ice Cream',
        )).toBe(false);
    });
});

describe('merge target eligibility', () => {
    const verified = { menu_item_id: 'verified', is_verified: true };
    const unverified = { menu_item_id: 'unverified', is_verified: false };

    it('offers verified targets in the legacy local-resolution branch', () => {
        expect(isEligibleMergeTarget(verified, 'source')).toBe(true);
    });

    it('never offers an unverified target that is not the item being resolved', () => {
        expect(isEligibleMergeTarget(unverified, 'source')).toBe(false);
        expect(isEligibleMergeTarget(unverified, 'unverified')).toBe(true);
    });

    it('requires both stable and local target IDs in global mode', () => {
        expect(isResolutionTargetSelectionMissing(true, 'local-item', '')).toBe(true);
        expect(isResolutionTargetSelectionMissing(true, '', 'global-item')).toBe(true);
        expect(isResolutionTargetSelectionMissing(
            true,
            'local-item',
            'global-item',
        )).toBe(false);
        expect(isResolutionTargetSelectionMissing(false, 'local-item', '')).toBe(false);
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

    it('maps missing item and variant identity, then verifies the assignment', async () => {
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
