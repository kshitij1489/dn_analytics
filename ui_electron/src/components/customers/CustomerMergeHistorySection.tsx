import { ActionButton, Card } from '..';
import './CustomerIdentity.css';
import { type CustomerMergeHistoryEntry, formatDateTime } from './customerIdentity';

interface CustomerMergeHistorySectionProps {
    loadingHistory: boolean;
    mergeHistory: CustomerMergeHistoryEntry[];
    undoingMergeId: number | null;
    onRefresh: () => void;
    onUndoMerge: (mergeId: number) => void;
}

export function CustomerMergeHistorySection({
    loadingHistory,
    mergeHistory,
    undoingMergeId,
    onRefresh,
    onUndoMerge,
}: CustomerMergeHistorySectionProps) {
    return (
        <div className="customer-identity-card-compact">
            <Card
                title="Merge Audit Trail"
                headerAction={(
                    <ActionButton variant="secondary" size="small" onClick={onRefresh} disabled={loadingHistory}>
                        Refresh
                    </ActionButton>
                )}
            >
                {loadingHistory ? (
                    <div className="customer-identity-empty">Loading merge history...</div>
                ) : mergeHistory.length === 0 ? (
                    <div className="customer-identity-empty">No customer merges have been recorded yet.</div>
                ) : (
                    <div className="customer-merge-history-list">
                        {mergeHistory.map((entry) => {
                            const activeMerge = !entry.undone_at;
                            return (
                                <div key={entry.merge_id} className="customer-merge-history-card">
                                    <div className="customer-identity-row customer-identity-row-start">
                                        <div className="customer-identity-title">
                                            #{entry.merge_id}: {entry.source_name || entry.source_customer_id}{' -> '}{entry.target_name || entry.target_customer_id}
                                        </div>
                                        <div className="customer-identity-row">
                                            <span
                                                className={[
                                                    'customer-identity-status-chip',
                                                    activeMerge
                                                        ? 'customer-identity-status-chip-verified'
                                                        : 'customer-identity-status-chip-unverified',
                                                ].join(' ')}
                                            >
                                                {activeMerge ? 'Active Merge' : 'Undone'}
                                            </span>
                                            {activeMerge && (
                                                <ActionButton
                                                    variant="danger"
                                                    size="small"
                                                    onClick={() => onUndoMerge(entry.merge_id)}
                                                    disabled={undoingMergeId === entry.merge_id}
                                                >
                                                    {undoingMergeId === entry.merge_id ? 'Undoing...' : 'Undo'}
                                                </ActionButton>
                                            )}
                                        </div>
                                    </div>
                                    <div className="customer-merge-history-meta">
                                        <span>Merged: {formatDateTime(entry.merged_at)}</span>
                                        <span>Orders moved: {entry.orders_moved}</span>
                                        <span>Addresses copied: {entry.copied_address_count}</span>
                                        <span>Model: {entry.model_name || 'manual/basic'}</span>
                                        {entry.similarity_score != null && <span>Score: {(entry.similarity_score * 100).toFixed(0)}%</span>}
                                        {entry.undone_at && <span>Undone: {formatDateTime(entry.undone_at)}</span>}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </Card>
        </div>
    );
}
