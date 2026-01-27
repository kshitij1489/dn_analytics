/**
 * Select Component
 * 
 * Styled dropdown select with consistent theming.
 */

interface SelectOption {
    value: string;
    label: string;
}

interface SelectProps {
    value: string;
    onChange: (value: string) => void;
    options: SelectOption[];
    placeholder?: string;
    className?: string;
    disabled?: boolean;
}

export function Select({
    value,
    onChange,
    options,
    placeholder,
    className = '',
    disabled = false
}: SelectProps) {
    return (
        <select
            value={value}
            onChange={e => onChange(e.target.value)}
            disabled={disabled}
            className={`styled-select ${className}`}
        >
            {placeholder && (
                <option value="" disabled>{placeholder}</option>
            )}
            {options.map(opt => (
                <option key={opt.value} value={opt.value}>
                    {opt.label}
                </option>
            ))}
        </select>
    );
}

/**
 * Helper to create options from string array
 */
export function createOptions(values: string[]): SelectOption[] {
    return values.map(v => ({ value: v, label: v }));
}
