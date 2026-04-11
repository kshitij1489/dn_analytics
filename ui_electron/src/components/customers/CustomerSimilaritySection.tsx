import { ActionButton, Card } from '..';
import './CustomerIdentity.css';
import {
    type CustomerMergePreview,
    type CustomerMergeRequestPayload,
    type CustomerSimilarityCandidate,
    type CustomerSimilarityCandidatePerson,
    formatCurrency,
} from './customerIdentity';

interface CustomerSimilaritySectionProps {
    activeMergeRequest: CustomerMergeRequestPayload | null;
    executingMerge: boolean;
    loadingPreview: boolean;
    loadingSimilar: boolean;
    manualSourceId: string;
    manualTargetId: string;
    mergePreview: CustomerMergePreview | null;
    selectedSuggestion: CustomerSimilarityCandidate | null;
    similarSuggestions: CustomerSimilarityCandidate[];
    onManualSourceChange: (value: string) => void;
    onManualTargetChange: (value: string) => void;
    onMerge: () => void;
    onPreviewManual: () => void;
    onRefresh: () => void;
    onSelectSuggestion: (candidate: CustomerSimilarityCandidate) => void;
}

function CustomerMeta({ customer }: { customer: CustomerSimilarityCandidatePerson }) {
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
                    {customer.is_verified ? 'Verified' : 'Unverified'}
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

export function CustomerSimilaritySection({
    activeMergeRequest,
    executingMerge,
    loadingPreview,
    loadingSimilar,
    manualSourceId,
    manualTargetId,
    mergePreview,
    selectedSuggestion,
    similarSuggestions,
    onManualSourceChange,
    onManualTargetChange,
    onMerge,
    onPreviewManual,
    onRefresh,
    onSelectSuggestion,
}: CustomerSimilaritySectionProps) {
    return (
        <div className="customer-identity-grid">
            <div className="customer-identity-card-compact">
                <Card
                    title="Similarity Queue"
                    headerAction={(
                        <ActionButton variant="secondary" size="small" onClick={onRefresh} disabled={loadingSimilar}>
                            Refresh
                        </ActionButton>
                    )}
                >
                    <div className="customer-identity-copy">
                        Basic ML suggestions rank likely duplicate customers by name, phone, address, and order profile similarity.
                    </div>

                    <div className="customer-identity-manual-card">
                        <div className="customer-identity-kicker">Manual Pair Review</div>
                        <div className="customer-identity-copy">
                            Enter a source customer ID and a surviving target customer ID to preview a merge outside the suggestion queue.
                        </div>
                        <div className="customer-identity-input-grid">
                            <input
                                className="customer-identity-input"
                                value={manualSourceId}
                                onChange={(event) => onManualSourceChange(event.target.value)}
                                placeholder="Source customer ID"
                            />
                            <input
                                className="customer-identity-input"
                                value={manualTargetId}
                                onChange={(event) => onManualTargetChange(event.target.value)}
                                placeholder="Target customer ID"
                            />
                        </div>
                        <div>
                            <ActionButton
                                variant="secondary"
                                size="small"
                                onClick={onPreviewManual}
                                disabled={loadingPreview || executingMerge}
                            >
                                {loadingPreview && !selectedSuggestion && !!activeMergeRequest ? 'Loading Preview...' : 'Preview Manual Pair'}
                            </ActionButton>
                        </div>
                    </div>

                    {loadingSimilar ? (
                        <div className="customer-identity-empty">Loading suggestions...</div>
                    ) : similarSuggestions.length === 0 ? (
                        <div className="customer-identity-empty">No strong duplicate suggestions are available right now.</div>
                    ) : (
                        <div className="customer-identity-list">
                            {similarSuggestions.map((candidate) => {
                                const isSelected =
                                    selectedSuggestion?.source_customer.customer_id === candidate.source_customer.customer_id &&
                                    selectedSuggestion?.target_customer.customer_id === candidate.target_customer.customer_id;

                                return (
                                    <button
                                        key={`${candidate.source_customer.customer_id}-${candidate.target_customer.customer_id}`}
                                        type="button"
                                        className={[
                                            'customer-identity-candidate',
                                            isSelected ? 'customer-identity-candidate-selected' : '',
                                        ].filter(Boolean).join(' ')}
                                        onClick={() => onSelectSuggestion(candidate)}
                                    >
                                        <div className="customer-identity-row">
                                        <div className="customer-identity-title">
                                            {candidate.source_customer.name}{' -> '}{candidate.target_customer.name}
                                        </div>
                                            <span className="customer-identity-score-chip">
                                                {(candidate.score * 100).toFixed(0)}%
                                            </span>
                                        </div>
                                        <div className="customer-identity-subtitle">
                                            Source #{candidate.source_customer.customer_id} into target #{candidate.target_customer.customer_id}
                                        </div>
                                        <div className="customer-identity-copy">
                                            {candidate.reasons.slice(0, 2).join(' | ')}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </Card>
            </div>

            <div className="customer-identity-card-compact">
                <Card
                    title="Comparison View"
                    headerAction={
                        activeMergeRequest && mergePreview ? (
                            <ActionButton
                                variant="primary"
                                size="small"
                                onClick={onMerge}
                                disabled={executingMerge || loadingPreview}
                            >
                                {executingMerge ? 'Merging...' : selectedSuggestion ? 'Merge Selected Pair' : 'Merge Manual Pair'}
                            </ActionButton>
                        ) : null
                    }
                >
                    {!activeMergeRequest ? (
                        <div className="customer-identity-empty">
                            Select a suggestion from the queue or preview a manual pair to review it side by side.
                        </div>
                    ) : loadingPreview || !mergePreview ? (
                        <div className="customer-identity-empty">Loading merge preview...</div>
                    ) : (
                        <div className="customer-identity-preview">
                            <div className="customer-identity-preview-header">
                                <div className="customer-identity-score-chip">
                                    Score {(mergePreview.score || 0) * 100 >= 1 ? `${((mergePreview.score || 0) * 100).toFixed(0)}%` : 'N/A'}
                                </div>
                                {!selectedSuggestion && <div className="customer-identity-manual-chip">Manual Review</div>}
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
                        </div>
                    )}
                </Card>
            </div>
        </div>
    );
}
