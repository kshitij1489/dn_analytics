/**
 * Format Column Header Utility
 * 
 * Converts database column names (snake_case) to Title Case display labels.
 * Handles special cases like ID, API, SAP, etc.
 */

const SPECIAL_CASES: Record<string, string> = {
    id: 'ID',
    api: 'API',
    csv: 'CSV',
    url: 'URL',
    sap: 'SAP',
    gstin: 'GSTIN',
    kpi: 'KPI',
};

/**
 * Converts a snake_case column key to a Title Case label.
 * @param key - The column key (e.g., 'variant_id', 'created_at')
 * @returns Formatted label (e.g., 'Variant ID', 'Created At')
 */
export function formatColumnHeader(key: string): string {
    return key
        .split('_')
        .map(word =>
            SPECIAL_CASES[word.toLowerCase()] ||
            word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
        )
        .join(' ');
}
