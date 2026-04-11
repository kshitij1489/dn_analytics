
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

interface CustomerProfileCustomer extends CustomerSearchResponse {
    address?: string | null;
}

interface CustomerAddress {
    address_id: number;
    customer_id: string;
    label?: string | null;
    address_line_1?: string | null;
    address_line_2?: string | null;
    city?: string | null;
    state?: string | null;
    postal_code?: string | null;
    country?: string | null;
    is_default: boolean;
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
    customer: CustomerProfileCustomer;
    orders: CustomerProfileOrder[];
    addresses: CustomerAddress[];
}

function formatCustomerAddress(address: CustomerAddress): string[] {
    const line1 = [address.address_line_1, address.address_line_2]
        .filter((part): part is string => Boolean(part?.trim()))
        .map((part) => part.trim())
        .join(', ');

    const line2 = [address.city, address.state, address.postal_code]
        .filter((part): part is string => Boolean(part?.trim()))
        .map((part) => part.trim())
        .join(', ');

    const line3 = address.country?.trim() || '';

    return [line1, line2, line3].filter(Boolean);
}

export function CustomerProfile({ headerActions, initialCustomerId }: { headerActions?: React.ReactNode, initialCustomerId?: string | number }) {
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<CustomerSearchResponse[]>([]);
    const [selectedCustomer, setSelectedCustomer] = useState<CustomerProfileData | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadingProfile, setLoadingProfile] = useState(false);
    const primaryAddress = selectedCustomer?.addresses.find((address) => address.is_default) ?? selectedCustomer?.addresses[0];
    const primaryAddressLines = primaryAddress ? formatCustomerAddress(primaryAddress) : [];
    const savedAddress = primaryAddressLines.join(', ') || selectedCustomer?.customer.address?.trim();

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

    const loadProfileById = useCallback(async (id: string | number) => {
        setLoadingProfile(true);
        try {
            const res = await endpoints.customers.profile(String(id));
            setSelectedCustomer(res.data);
            setSearchQuery(String(id)); // Pre-fill search with customer ID
        } catch (err) {
            console.error("Profile fetch failed", err);
        } finally {
            setLoadingProfile(false);
        }
    }, []);

    // Initial Load by ID (from navigation)
    useEffect(() => {
        if (initialCustomerId) {
            loadProfileById(initialCustomerId);
        }
    }, [initialCustomerId, loadProfileById]);

    const handleSelectCustomer = async (customer: CustomerSearchResponse) => {
        setLoadingProfile(true);
        setSearchResults([]); // Clear results to hide dropdown
        setSearchQuery(customer.name); // Set input to selected Name
        try {
            const res = await endpoints.customers.profile(customer.customer_id);
            setSelectedCustomer(res.data);
        } catch (err) {
            console.error("Profile fetch failed", err);
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
                    <div className="card" style={{ padding: '20px', marginBottom: '20px' }}>
                        <div
                            style={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'flex-start',
                                gap: '20px',
                                flexWrap: 'wrap'
                            }}
                        >
                            <div style={{ flex: '1 1 320px', minWidth: '280px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
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
                                <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginTop: '6px' }}>
                                    Customer ID: {selectedCustomer.customer.customer_id}
                                </div>
                                <div
                                    style={{
                                        display: 'grid',
                                        gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                                        gap: '12px',
                                        marginTop: '16px'
                                    }}
                                >
                                    <div
                                        style={{
                                            border: '1px solid var(--border-color)',
                                            borderRadius: '12px',
                                            padding: '12px 14px',
                                            background: 'var(--bg-secondary)'
                                        }}
                                    >
                                        <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                                            Phone
                                        </div>
                                        <div style={{ fontSize: '14px', color: 'var(--text-color)' }}>
                                            {selectedCustomer.customer.phone || 'No phone on file'}
                                        </div>
                                    </div>
                                    <div
                                        style={{
                                            border: '1px solid var(--border-color)',
                                            borderRadius: '12px',
                                            padding: '12px 14px',
                                            background: 'var(--bg-secondary)'
                                        }}
                                    >
                                        <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                                            Address Book
                                        </div>
                                        <div style={{ fontSize: '14px', color: 'var(--text-color)' }}>
                                            {selectedCustomer.addresses.length} saved {selectedCustomer.addresses.length === 1 ? 'address' : 'addresses'}
                                        </div>
                                    </div>
                                    <div
                                        style={{
                                            border: '1px solid var(--border-color)',
                                            borderRadius: '12px',
                                            padding: '12px 14px',
                                            background: 'var(--bg-secondary)'
                                        }}
                                    >
                                        <div style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-secondary)', marginBottom: '6px' }}>
                                            Primary Address
                                        </div>
                                        <div style={{ fontSize: '14px', color: savedAddress ? 'var(--text-color)' : 'var(--text-secondary)', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                                            {savedAddress || 'No saved address on file'}
                                        </div>
                                    </div>
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
                    </div>

                    <div className="card" style={{ padding: '20px', marginBottom: '20px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap' }}>
                            <div>
                                <h3 style={{ margin: 0, fontSize: '18px' }}>Address Book</h3>
                                <div style={{ marginTop: '4px', color: 'var(--text-secondary)', fontSize: '13px' }}>
                                    Structured addresses are now stored separately from the legacy customer record.
                                </div>
                            </div>
                            <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
                                Default address appears first.
                            </div>
                        </div>

                        {selectedCustomer.addresses.length === 0 ? (
                            <div style={{ padding: '16px', borderRadius: '12px', background: 'var(--bg-secondary)', color: 'var(--text-secondary)' }}>
                                No structured addresses are saved for this customer yet.
                            </div>
                        ) : (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '14px' }}>
                                {selectedCustomer.addresses.map((address) => {
                                    const addressLines = formatCustomerAddress(address);

                                    return (
                                        <div
                                            key={address.address_id}
                                            style={{
                                                border: '1px solid var(--border-color)',
                                                borderRadius: '14px',
                                                padding: '14px 16px',
                                                background: 'var(--bg-secondary)',
                                                minHeight: '140px'
                                            }}
                                        >
                                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '10px', alignItems: 'flex-start', marginBottom: '10px' }}>
                                                <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-color)' }}>
                                                    {address.label?.trim() || 'Address'}
                                                </div>
                                                {address.is_default && (
                                                    <span
                                                        style={{
                                                            fontSize: '11px',
                                                            padding: '3px 8px',
                                                            borderRadius: '999px',
                                                            background: 'rgba(0, 122, 255, 0.12)',
                                                            color: 'var(--accent-color)',
                                                            border: '1px solid rgba(0, 122, 255, 0.2)',
                                                            fontWeight: 600
                                                        }}
                                                    >
                                                        Default
                                                    </span>
                                                )}
                                            </div>
                                            {addressLines.length > 0 ? (
                                                <div style={{ display: 'grid', gap: '6px' }}>
                                                    {addressLines.map((line, index) => (
                                                        <div key={`${address.address_id}-${index}`} style={{ color: 'var(--text-color)', lineHeight: 1.5 }}>
                                                            {line}
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div style={{ color: 'var(--text-secondary)' }}>
                                                    No address details saved.
                                                </div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
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
