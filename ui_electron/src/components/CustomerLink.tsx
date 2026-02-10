import React from 'react';
import { useNavigation } from '../contexts/NavigationContext';

interface CustomerLinkProps {
    customerId: number | string;
    name: string;
    className?: string;
    style?: React.CSSProperties;
}

export function CustomerLink({ customerId, name, className, style }: CustomerLinkProps) {
    const { navigate } = useNavigation();

    const handleClick = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        navigate('orders', {
            view: 'customers',
            mode: 'profile',
            customerId: customerId
        });
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            navigate('orders', {
                view: 'customers',
                mode: 'profile',
                customerId: customerId
            });
        }
    };

    return (
        <span
            role="link"
            tabIndex={0}
            onClick={handleClick}
            onKeyDown={handleKeyDown}
            className={className}
            style={{
                cursor: 'pointer',
                color: 'var(--accent-color, #3B82F6)',
                textDecoration: 'underline',
                fontWeight: '500',
                ...style
            }}
            title="View Customer Profile"
        >
            {name}
        </span>
    );
}
