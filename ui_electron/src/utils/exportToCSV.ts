/**
 * CSV Export Utility
 * 
 * Converts data arrays to CSV format and triggers download.
 * Handles various data types and proper escaping.
 */

/**
 * Export data to a CSV file and trigger download
 * 
 * @param data - Array of objects to export
 * @param filename - Name for the downloaded file (without .csv extension)
 * @param headers - Optional array of column headers (defaults to object keys)
 */
export function exportToCSV(data: Record<string, any>[], filename: string, headers?: string[]): void {
    if (!data || data.length === 0) {
        console.warn('exportToCSV: No data to export');
        return;
    }

    // Use provided headers or derive from first row
    const cols = headers || Object.keys(data[0]);

    // Build CSV content
    const csvContent = [
        // Header row
        cols.join(','),
        // Data rows
        ...data.map(row =>
            cols.map(col => {
                const val = row[col];

                // Handle null/undefined
                if (val === null || val === undefined) {
                    return '';
                }

                // Handle strings that need quoting
                if (typeof val === 'string') {
                    // Escape quotes and wrap in quotes if contains comma, newline, or quote
                    if (val.includes(',') || val.includes('\n') || val.includes('"')) {
                        return `"${val.replace(/"/g, '""')}"`;
                    }
                    return val;
                }

                return String(val);
            }).join(',')
        )
    ].join('\n');

    // Create and trigger download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.setAttribute('href', url);
    link.setAttribute('download', `${filename}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}
