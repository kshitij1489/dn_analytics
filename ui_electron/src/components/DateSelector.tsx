import { useRef } from 'react';

interface DateSelectorProps {
    value: string; // YYYY-MM-DD format from backend
    displayValue: string; // What to show (e.g., "2026-02-09")
    onChange: (date: string) => void;
    maxDate?: string; // Max selectable date (YYYY-MM-DD)
}

/**
 * DateSelector - A clickable date label with calendar icon
 * 
 * Displays the date in a label format with a calendar icon.
 * Clicking opens the native date picker.
 */
export function DateSelector({ value, displayValue, onChange, maxDate }: DateSelectorProps) {
    const inputRef = useRef<HTMLInputElement>(null);

    const handleClick = (e: React.MouseEvent) => {
        e.preventDefault();
        inputRef.current?.showPicker?.();
    };

    return (
        <label className="date-selector-label" onClick={handleClick}>
            <span className="date-selector-icon">📅</span>
            <span className="date-selector-text">{displayValue}</span>
            <span className="date-selector-suffix">(5 AM - 5 AM)</span>
            <input
                ref={inputRef}
                type="date"
                value={value}
                max={maxDate}
                onChange={(e) => onChange(e.target.value)}
                className="date-selector-hidden-input"
            />
        </label>
    );
}
