/**
 * Shared Chart Utilities
 * 
 * Contains common logic for:
 * 1. Filtering by weekdays
 * 2. Grouping by time bucket (Day/Week/Month) with Correct Average Calculation
 * 3. Moving Average Calculation (Strict 7-Day Calendar Window)
 * 4. Holiday Filtering
 */

import { INDIAN_HOLIDAYS } from '../constants/holidays';

export const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
export const DAYS_ABBR = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/**
 * Filter data rows to only include those where the day of week matches selectedDays.
 */
export function filterByWeekdays(data: any[], selectedDays: string[]) {
    if (!data || !data.length) return [];

    return data.filter(row => {
        const date = new Date(row.date);
        const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][date.getDay()];
        return selectedDays.includes(dayName);
    });
}

/**
 * Groups data by Day, Week, or Month.
 * standard Pandas-like resampling: Week ends on Sunday. Month starts on 1st.
 * 
 * Returns array of objects with:
 * - date: bucket key
 * - revenue: sum of revenue
 * - num_orders: sum of num_orders
 * - count: number of valid records in this bucket (for Average calc)
 */
export function groupDataByTimeBucket(data: any[], bucket: string) {
    if (bucket === 'Day') {
        // Just ensure data structure is consistent, though 'count' is 1 per row
        return data.map(d => ({
            ...d,
            count: 1
        }));
    }

    const groups: { [key: string]: any[] } = {};

    data.forEach(row => {
        const dateStr = row.date;
        const [year, month, day] = dateStr.split('-').map(Number);
        let key: string;

        if (bucket === 'Week') {
            // Match pandas resample('W') behavior: week ENDS on Sunday
            const date = new Date(year, month - 1, day);
            const dayOfWeek = date.getDay();  // 0 = Sunday

            // Calculate the Sunday that ends this week
            const weekEnd = new Date(year, month - 1, day + (7 - dayOfWeek) % 7);
            const weekYear = weekEnd.getFullYear();
            const weekMonth = String(weekEnd.getMonth() + 1).padStart(2, '0');
            const weekDay = String(weekEnd.getDate()).padStart(2, '0');
            key = `${weekYear}-${weekMonth}-${weekDay}`;
        } else { // Month
            key = `${year}-${String(month).padStart(2, '0')}-01`;
        }

        if (!groups[key]) groups[key] = [];
        groups[key].push(row);
    });

    return Object.keys(groups).sort().map(key => ({
        date: key,
        revenue: groups[key].reduce((sum, r) => sum + (r.revenue || 0), 0),
        num_orders: groups[key].reduce((sum, r) => sum + (r.num_orders || 0), 0),
        count: groups[key].length // Critical for Average calculation
    }));
}

/**
 * Calculates 7-Day Moving Average using Strict Calendar Window logic.
 * 
 * Logic:
 * For each data point (which is a date):
 * 1. Define window [Current Date - 6 Days, Current Date]
 * 2. Look at ALL original data (sorted).
 * 3. Filter for records inside this window AND that match selectedDays.
 *    (e.g. If Monday is excluded, Monday data is ignored in sum and count).
 * 4. Average = Sum / Count.
 * 
 * @param sortedData - MUST be sorted by date ascending. passed in effectively crude form? No, full dataset.
 * @param selectedDays - List of days to include (Monday, Tuesday, etc.)
 */
export function calculateStrictMA(data: any[], selectedDays: string[]) {
    if (!data.length) return [];

    // Ensure sorted (safe copy)
    const sortedData = [...data].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());

    const computed = sortedData.map((currentPoint) => {
        const currentDate = new Date(currentPoint.date);

        // Define Window: [Current - 6 days, Current]
        const windowStart = new Date(currentDate);
        windowStart.setDate(currentDate.getDate() - 6);

        // Find all records that fall in this date range AND are valid 'selected days'
        const validWindowRecords = sortedData.filter(d => {
            const dDate = new Date(d.date);
            // Check date range
            if (dDate < windowStart || dDate > currentDate) return false;

            // Check if this specific day is allowed
            const dayName = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][dDate.getDay()];
            return selectedDays.includes(dayName);
        });

        // Calculate Average
        const sum = validWindowRecords.reduce((acc, r) => acc + (r.revenue || 0), 0);
        const count = validWindowRecords.length;
        const avg = count > 0 ? sum / count : 0;

        return { ...currentPoint, value: avg };
    });

    // Finally, filter the DISPLAY to only show selected days
    // (We calculated using appropriate window, but we only show points for selected days)
    return filterByWeekdays(computed, selectedDays);
}

/**
 * Get holidays that fall within the range of dates present in the data.
 */
export function getVisibleHolidays(data: any[], showHolidays: boolean) {
    if (!data || !data.length || !showHolidays) return [];

    const dates = data.map(d => d.date);
    // Safe min/max finding for string dates "YYYY-MM-DD"
    const sortedDates = [...dates].sort();
    const minDate = sortedDates[0];
    const maxDate = sortedDates[sortedDates.length - 1];

    return INDIAN_HOLIDAYS.filter(h => h.date >= minDate && h.date <= maxDate);
}
