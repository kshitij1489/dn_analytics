/**
 * CSV Export Utility
 * 
 * Exports data to CSV format with proper handling of special characters.
 */

export function exportToCSV(data: any[], filename: string, headers?: string[]): boolean {
    if (!data || data.length === 0) {
        return false;
    }

    // Get headers from first data item if not provided
    const csvHeaders = headers || Object.keys(data[0]);

    // Create CSV content
    const csvRows = [];

    // Add header row
    csvRows.push(csvHeaders.join(','));

    // Add data rows
    for (const row of data) {
        const values = csvHeaders.map(header => {
            const value = row[header];
            // Handle values that might contain commas or quotes
            if (value == null) return '';
            const stringValue = String(value);
            if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
                return `"${stringValue.replace(/"/g, '""')}"`;
            }
            return stringValue;
        });
        csvRows.push(values.join(','));
    }

    const csvContent = csvRows.join('\n');

    // Create blob and trigger download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    link.setAttribute('href', url);
    link.setAttribute('download', `${filename}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    return true;
}
