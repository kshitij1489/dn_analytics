import type { GlobalMenuStatus, Store } from './types/api';

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

export function isGlobalMenuCatalogReadable(
    store: Store | null | undefined,
    status: GlobalMenuStatus | null | undefined,
    isAllStores = false,
): boolean {
    return Boolean(
        !isAllStores &&
        store &&
        status &&
        hasGlobalMenuCapability(store) &&
        status.active &&
        status.restaurant_id === store.restaurant_id &&
        status.menu_group_id === store.menu_group_id,
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
    return isGlobalMenuCatalogReadable(store, status, isAllStores);
}

export function globalMenuViewLabels(groupOwnedReady: boolean) {
    return {
        catalog: groupOwnedReady ? 'Group Catalog' : 'Menu Items',
        matrix: groupOwnedReady ? 'Menu Matrix' : 'Menu Matrix',
        history: groupOwnedReady ? 'Group History' : 'Resolution History',
    };
}
