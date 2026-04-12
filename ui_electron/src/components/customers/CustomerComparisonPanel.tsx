import { ActionButton, Card } from '..';
import { CustomerMeta } from './CustomerMeta';
import { CustomerOrderSnapshot } from './CustomerOrderSnapshot';
import type { CustomerMergePreview, CustomerMergeRequestPayload } from './customerIdentity';
import './CustomerIdentity.css';

interface CustomerComparisonPanelProps {
    activeMergeRequest: CustomerMergeRequestPayload | null;
    executingMerge: boolean;
    loadingPreview: boolean;
    mergePreview: CustomerMergePreview | null;
    onMerge: () => void;
}

export function CustomerComparisonPanel({
    activeMergeRequest,
    executingMerge,
    loadingPreview,
    mergePreview,
    onMerge,
}: CustomerComparisonPanelProps) {
    return (
        <div className="customer-identity-card-compact">
            <Card
                title="Comparison View"
                headerAction={
                    activeMergeRequest && mergePreview ? (
                        <ActionButton
                            variant="primary"
                            size="small"
                            className="customer-identity-merge-button"
                            onClick={onMerge}
                            disabled={executingMerge || loadingPreview}
                        >
                            {executingMerge ? 'Merging...' : 'Merge Selected Pair'}
                        </ActionButton>
                    ) : null
                }
            >
                {!activeMergeRequest ? (
                    <div className="customer-identity-empty">
                        Select a merge pair from the queue to review it side by side.
                    </div>
                ) : loadingPreview || !mergePreview ? (
                    <div className="customer-identity-empty">Loading merge preview...</div>
                ) : (
                    <div className="customer-identity-preview">
                        <div className="customer-identity-preview-header">
                            <div className="customer-identity-score-chip">
                                Score {(mergePreview.score || 0) * 100 >= 1 ? `${((mergePreview.score || 0) * 100).toFixed(0)}%` : 'N/A'}
                            </div>
                            <div className="customer-identity-copy">
                                Model: {mergePreview.model_name || 'basic_duplicate_knn_v1'}
                            </div>
                        </div>

                        <div className="customer-identity-preview-grid">
                            <div className="customer-identity-panel">
                                <div className="customer-identity-panel-label">Source Customer</div>
                                <CustomerMeta customer={mergePreview.source_customer} />
                            </div>
                            <div className="customer-identity-panel">
                                <div className="customer-identity-panel-label">Target Customer</div>
                                <CustomerMeta customer={mergePreview.target_customer} />
                            </div>
                        </div>

                        <div className="customer-identity-metrics-grid">
                            <div className="customer-identity-metric-card">
                                <div className="customer-identity-metric-label">Orders To Move</div>
                                <div className="customer-identity-metric-value">{mergePreview.orders_to_move}</div>
                            </div>
                            <div className="customer-identity-metric-card">
                                <div className="customer-identity-metric-label">Source Addresses</div>
                                <div className="customer-identity-metric-value">{mergePreview.source_address_count}</div>
                            </div>
                            <div className="customer-identity-metric-card">
                                <div className="customer-identity-metric-label">Target Addresses</div>
                                <div className="customer-identity-metric-value">{mergePreview.target_address_count}</div>
                            </div>
                        </div>

                        <div>
                            <div className="customer-identity-kicker">Why This Pair Was Suggested</div>
                            {mergePreview.reasons.length > 0 ? (
                                <div className="customer-identity-reason-list">
                                    {mergePreview.reasons.map((reason) => (
                                        <div key={reason} className="customer-identity-reason-card">
                                            {reason}
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="customer-identity-empty">No model-based reasons were attached to this pair.</div>
                            )}
                        </div>

                        <div>
                            <div className="customer-identity-kicker">Order Comparison</div>
                            <div className="customer-identity-preview-grid">
                                <CustomerOrderSnapshot
                                    label="Source Orders"
                                    snapshot={mergePreview.source_order_snapshot}
                                />
                                <CustomerOrderSnapshot
                                    label="Target Orders"
                                    snapshot={mergePreview.target_order_snapshot}
                                />
                            </div>
                        </div>
                    </div>
                )}
            </Card>
        </div>
    );
}
