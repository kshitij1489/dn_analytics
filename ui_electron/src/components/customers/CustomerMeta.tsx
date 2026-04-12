import type { CustomerSimilarityCandidatePerson } from './customerIdentity';
import { formatCurrency } from './customerIdentity';
import './CustomerIdentity.css';

export function CustomerMeta({ customer }: { customer: CustomerSimilarityCandidatePerson }) {
    return (
        <div className="customer-identity-meta">
            <div className="customer-identity-meta-header">
                <div className="customer-identity-meta-name">{customer.name}</div>
                <span
                    className={[
                        'customer-identity-status-chip',
                        customer.is_verified
                            ? 'customer-identity-status-chip-verified'
                            : 'customer-identity-status-chip-unverified',
                    ].join(' ')}
                >
                    {customer.is_verified ? (
                        <>
                            <span className="customer-identity-status-check" aria-hidden="true">✓</span>
                            Verified
                        </>
                    ) : 'Unverified'}
                </span>
            </div>
            <div className="customer-identity-meta-id">Customer ID: {customer.customer_id}</div>
            <div className="customer-identity-meta-text">Phone: {customer.phone || 'No phone on file'}</div>
            <div
                className={[
                    'customer-identity-meta-text',
                    'customer-identity-meta-address',
                    customer.address ? '' : 'customer-identity-meta-text-muted',
                ].filter(Boolean).join(' ')}
            >
                Address: {customer.address || 'No saved address'}
            </div>
            <div className="customer-identity-meta-stats">
                <span>Orders: {customer.total_orders}</span>
                <span>Spent: {formatCurrency(customer.total_spent)}</span>
                <span>Last: {customer.last_order_date ? customer.last_order_date.split('T')[0] : 'Unknown'}</span>
            </div>
        </div>
    );
}
