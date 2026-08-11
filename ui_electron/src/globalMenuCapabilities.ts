import type { Store } from './types/api';

export const GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY = 'global_menu_shared_pos_catalog_v1';

export function hasGlobalMenuMutationCapability(store?: Store | null): boolean {
    return Boolean(
        store?.menu_group_id &&
        store.menu_capabilities?.includes('global_menu_mutations_v1'),
    );
}

export function hasGlobalMenuResolutionCapability(store?: Store | null): boolean {
    return Boolean(
        store?.menu_group_id && (
            store.menu_capabilities?.includes('global_menu_resolution_v1') ||
            store.menu_capabilities?.includes('global_menu_mutations_v1')
        ),
    );
}

export function hasGlobalMenuSharedPosCatalogCapability(store?: Store | null): boolean {
    return Boolean(
        store?.menu_group_id &&
        store.menu_capabilities?.includes(GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY),
    );
}
