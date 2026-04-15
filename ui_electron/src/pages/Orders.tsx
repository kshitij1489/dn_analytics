import { useState } from 'react';
import { endpoints } from '../api';
import { PaginatedDataTable, TabButton } from '../components';

export default function Orders({ lastDbSync }: { lastDbSync?: number }) {
    const [activeTab, setActiveTab] = useState<'orders' | 'items' | 'restaurants' | 'taxes' | 'discounts'>('orders');

    const tabs = [
        { id: 'orders', label: '🛒 Orders' },
        { id: 'items', label: '📦 Order Items' },
        { id: 'restaurants', label: '🍽️ Restaurants' },
        { id: 'taxes', label: '📊 Taxes' },
        { id: 'discounts', label: '💰 Discounts' },
    ] as const;

    return (
        <div className="page-container" style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>
            <div className="segmented-control segmented-page-tabs" style={{ marginBottom: '20px' }}>
                {tabs.map((tab) => (
                    <TabButton
                        key={tab.id}
                        active={activeTab === tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        variant="segmented"
                        size="large"
                    >
                        {tab.label}
                    </TabButton>
                ))}
            </div>

            {activeTab === 'orders' && <PaginatedDataTable title="Orders" apiCall={endpoints.orders.orders} defaultSort="created_on" lastDbSync={lastDbSync} />}
            {activeTab === 'items' && <PaginatedDataTable title="Order Items" apiCall={endpoints.orders.items} defaultSort="created_at" lastDbSync={lastDbSync} />}
            {activeTab === 'restaurants' && <PaginatedDataTable title="Restaurants" apiCall={endpoints.orders.restaurants} defaultSort="restaurant_id" lastDbSync={lastDbSync} />}
            {activeTab === 'taxes' && <PaginatedDataTable title="Taxes" apiCall={endpoints.orders.taxes} defaultSort="created_at" lastDbSync={lastDbSync} />}
            {activeTab === 'discounts' && <PaginatedDataTable title="Discounts" apiCall={endpoints.orders.discounts} defaultSort="created_at" lastDbSync={lastDbSync} />}
        </div>
    );
}
