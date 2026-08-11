import { ALL_STORES_SCOPE } from '../api';
import { useStore } from '../contexts/StoreContext';

/** Local value for the All Stores read-only federation. */
export const ALL_STORES_VALUE = ALL_STORES_SCOPE;

/**
 * Sidebar store picker backed only by the authenticated server list.
 *
 * All Stores becomes selectable once at least two authorized restaurants have an
 * initialized database. It is a local read scope: state-changing actions still
 * require one physical restaurant.
 */
export function StoreSelector() {
    const {
        stores, selectedStore, selectStore, loading, error, scopeLocked,
        isAllStores, allStores, completeness,
    } = useStore();

    // A mutation awaiting its central commit owns the current restaurant until it
    // settles; switching underneath would attribute the result to the wrong store.
    const disabled = loading || scopeLocked;

    const includedStores = completeness?.profilesIncluded ?? allStores.member_count;
    const identity = completeness?.identityCoverage;
    const identityStatus = identity?.global_aggregation_active
        ? ` · global menu identity ${identity.linked}/${identity.total}`
        : identity?.global_mode_active
            ? ` · legacy menu grouping (global coverage ${identity.linked}/${identity.total})`
            : '';
    const allStoresStatus = `All Stores · ${includedStores} of ${allStores.member_count} available${identityStatus}`;

    const status = error
        || (scopeLocked ? 'Finishing a pending change…' : null)
        || (isAllStores ? allStoresStatus : null)
        || (selectedStore ? `${selectedStore.display_name} · ${selectedStore.timezone}` : 'Choose a physical restaurant to begin');

    return (
        <div className="store-selector-pill">
            <select
                value={isAllStores ? ALL_STORES_VALUE : selectedStore?.restaurant_id ?? ''}
                onChange={(e) => void selectStore(e.target.value)}
                disabled={disabled}
                aria-label="Select store"
                aria-busy={scopeLocked}
                aria-describedby="store-selector-status"
                title={scopeLocked ? 'A change is awaiting confirmation from the server' : undefined}
            >
                <option value="" disabled>Select a restaurant</option>
                <option value={ALL_STORES_VALUE} disabled={!allStores.available}>
                    {allStores.available
                        ? `All Stores (${allStores.member_count})`
                        : 'All Stores — needs two synced restaurants'}
                </option>
                {stores.map((store) => (
                    <option key={store.restaurant_id} value={store.restaurant_id}>
                        {store.display_name}{store.authorization_state !== 'authorized' ? ' (offline only)' : ''}
                    </option>
                ))}
            </select>
            <div id="store-selector-status" className="store-selector-status">
                {status}
            </div>
        </div>
    );
}
