/**
 * Shared types, constants, and utility functions for customer analytics views.
 */

import type { Dispatch, SetStateAction } from 'react';

// --- Filter types ---

export interface EvaluationFilters {
    evaluationStartDate: string;
    evaluationEndDate: string;
}

export interface MetricFilters extends EvaluationFilters {
    minOrdersPerCustomer: string;
    orderSource: string;
}

export interface LookbackFilters extends MetricFilters {
    lookbackStartDate: string;
    lookbackEndDate: string;
}

// --- Sortable table types ---

export type SortableValue = string | number | null | undefined;

export interface SortState {
    sortKey: string | null;
    handleSort: (key: string) => void;
    renderSortIcon: (key: string) => string;
    sortRows: <T extends Record<string, SortableValue>>(rows: T[]) => T[];
    resetSort: () => void;
}

// --- Constants ---

export const ORDER_SOURCE_OPTIONS = [
    { value: 'All', label: 'All Sources' },
    { value: 'POS', label: 'POS' },
    { value: 'Swiggy', label: 'Swiggy' },
    { value: 'Zomato', label: 'Zomato' },
    { value: 'Home Website', label: 'Home Website' },
];

export const MIN_ORDER_OPTIONS = [
    { value: '2', label: '>= 2 Orders' },
    { value: '3', label: '>= 3 Orders' },
    { value: '4', label: '>= 4 Orders' },
    { value: '5', label: '>= 5 Orders' },
];

export const RETENTION_MIN_ORDER_OPTIONS = [
    { value: '1', label: '>= 1 Orders' },
    ...MIN_ORDER_OPTIONS,
];

// --- Date utilities ---

function formatDateUTC(date: Date) {
    return date.toISOString().slice(0, 10);
}

export function getCurrentDateISO() {
    const now = new Date();
    return [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, '0'),
        String(now.getDate()).padStart(2, '0'),
    ].join('-');
}

export function getMonthStartISO(dateISO: string) {
    const [year, month] = dateISO.split('-').map(Number);
    return formatDateUTC(new Date(Date.UTC(year, month - 1, 1)));
}

export function shiftMonthStartISO(dateISO: string, offset: number) {
    const [year, month] = dateISO.split('-').map(Number);
    return formatDateUTC(new Date(Date.UTC(year, month - 1 + offset, 1)));
}

export function getMonthEndISO(monthStartISO: string) {
    const [year, month] = monthStartISO.split('-').map(Number);
    return formatDateUTC(new Date(Date.UTC(year, month, 0)));
}

// --- Filter defaults ---

export function buildDefaultLookbackFilters(): LookbackFilters {
    const currentDate = getCurrentDateISO();
    const currentMonthStart = getMonthStartISO(currentDate);
    const previousMonthStart = shiftMonthStartISO(currentMonthStart, -1);

    return {
        evaluationStartDate: currentMonthStart,
        evaluationEndDate: currentDate,
        lookbackStartDate: previousMonthStart,
        lookbackEndDate: getMonthEndISO(previousMonthStart),
        minOrdersPerCustomer: '2',
        orderSource: 'All',
    };
}

export function buildDefaultMetricFilters(): MetricFilters {
    const currentDate = getCurrentDateISO();
    const currentMonthStart = getMonthStartISO(currentDate);

    return {
        evaluationStartDate: currentMonthStart,
        evaluationEndDate: currentDate,
        minOrdersPerCustomer: '2',
        orderSource: 'All',
    };
}

// --- Filter updaters ---

export function updateEvaluationStartDate<T extends EvaluationFilters>(
    setFilters: Dispatch<SetStateAction<T>>,
    date: string,
) {
    setFilters((current) => ({
        ...current,
        evaluationStartDate: date,
        evaluationEndDate: current.evaluationEndDate < date ? date : current.evaluationEndDate,
    }));
}

export function updateEvaluationEndDate<T extends EvaluationFilters>(
    setFilters: Dispatch<SetStateAction<T>>,
    date: string,
) {
    setFilters((current) => ({
        ...current,
        evaluationStartDate: current.evaluationStartDate > date ? date : current.evaluationStartDate,
        evaluationEndDate: date,
    }));
}

export function updateLookbackStartDate<T extends LookbackFilters>(
    setFilters: Dispatch<SetStateAction<T>>,
    date: string,
) {
    setFilters((current) => ({
        ...current,
        lookbackStartDate: date,
        lookbackEndDate: current.lookbackEndDate < date ? date : current.lookbackEndDate,
    }));
}

export function updateLookbackEndDate<T extends LookbackFilters>(
    setFilters: Dispatch<SetStateAction<T>>,
    date: string,
) {
    setFilters((current) => ({
        ...current,
        lookbackStartDate: current.lookbackStartDate > date ? date : current.lookbackStartDate,
        lookbackEndDate: date,
    }));
}

// --- Param builders ---

export function buildLookbackParams(filters: LookbackFilters, lastDbSync?: number) {
    return {
        _t: lastDbSync,
        evaluation_start_date: filters.evaluationStartDate,
        evaluation_end_date: filters.evaluationEndDate,
        lookback_start_date: filters.lookbackStartDate,
        lookback_end_date: filters.lookbackEndDate,
        min_orders_per_customer: Number(filters.minOrdersPerCustomer),
        order_sources: filters.orderSource === 'All' ? undefined : [filters.orderSource],
    };
}

export function buildMetricParams(filters: MetricFilters, lastDbSync?: number) {
    return {
        _t: lastDbSync,
        evaluation_start_date: filters.evaluationStartDate,
        evaluation_end_date: filters.evaluationEndDate,
        min_orders_per_customer: Number(filters.minOrdersPerCustomer),
        order_sources: filters.orderSource === 'All' ? undefined : [filters.orderSource],
    };
}

/** Customer affinity API: evaluation window + order source only (no order-count threshold). */
export function buildAffinityParams(filters: MetricFilters, lastDbSync?: number) {
    return {
        _t: lastDbSync,
        evaluation_start_date: filters.evaluationStartDate,
        evaluation_end_date: filters.evaluationEndDate,
        order_sources: filters.orderSource === 'All' ? undefined : [filters.orderSource],
    };
}

// --- Format helpers ---

export function getErrorMessage(error: any) {
    return error?.response?.data?.detail || error?.message || 'Failed to load customer analytics.';
}

export function formatPercentage(value?: number | null) {
    if (value == null || Number.isNaN(value)) return '0.00%';
    return `${value.toFixed(2)}%`;
}

export function formatCurrency(value?: number | null) {
    return `Rs ${Number(value || 0).toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 2,
    })}`;
}

export function formatOptionalDate(value?: string | null) {
    return value || '-';
}
