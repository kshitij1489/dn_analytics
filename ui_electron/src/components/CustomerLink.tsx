import React from 'react';
import { useNavigation } from '../contexts/NavigationContext';
import { useStore } from '../contexts/StoreContext';

interface CustomerLinkProps {
    customerId: number | string;
    name: string;
    /** Owning restaurant, present on All Stores rows. */
    restaurantId?: string | null;
    restaurantName?: string | null;
    className?: string;
    style?: React.CSSProperties;
}

export function CustomerLink({
    customerId, name, restaurantId, restaurantName, className, style,
}: CustomerLinkProps) {
    const { navigate } = useNavigation();
    const { isAllStores, stores, selectStore } = useStore();

    const openProfile = () => {
        navigate('customers', {
            section: 'profiles',
            mode: 'profile',
            customerId: customerId,
        });
    };

    // A customer profile is a single-store surface: local customer IDs are not
    // globally unique. From All Stores, switch to the owning restaurant first.
    const activate = () => {
        if (!isAllStores) {
            openProfile();
            return;
        }
        if (!restaurantId) {
            window.alert('Select one physical restaurant to open a customer profile.');
            return;
        }
        const label = restaurantName
            || stores.find((store) => store.restaurant_id === restaurantId)?.display_name
            || restaurantId;
        if (!window.confirm(`Open this customer in ${label}? The app will switch to that restaurant.`)) {
            return;
        }
        void selectStore(restaurantId).then(openProfile);
    };

    const handleClick = (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        activate();
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            activate();
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
            title={isAllStores && restaurantName ? `View Customer Profile · ${restaurantName}` : 'View Customer Profile'}
        >
            {name}
        </span>
    );
}
