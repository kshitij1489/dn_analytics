import type { GlobalMenuStatus, Store } from './types/api';

export const GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY = 'global_menu_shared_pos_catalog_v1';

export function hasGlobalMenuCapability(store?: Store | null): boolean {
    return Boolean(
        store?.menu_group_id &&
        store.menu_capabilities?.includes('global_menu_v1'),
    );
}

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

export function canUseCanonicalMenuControls(store?: Store | null): boolean {
    return !hasGlobalMenuCapability(store) || hasGlobalMenuMutationCapability(store);
}

export function isGroupOwnedMenuReady(
    store: Store | null | undefined,
    status: GlobalMenuStatus | null | undefined,
    isAllStores = false,
): boolean {
    return Boolean(
        !isAllStores &&
        store &&
        status &&
        hasGlobalMenuSharedPosCatalogCapability(store) &&
        status.active &&
        status.shared_pos_catalog_ready &&
        status.restaurant_id === store.restaurant_id &&
        status.menu_group_id === store.menu_group_id,
    );
}

export function globalMenuViewLabels(groupOwnedReady: boolean) {
    return {
        catalog: groupOwnedReady ? 'Group Catalog' : 'Menu Items',
        matrix: groupOwnedReady ? 'Menu Matrix' : 'Menu Matrix',
        history: groupOwnedReady ? 'Group History' : 'Resolution History',
    };
}
