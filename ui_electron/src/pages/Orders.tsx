import { useState } from 'react';
import { endpoints } from '../api';
import { PaginatedDataTable } from '../components';

export default function Orders({ lastDbSync }: { lastDbSync?: number }) {
    const [activeTab, setActiveTab] = useState<'orders' | 'items' | 'restaurants' | 'taxes' | 'discounts'>('orders');

    const tabs = [
        { id: 'orders', label: '🛒 Orders' },
        { id: 'items', label: '📦 Order Items' },
        { id: 'restaurants', label: '🍽️ Restaurants' },
        { id: 'taxes', label: '📊 Taxes' },
        { id: 'discounts', label: '💰 Discounts' },
    ];

    return (
        <div className="page-container" style={{ padding: '20px', fontFamily: 'Inter, sans-serif' }}>
            <div style={{ display: 'flex', gap: '5px', marginBottom: '30px', background: 'white', padding: '5px', borderRadius: '30px', overflowX: 'auto', boxShadow: '0 2px 10px rgba(0,0,0,0.1)' }}>
                {tabs.map((tab) => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id as typeof activeTab)}
                        style={{
                            flex: 1,
                            padding: '12px',
                            background: activeTab === tab.id ? '#3B82F6' : 'transparent',
                            border: 'none',
                            color: activeTab === tab.id ? 'white' : 'black',
                            cursor: 'pointer',
                            borderRadius: '25px',
                            transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                            fontWeight: activeTab === tab.id ? 600 : 500,
                            minWidth: '120px',
                            boxShadow: activeTab === tab.id ? '0 2px 5px rgba(96, 165, 250, 0.4)' : 'none'
                        }}
                    >
                        {tab.label}
                    </button>
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
