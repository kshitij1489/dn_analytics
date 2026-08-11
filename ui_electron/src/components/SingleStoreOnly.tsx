import type { ReactNode } from 'react';
import { useStore } from '../contexts/StoreContext';

/**
 * Gate for surfaces that change state or resolve one store's records.
 *
 * All Stores is a read-only federation: menu and customer mutations, merge/undo,
 * resolutions, remap, manual pulls, and reset always require one physical
 * restaurant. The backend refuses these routes in All mode too — this only makes
 * the reason visible before the request is sent.
 */
export function SingleStoreOnly({ what, children }: { what: string; children: ReactNode }) {
    const { isAllStores, allStores } = useStore();

    if (!isAllStores) return <>{children}</>;

    return (
        <div className="card" style={{ maxWidth: 680, margin: '24px auto' }}>
            <h3>{what} needs one restaurant</h3>
            <p>
                All Stores combines {allStores.member_count} restaurants for reading only. A change here
                belongs to exactly one store's database, so pick that restaurant in the sidebar first.
            </p>
        </div>
    );
}
