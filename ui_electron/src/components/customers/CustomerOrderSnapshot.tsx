import type { CustomerMergePreviewCustomerOrders } from './customerIdentity';
import { formatCurrency, formatDateTime } from './customerIdentity';
import './CustomerIdentity.css';

export function CustomerOrderSnapshot({
    label,
    snapshot,
}: {
    label: string;
    snapshot: CustomerMergePreviewCustomerOrders;
}) {
    return (
        <div className="customer-identity-panel">
            <div className="customer-identity-panel-label">{label}</div>

            <div className="customer-identity-order-section">
                <div>
                    <div className="customer-identity-kicker">Top Items</div>
                    {snapshot.top_items.length > 0 ? (
                        <div className="customer-identity-top-item-list">
                            {snapshot.top_items.map((item) => (
                                <div
                                    key={item.item_name}
                                    className="customer-identity-top-item-card"
                                >
                                    <div className="customer-identity-row customer-identity-row-start">
                                        <div className="customer-identity-title-sm">{item.item_name}</div>
                                        <div className="customer-identity-subtitle">{item.total_quantity} qty</div>
                                    </div>
                                    <div className="customer-identity-subtitle">
                                        {item.order_count} {item.order_count === 1 ? 'order' : 'orders'}
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="customer-identity-empty">No item history available.</div>
                    )}
                </div>

                <div>
                    <div className="customer-identity-kicker">Recent Orders</div>
                    {snapshot.recent_orders.length > 0 ? (
                        <div className="customer-identity-order-list">
                            {snapshot.recent_orders.map((order) => (
                                <div key={order.order_id} className="customer-identity-order-card">
                                    <div className="customer-identity-row customer-identity-row-start">
                                        <div>
                                            <div className="customer-identity-title-sm">Order #{order.order_number}</div>
                                            <div className="customer-identity-subtitle">{formatDateTime(order.created_on)}</div>
                                        </div>
                                        <div className="customer-identity-order-total">{formatCurrency(order.total_amount)}</div>
                                    </div>
                                    {order.items.length > 0 ? (
                                        <div className="customer-identity-item-chip-list">
                                            {order.items.map((item) => (
                                                <span
                                                    key={`${order.order_id}-${item.item_name}`}
                                                    className="customer-identity-item-chip"
                                                >
                                                    {item.item_name}
                                                    {item.quantity > 1 ? ` x${item.quantity}` : ''}
                                                </span>
                                            ))}
                                        </div>
                                    ) : (
                                        <div className="customer-identity-subtitle">No items</div>
                                    )}
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="customer-identity-empty">No orders found.</div>
                    )}
                </div>
            </div>
        </div>
    );
}
