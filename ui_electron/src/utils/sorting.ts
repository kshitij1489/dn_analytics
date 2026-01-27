/**
 * Sorting Utilities
 * 
 * Common sorting helpers used across table components.
 */

export type SortDirection = 'asc' | 'desc';

export interface SortConfig {
    key: string;
    direction: SortDirection;
}

/**
 * Generic sorting function for arrays of objects
 * 
 * @param data - Array of objects to sort
 * @param sortKey - Object key to sort by
 * @param direction - Sort direction ('asc' or 'desc')
 * @returns Sorted copy of the array
 */
export function sortData<T extends Record<string, any>>(
    data: T[],
    sortKey: string,
    direction: SortDirection
): T[] {
    return [...data].sort((a, b) => {
        const aVal = a[sortKey];
        const bVal = b[sortKey];

        // Handle null/undefined
        if (aVal == null && bVal == null) return 0;
        if (aVal == null) return direction === 'asc' ? 1 : -1;
        if (bVal == null) return direction === 'asc' ? -1 : 1;

        // Numeric comparison
        if (typeof aVal === 'number' && typeof bVal === 'number') {
            return direction === 'asc' ? aVal - bVal : bVal - aVal;
        }

        // String comparison
        const aStr = String(aVal).toLowerCase();
        const bStr = String(bVal).toLowerCase();

        if (aStr < bStr) return direction === 'asc' ? -1 : 1;
        if (aStr > bStr) return direction === 'asc' ? 1 : -1;
        return 0;
    });
}

/**
 * Toggle sort direction or set new sort key
 * 
 * @param currentKey - Currently sorted key
 * @param currentDirection - Current sort direction
 * @param newKey - Key being clicked
 * @returns New sort configuration
 */
export function getNextSortConfig(
    currentKey: string,
    currentDirection: SortDirection,
    newKey: string
): SortConfig {
    if (currentKey === newKey) {
        return {
            key: newKey,
            direction: currentDirection === 'asc' ? 'desc' : 'asc'
        };
    }
    return {
        key: newKey,
        direction: 'asc'
    };
}

/**
 * Get sort icon for column header
 * 
 * @param columnKey - Column being rendered
 * @param sortKey - Currently sorted key
 * @param direction - Current sort direction
 * @returns Icon string
 */
export function getSortIcon(
    columnKey: string,
    sortKey: string,
    direction: SortDirection
): string {
    if (sortKey !== columnKey) {
        return ' ⇅'; // Neutral icon
    }
    return direction === 'asc' ? ' ↑' : ' ↓';
}
