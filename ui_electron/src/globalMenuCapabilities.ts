import type { GlobalMenuStatus, Store } from './types/api';

export const GLOBAL_MENU_SHARED_POS_CATALOG_CAPABILITY = 'global_menu_shared_pos_catalog_v1';
export const GLOBAL_MENU_GROUP_POS_ALIASES_CAPABILITY = 'global_menu_group_pos_aliases_v1';

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

export function hasGlobalMenuGroupPosAliasesCapability(store?: Store | null): boolean {
    return Boolean(
        store?.menu_group_id &&
        store.menu_capabilities?.includes(GLOBAL_MENU_GROUP_POS_ALIASES_CAPABILITY),
    );
}

export function hasGlobalMenuPosPolicyConflict(store?: Store | null): boolean {
    return hasGlobalMenuSharedPosCatalogCapability(store) &&
        hasGlobalMenuGroupPosAliasesCapability(store);
}

export function isGlobalMenuAliasReviewAvailable(
    store?: Store | null,
    isAllStores = false,
): boolean {
    return Boolean(
        !isAllStores &&
        hasGlobalMenuCapability(store) &&
        hasGlobalMenuResolutionCapability(store) &&
        !hasGlobalMenuPosPolicyConflict(store),
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
        !hasGlobalMenuPosPolicyConflict(store) &&
        !status.pos_policy_conflict &&
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
    const sharedPos = hasGlobalMenuSharedPosCatalogCapability(store);
    const groupPosAliases = hasGlobalMenuGroupPosAliasesCapability(store);
    const selectedPolicyReady = sharedPos
        ? Boolean(status?.shared_pos_catalog_ready)
        : groupPosAliases
            ? Boolean(status?.group_pos_aliases_ready)
            : false;
    return Boolean(
        !isAllStores &&
        store &&
        status &&
        (sharedPos || groupPosAliases) &&
        (!groupPosAliases || hasGlobalMenuResolutionCapability(store)) &&
        !hasGlobalMenuPosPolicyConflict(store) &&
        !status.pos_policy_conflict &&
        status.active &&
        status.group_pos_policy_ready &&
        selectedPolicyReady &&
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
