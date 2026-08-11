import { describe, expect, it } from 'vitest';
import type { Store } from './types/api';
import {
    hasGlobalMenuMutationCapability,
    hasGlobalMenuResolutionCapability,
    hasGlobalMenuSharedPosCatalogCapability,
} from './globalMenuCapabilities';

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

describe('global menu capability ladder', () => {
    it('enables coverage repair in shadow without enabling unrestricted writes', () => {
        const shadow = store(['global_menu_v1', 'global_menu_resolution_v1']);
        expect(hasGlobalMenuResolutionCapability(shadow)).toBe(true);
        expect(hasGlobalMenuMutationCapability(shadow)).toBe(false);
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
});
