
import { useState, useEffect, useCallback } from 'react';
import { endpoints } from '../api';
import _ from 'lodash';

interface CustomerSearchResponse {
    customer_id: string;
    name: string;
    phone: string;
    total_spent: number;
    last_order_date: string;
    is_verified: boolean;
}

interface CustomerProfileOrder {
    order_id: string;
    order_number: string;
    created_on: string;
    items_summary: string;
    total_amount: number;
    order_source: string;
    status: string;
}

interface CustomerProfileData {
    customer: CustomerSearchResponse;
    orders: CustomerProfileOrder[];
}

export function CustomerProfile({ headerActions }: { headerActions?: React.ReactNode }) {
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<CustomerSearchResponse[]>([]);
    const [selectedCustomer, setSelectedCustomer] = useState<CustomerProfileData | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadingProfile, setLoadingProfile] = useState(false);

    // Debounced Search
    const debouncedSearch = useCallback(
        _.debounce(async (q: string) => {
            if (!q || q.length < 2) {
                setSearchResults([]);
                return;
            }
            setLoading(true);
            try {
                const res = await endpoints.customers.search(q);
                setSearchResults(res.data);
            } catch (err) {
                console.error("Search failed", err);
            } finally {
                setLoading(false);
            }
        }, 500),
        []
    );

    useEffect(() => {
        debouncedSearch(searchQuery);
        return () => debouncedSearch.cancel(); // Cleanup on unmount
    }, [searchQuery, debouncedSearch]);

    const handleSelectCustomer = async (customer: CustomerSearchResponse) => {
        setLoadingProfile(true);
        setSearchResults([]); // Clear results to hide dropdown
        setSearchQuery(customer.name); // Set input to selected Name
        try {
            const res = await endpoints.customers.profile(customer.customer_id);
            setSelectedCustomer(res.data);
        } catch (err) {
            console.error("Profile fetch failed", err);
            alert("Failed to load profile");
        } finally {
            setLoadingProfile(false);
        }
    };

    return (
        <div style={{ padding: '0', maxWidth: '100%', margin: '0' }}>
            {/* Header / Search Section */}
            <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '10px',
                gap: '20px'
            }}>
                <div>{headerActions}</div>
                <div style={{ flex: 1, position: 'relative', maxWidth: '500px' }}>
                    <input
                        type="text"
                        placeholder="Search by Name or Phone or Customer ID..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        style={{
                            width: '100%',
                            padding: '8px 20px',
                            fontSize: '14px',
                            borderRadius: '25px',
                            border: '1px solid var(--border-color)',
                            background: 'var(--input-bg)',
                            color: 'var(--text-color)',
                            outline: 'none',
                        }}
                    />

                    {/* Loader */}
                    {loading && (
                        <div style={{ position: 'absolute', right: '15px', top: '10px', color: 'var(--text-secondary)', fontSize: '12px' }}>
                            Searching...
                        </div>
                    )}

                    {/* Dropdown Results */}
                    {searchResults.length > 0 && (
                        <div style={{
                            position: 'absolute',
                            top: '100%',
                            left: 0,
                            right: 0,
                            background: 'var(--card-bg)',
                            border: '1px solid var(--border-color)',
                            borderRadius: '10px',
                            marginTop: '5px',
                            zIndex: 100,
                            boxShadow: '0 4px 15px rgba(0,0,0,0.1)',
                            maxHeight: '300px',
                            overflowY: 'auto'
                        }}>
                            {searchResults.map(c => (
                                <div
                                    key={c.customer_id}
                                    onClick={() => handleSelectCustomer(c)}
                                    style={{
                                        padding: '12px 20px',
                                        borderBottom: '1px solid var(--border-color)',
                                        cursor: 'pointer',
                                        display: 'flex',
                                        justifyContent: 'space-between',
                                        alignItems: 'center'
                                    }}
                                    onMouseEnter={(e) => e.currentTarget.style.background = 'var(--hover-bg)'}
                                    onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                                >
                                    <div>
                                        <div style={{ fontWeight: '600', color: 'var(--text-color)' }}>{c.name}</div>
                                        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{c.phone}</div>
                                    </div>
                                    <div style={{ fontSize: '13px', textAlign: 'right', color: 'var(--text-secondary)' }}>
                                        <div>₹{Math.round(c.total_spent)}</div>
                                        <div>Last: {c.last_order_date?.split('T')[0]}</div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {/* Profile Content */}
            {loadingProfile && <div style={{ textAlign: 'center', padding: '40px' }}>Loading Profile...</div>}

            {!loadingProfile && selectedCustomer && (
                <div style={{ animation: 'fadeIn 0.3s ease-in-out' }}>
                    {/* Customer Header Card */}
                    <div className="card" style={{ padding: '20px', marginBottom: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <h2 style={{ margin: '0', fontSize: '24px' }}>{selectedCustomer.customer.name}</h2>
                                {selectedCustomer.customer.is_verified ? (
                                    <span style={{ fontSize: '12px', background: 'rgba(16, 185, 129, 0.1)', color: '#10B981', padding: '2px 8px', borderRadius: '12px', fontWeight: '600', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
                                        Verified
                                    </span>
                                ) : (
                                    <span style={{ fontSize: '12px', background: 'rgba(107, 114, 128, 0.1)', color: '#6B7280', padding: '2px 8px', borderRadius: '12px', fontWeight: '600', border: '1px solid rgba(107, 114, 128, 0.2)' }}>
                                        Not Verified
                                    </span>
                                )}
                            </div>
                            <div style={{ color: 'var(--text-secondary)', fontSize: '14px', marginTop: '5px' }}>
                                📞 {selectedCustomer.customer.phone || 'No Phone'}
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: '20px' }}>
                            <div style={{ textAlign: 'center' }}>
                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', textTransform: 'uppercase' }}>Total Orders</div>
                                <div style={{ fontSize: '20px', fontWeight: 'bold' }}>{selectedCustomer.orders.length}</div>
                            </div>
                            <div style={{ textAlign: 'center' }}>
                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)', textTransform: 'uppercase' }}>Total Spent</div>
                                <div style={{ fontSize: '20px', fontWeight: 'bold', color: 'var(--accent-color)' }}>
                                    ₹{Math.round(selectedCustomer.customer.total_spent)}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Order History Table */}
                    <div className="card" style={{ padding: '0', overflow: 'hidden' }}>
                        <table className="standard-table">
                            <thead>
                                <tr>
                                    <th>Date</th>
                                    <th>Order No</th>
                                    <th>Items</th>
                                    <th>Total</th>
                                    <th>Source</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody>
                                {selectedCustomer.orders.length === 0 ? (
                                    <tr>
                                        <td colSpan={6} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
                                            No orders yet for this customer.
                                        </td>
                                    </tr>
                                ) : (
                                    selectedCustomer.orders.map(order => (
                                        <tr key={order.order_id}>
                                            <td style={{ whiteSpace: 'nowrap' }}>
                                                {new Date(order.created_on).toLocaleDateString()}
                                                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                                                    {new Date(order.created_on).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </div>
                                            </td>
                                            <td>{order.order_number}</td>
                                            <td>
                                                <div style={{ maxWidth: '400px', whiteSpace: 'normal', fontSize: '13px' }}>
                                                    {order.items_summary}
                                                </div>
                                            </td>
                                            <td style={{ fontWeight: '600' }}>₹{Math.round(order.total_amount)}</td>
                                            <td>
                                                <span style={{
                                                    fontSize: '12px',
                                                    padding: '2px 8px',
                                                    borderRadius: '4px',
                                                    background: 'var(--bg-secondary)',
                                                    color: 'var(--text-color)',
                                                    border: '1px solid var(--border-color)'
                                                }}>
                                                    {order.order_source}
                                                </span>
                                            </td>
                                            <td>
                                                <span style={{
                                                    padding: '4px 8px',
                                                    borderRadius: '12px',
                                                    fontSize: '11px',
                                                    background: order.status === 'Success' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                                                    color: order.status === 'Success' ? '#10B981' : '#EF4444',
                                                    fontWeight: '600'
                                                }}>
                                                    {order.status}
                                                </span>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {!loadingProfile && !selectedCustomer && !loading && (
                <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text-secondary)' }}>
                    <div style={{ fontSize: '40px', marginBottom: '10px' }}>🔍</div>
                    Search for a customer to view their profile
                </div>
            )}
        </div>
    );
}
