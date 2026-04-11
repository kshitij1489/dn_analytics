import { useState, useEffect, useCallback } from 'react';
import _ from 'lodash';
import { endpoints } from '../api';
import './CustomerProfile.css';

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

export function CustomerProfile({
    headerActions,
    initialCustomerId,
}: {
    headerActions?: React.ReactNode;
    initialCustomerId?: string | number;
}) {
    const [searchQuery, setSearchQuery] = useState('');
    const [searchResults, setSearchResults] = useState<CustomerSearchResponse[]>([]);
    const [selectedCustomer, setSelectedCustomer] = useState<CustomerProfileData | null>(null);
    const [loading, setLoading] = useState(false);
    const [loadingProfile, setLoadingProfile] = useState(false);

    const primaryAddress = selectedCustomer?.addresses.find((address) => address.is_default) ?? selectedCustomer?.addresses[0];
    const primaryAddressLines = primaryAddress ? formatCustomerAddress(primaryAddress) : [];
    const savedAddress = primaryAddressLines.join(', ') || selectedCustomer?.customer.address?.trim();

    const debouncedSearch = useCallback(
        _.debounce(async (query: string) => {
            if (!query || query.length < 2) {
                setSearchResults([]);
                return;
            }

            setLoading(true);
            try {
                const res = await endpoints.customers.search(query);
                setSearchResults(res.data);
            } catch (error) {
                console.error('Search failed', error);
            } finally {
                setLoading(false);
            }
        }, 500),
        [],
    );

    useEffect(() => {
        debouncedSearch(searchQuery);
        return () => debouncedSearch.cancel();
    }, [debouncedSearch, searchQuery]);

    const loadProfileById = useCallback(async (customerId: string | number) => {
        setLoadingProfile(true);
        try {
            const res = await endpoints.customers.profile(String(customerId));
            setSelectedCustomer(res.data);
            setSearchQuery(String(customerId));
        } catch (error) {
            console.error('Profile fetch failed', error);
        } finally {
            setLoadingProfile(false);
        }
    }, []);

    useEffect(() => {
        if (initialCustomerId) {
            loadProfileById(initialCustomerId);
        }
    }, [initialCustomerId, loadProfileById]);

    const handleSelectCustomer = async (customer: CustomerSearchResponse) => {
        setLoadingProfile(true);
        setSearchResults([]);
        setSearchQuery(customer.name);
        try {
            const res = await endpoints.customers.profile(customer.customer_id);
            setSelectedCustomer(res.data);
        } catch (error) {
            console.error('Profile fetch failed', error);
        } finally {
            setLoadingProfile(false);
        }
    };

    return (
        <div className="customer-profile">
            <div className="customer-profile-header">
                <div className="customer-profile-header-actions">{headerActions}</div>
                <div className="customer-profile-search">
                    <input
                        type="text"
                        className="customer-profile-search-input"
                        placeholder="Search by Name or Phone or Customer ID..."
                        value={searchQuery}
                        onChange={(event) => setSearchQuery(event.target.value)}
                    />

                    {loading && <div className="customer-profile-searching">Searching...</div>}

                    {searchResults.length > 0 && (
                        <div className="customer-profile-search-results">
                            {searchResults.map((customer) => (
                                <button
                                    key={customer.customer_id}
                                    type="button"
                                    className="customer-profile-search-result"
                                    onClick={() => handleSelectCustomer(customer)}
                                >
                                    <div>
                                        <div className="customer-profile-search-result-name">{customer.name}</div>
                                        <div className="customer-profile-search-result-phone">{customer.phone}</div>
                                    </div>
                                    <div className="customer-profile-search-result-totals">
                                        <div>₹{Math.round(customer.total_spent)}</div>
                                        <div className="customer-profile-search-result-meta">
                                            Last: {customer.last_order_date?.split('T')[0]}
                                        </div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {loadingProfile && <div className="customer-profile-loading">Loading Profile...</div>}

            {!loadingProfile && selectedCustomer && (
                <div className="customer-profile-content">
                    <div className="card customer-profile-card customer-profile-card-padded">
                        <div className="customer-profile-summary">
                            <div className="customer-profile-summary-main">
                                <div className="customer-profile-title-row">
                                    <h2 className="customer-profile-title">{selectedCustomer.customer.name}</h2>
                                    <span
                                        className={[
                                            'customer-profile-badge',
                                            selectedCustomer.customer.is_verified
                                                ? 'customer-profile-badge-verified'
                                                : 'customer-profile-badge-unverified',
                                        ].join(' ')}
                                    >
                                        {selectedCustomer.customer.is_verified ? 'Verified' : 'Not Verified'}
                                    </span>
                                </div>
                                <div className="customer-profile-id">
                                    Customer ID: {selectedCustomer.customer.customer_id}
                                </div>

                                <div className="customer-profile-summary-grid">
                                    <div className="customer-profile-summary-card">
                                        <div className="customer-profile-summary-label">Phone</div>
                                        <div className="customer-profile-summary-value">
                                            {selectedCustomer.customer.phone || 'No phone on file'}
                                        </div>
                                    </div>
                                    <div className="customer-profile-summary-card">
                                        <div className="customer-profile-summary-label">Address Book</div>
                                        <div className="customer-profile-summary-value">
                                            {selectedCustomer.addresses.length} saved {selectedCustomer.addresses.length === 1 ? 'address' : 'addresses'}
                                        </div>
                                    </div>
                                    <div className="customer-profile-summary-card">
                                        <div className="customer-profile-summary-label">Primary Address</div>
                                        <div
                                            className={[
                                                'customer-profile-summary-value',
                                                savedAddress ? '' : 'customer-profile-summary-value-muted',
                                            ].filter(Boolean).join(' ')}
                                        >
                                            {savedAddress || 'No saved address on file'}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div className="customer-profile-metrics">
                                <div className="customer-profile-metric">
                                    <div className="customer-profile-metric-label">Total Orders</div>
                                    <div className="customer-profile-metric-value">{selectedCustomer.orders.length}</div>
                                </div>
                                <div className="customer-profile-metric">
                                    <div className="customer-profile-metric-label">Total Spent</div>
                                    <div className="customer-profile-metric-value customer-profile-metric-accent">
                                        ₹{Math.round(selectedCustomer.customer.total_spent)}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <div className="card customer-profile-card customer-profile-card-padded">
                        <div className="customer-profile-section-header">
                            <div>
                                <h3 className="customer-profile-section-title">Address Book</h3>
                                <div className="customer-profile-section-description">
                                    Structured addresses are now stored separately from the legacy customer record.
                                </div>
                            </div>
                            <div className="customer-profile-section-note">Default address appears first.</div>
                        </div>

                        {selectedCustomer.addresses.length === 0 ? (
                            <div className="customer-profile-address-empty">
                                No structured addresses are saved for this customer yet.
                            </div>
                        ) : (
                            <div className="customer-profile-address-grid">
                                {selectedCustomer.addresses.map((address) => {
                                    const addressLines = formatCustomerAddress(address);
                                    return (
                                        <div key={address.address_id} className="customer-profile-address-card">
                                            <div className="customer-profile-address-header">
                                                <div className="customer-profile-address-title">
                                                    {address.label?.trim() || 'Address'}
                                                </div>
                                                {address.is_default && (
                                                    <span className="customer-profile-address-default">Default</span>
                                                )}
                                            </div>
                                            {addressLines.length > 0 ? (
                                                <div className="customer-profile-address-lines">
                                                    {addressLines.map((line, index) => (
                                                        <div key={`${address.address_id}-${index}`} className="customer-profile-address-line">
                                                            {line}
                                                        </div>
                                                    ))}
                                                </div>
                                            ) : (
                                                <div className="customer-profile-address-muted">No address details saved.</div>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>

                    <div className="card customer-profile-card customer-profile-card-flush">
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
                                        <td colSpan={6} className="customer-profile-no-orders">
                                            No orders yet for this customer.
                                        </td>
                                    </tr>
                                ) : (
                                    selectedCustomer.orders.map((order) => (
                                        <tr key={order.order_id}>
                                            <td className="customer-profile-order-date">
                                                {new Date(order.created_on).toLocaleDateString()}
                                                <div className="customer-profile-order-time">
                                                    {new Date(order.created_on).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                </div>
                                            </td>
                                            <td>{order.order_number}</td>
                                            <td>
                                                <div className="customer-profile-order-items">{order.items_summary}</div>
                                            </td>
                                            <td className="customer-profile-order-total">₹{Math.round(order.total_amount)}</td>
                                            <td>
                                                <span className="customer-profile-order-source">{order.order_source}</span>
                                            </td>
                                            <td>
                                                <span
                                                    className={[
                                                        'customer-profile-order-status',
                                                        order.status === 'Success'
                                                            ? 'customer-profile-order-status-success'
                                                            : 'customer-profile-order-status-failed',
                                                    ].join(' ')}
                                                >
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
                <div className="customer-profile-empty">
                    <div className="customer-profile-empty-icon">?</div>
                    Search for a customer to view their profile
                </div>
            )}
        </div>
    );
}
