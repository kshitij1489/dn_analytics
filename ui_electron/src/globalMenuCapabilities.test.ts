import { describe, expect, it } from 'vitest';
import type { Store } from './types/api';
import {
    canUseCanonicalMenuControls,
    globalMenuViewLabels,
    hasGlobalMenuCapability,
    hasGlobalMenuMutationCapability,
    hasGlobalMenuResolutionCapability,
    isGroupOwnedMenuReady,
    isGlobalMenuCatalogReadable,
} from './globalMenuCapabilities';
import type { GlobalMenuStatus } from './types/api';

const FOUR_CAPABILITIES = [
    'global_menu_v1',
    'global_menu_resolution_v1',
    'global_menu_aggregation_v1',
    'global_menu_mutations_v1',
];

const store = (capabilities: string[], menuGroupId: string | null = 'group-1'): Store => ({
    restaurant_id: 'rest-1',
    display_name: 'One',
    timezone: 'Asia/Kolkata',
    database_path: '/rest-1.db',
    authorization_state: 'authorized',
    is_bound: true,
    menu_group_id: menuGroupId,
    menu_capabilities: capabilities,
});

const status = (overrides: Partial<GlobalMenuStatus> = {}): GlobalMenuStatus => ({
    mode: 'global_menu_v1',
    restaurant_id: 'rest-1',
    menu_group_id: 'group-1',
    schema_version: 1,
    active: true,
    server_advertised: true,
    capabilities: FOUR_CAPABILITIES,
    aggregation_advertised: true,
    resolution_advertised: true,
    mutation_advertised: true,
    resolution_ready: true,
    mutation_ready: true,
    aggregation_ready: true,
    coverage_complete: false,
    coverage_linked: 1,
    coverage_total: 2,
    quarantine_count: 0,
    catalog_revision: 7,
    mutation_revision: 7,
    bootstrap_status: 'complete',
    reason: 'active',
    ...overrides,
});

describe('global menu capabilities', () => {
    it('advertises the four capabilities together for an enrolled member', () => {
        const member = store(FOUR_CAPABILITIES);
        expect(hasGlobalMenuCapability(member)).toBe(true);
        expect(hasGlobalMenuResolutionCapability(member)).toBe(true);
        expect(hasGlobalMenuMutationCapability(member)).toBe(true);
        expect(canUseCanonicalMenuControls(member)).toBe(true);
    });

    it('keeps restaurant-scoped controls when the restaurant is ungrouped', () => {
        expect(hasGlobalMenuCapability(store(['global_menu_v1'], null))).toBe(false);
        expect(hasGlobalMenuResolutionCapability(store(['global_menu_resolution_v1'], null))).toBe(false);
        expect(canUseCanonicalMenuControls(store([], null))).toBe(true);
    });

    it('uses group labels only after the local projection is ready', () => {
        expect(globalMenuViewLabels(false).history).toBe('Resolution History');
        expect(globalMenuViewLabels(true)).toEqual({
            catalog: 'Group Catalog',
            matrix: 'Menu Matrix',
            history: 'Group History',
        });
    });

    it('treats incomplete coverage as ordinary enrollment, not a closed gate', () => {
        const member = store(FOUR_CAPABILITIES);
        const incomplete = status({ coverage_complete: false, coverage_linked: 0, coverage_total: 51 });
        expect(isGlobalMenuCatalogReadable(member, incomplete)).toBe(true);
        expect(isGroupOwnedMenuReady(member, incomplete)).toBe(true);
    });

    it('fails group views closed for stale, mismatched, or All Stores status', () => {
        const member = store(FOUR_CAPABILITIES);
        expect(isGroupOwnedMenuReady(member, status())).toBe(true);
        expect(isGroupOwnedMenuReady(member, null)).toBe(false);
        expect(isGroupOwnedMenuReady(member, status({ restaurant_id: 'rest-2' }))).toBe(false);
        expect(isGroupOwnedMenuReady(member, status({ menu_group_id: 'group-2' }))).toBe(false);
        expect(isGroupOwnedMenuReady(member, status({ active: false }))).toBe(false);
        expect(isGroupOwnedMenuReady(member, status(), true)).toBe(false);
    });
});
