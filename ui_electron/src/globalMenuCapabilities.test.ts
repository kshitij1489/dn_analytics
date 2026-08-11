import { describe, expect, it } from 'vitest';
import type { Store } from './types/api';
import {
    canUseCanonicalMenuControls,
    globalMenuViewLabels,
    hasGlobalMenuCapability,
    hasGlobalMenuMutationCapability,
    hasGlobalMenuResolutionCapability,
    hasGlobalMenuSharedPosCatalogCapability,
    isGroupOwnedMenuReady,
} from './globalMenuCapabilities';
import type { GlobalMenuStatus } from './types/api';

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
    capabilities: ['global_menu_v1', 'global_menu_shared_pos_catalog_v1'],
    aggregation_advertised: false,
    resolution_advertised: true,
    mutation_advertised: false,
    shared_pos_catalog_advertised: true,
    resolution_ready: true,
    mutation_ready: false,
    aggregation_ready: false,
    shared_pos_catalog_ready: true,
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

describe('global menu capability ladder', () => {
    it('enables coverage repair in shadow without enabling unrestricted writes', () => {
        const shadow = store(['global_menu_v1', 'global_menu_resolution_v1']);
        expect(hasGlobalMenuCapability(shadow)).toBe(true);
        expect(hasGlobalMenuResolutionCapability(shadow)).toBe(true);
        expect(hasGlobalMenuMutationCapability(shadow)).toBe(false);
        expect(canUseCanonicalMenuControls(shadow)).toBe(false);
    });

    it('keeps resolution hidden without a group and explicit write capability', () => {
        expect(hasGlobalMenuResolutionCapability(store(['global_menu_v1']))).toBe(false);
        expect(hasGlobalMenuResolutionCapability(store(['global_menu_resolution_v1'], null))).toBe(false);
        expect(hasGlobalMenuResolutionCapability(null)).toBe(false);
    });

    it('keeps coverage repair available after full activation', () => {
        const active = store(['global_menu_v1', 'global_menu_mutations_v1']);
        expect(hasGlobalMenuResolutionCapability(active)).toBe(true);
        expect(hasGlobalMenuMutationCapability(active)).toBe(true);
        expect(canUseCanonicalMenuControls(active)).toBe(true);
    });

    it('enables shared POS behavior only with its orthogonal capability', () => {
        const ordinaryGlobal = store(['global_menu_v1', 'global_menu_mutations_v1']);
        const sharedPos = store([
            'global_menu_v1',
            'global_menu_shared_pos_catalog_v1',
            'global_menu_mutations_v1',
        ]);

        expect(hasGlobalMenuSharedPosCatalogCapability(ordinaryGlobal)).toBe(false);
        expect(hasGlobalMenuSharedPosCatalogCapability(sharedPos)).toBe(true);
        expect(hasGlobalMenuSharedPosCatalogCapability(store([
            'global_menu_shared_pos_catalog_v1',
        ], null))).toBe(false);
        expect(hasGlobalMenuSharedPosCatalogCapability(null)).toBe(false);
    });

    it('uses truthful group-owned labels only after the local projection is ready', () => {
        expect(globalMenuViewLabels(false).history).toBe('Resolution History');
        expect(globalMenuViewLabels(true)).toEqual({
            catalog: 'Group Catalog',
            matrix: 'Menu Matrix',
            history: 'Group History',
        });
    });

    it('fails group-owned views closed for stale, mismatched, or All Stores status', () => {
        const shared = store(['global_menu_v1', 'global_menu_shared_pos_catalog_v1']);

        expect(isGroupOwnedMenuReady(shared, status())).toBe(true);
        expect(isGroupOwnedMenuReady(shared, null)).toBe(false);
        expect(isGroupOwnedMenuReady(shared, status({ restaurant_id: 'rest-2' }))).toBe(false);
        expect(isGroupOwnedMenuReady(shared, status({ menu_group_id: 'group-2' }))).toBe(false);
        expect(isGroupOwnedMenuReady(shared, status({ shared_pos_catalog_ready: false }))).toBe(false);
        expect(isGroupOwnedMenuReady(shared, status(), true)).toBe(false);
    });
});
