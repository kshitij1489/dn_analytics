import { useRef } from 'react';
import './DateSelector.css';

interface DateSelectorProps {
    value: string; // YYYY-MM-DD format from backend
    displayValue: string; // What to show (e.g., "2026-02-09")
    onChange: (date: string) => void;
    maxDate?: string; // Max selectable date (YYYY-MM-DD)
    minDate?: string; // Min selectable date (YYYY-MM-DD)
    suffix?: string;
}

/**
 * DateSelector - A clickable date label with calendar icon
 * 
 * Displays the date in a label format with a calendar icon.
 * Clicking opens the native date picker.
 */
export function DateSelector({
    value,
    displayValue,
    onChange,
    maxDate,
    minDate,
    suffix = '(5 AM - 5 AM)',
}: DateSelectorProps) {
    const inputRef = useRef<HTMLInputElement>(null);

    const handleClick = (e: React.MouseEvent) => {
        e.preventDefault();
        inputRef.current?.showPicker?.();
    };

    return (
        <label className="date-selector-label" onClick={handleClick}>
            <span className="date-selector-icon">📅</span>
            <span className="date-selector-text">{displayValue}</span>
            {suffix ? <span className="date-selector-suffix">{suffix}</span> : null}
            <input
                ref={inputRef}
                type="date"
                value={value}
                min={minDate}
                max={maxDate}
                onChange={(e) => onChange(e.target.value)}
                className="date-selector-hidden-input"
            />
        </label>
    );
}
