import { ActionButton, Card } from '..';
import { CustomerComparisonPanel } from './CustomerComparisonPanel';
import './CustomerIdentity.css';
import {
    type CustomerMergePreview,
    type CustomerMergeRequestPayload,
    type CustomerSimilarityCandidate,
} from './customerIdentity';

interface CustomerSimilaritySectionProps {
    activeMergeRequest: CustomerMergeRequestPayload | null;
    executingMerge: boolean;
    loadingPreview: boolean;
    loadingSimilar: boolean;
    mergePreview: CustomerMergePreview | null;
    queueMode: 'suggestions' | 'search' | 'compare';
    searchQuery: string;
    compareSourceCustomerId: string;
    compareTargetCustomerId: string;
    selectedSuggestion: CustomerSimilarityCandidate | null;
    similarSuggestions: CustomerSimilarityCandidate[];
    onMerge: (markTargetVerified?: boolean) => void;
    onModeChange: (mode: 'suggestions' | 'search' | 'compare') => void;
    onRefresh: () => void;
    onSearchQueryChange: (value: string) => void;
    onCompareSourceCustomerIdChange: (value: string) => void;
    onCompareTargetCustomerIdChange: (value: string) => void;
    onSelectSuggestion: (candidate: CustomerSimilarityCandidate) => void;
}

export function CustomerSimilaritySection({
    activeMergeRequest,
    executingMerge,
    loadingPreview,
    loadingSimilar,
    mergePreview,
    queueMode,
    searchQuery,
    compareSourceCustomerId,
    compareTargetCustomerId,
    selectedSuggestion,
    similarSuggestions,
    onMerge,
    onModeChange,
    onRefresh,
    onSearchQueryChange,
    onCompareSourceCustomerIdChange,
    onCompareTargetCustomerIdChange,
    onSelectSuggestion,
}: CustomerSimilaritySectionProps) {
    return (
        <div className="customer-identity-grid">
            <div className="customer-identity-card-compact">
                <Card>
                    <div className="customer-identity-toolbar">
                        <div className="customer-identity-toolbar-row">
                            <div className="customer-identity-toggle" role="tablist" aria-label="Similarity queue mode">
                                <button
                                    type="button"
                                    className={[
                                        'customer-identity-toggle-button',
                                        queueMode === 'suggestions' ? 'customer-identity-toggle-button-active' : '',
                                    ].filter(Boolean).join(' ')}
                                    onClick={() => onModeChange('suggestions')}
                                >
                                    Top Merge Suggestions
                                </button>
                                <button
                                    type="button"
                                    className={[
                                        'customer-identity-toggle-button',
                                        queueMode === 'compare' ? 'customer-identity-toggle-button-active' : '',
                                    ].filter(Boolean).join(' ')}
                                    onClick={() => onModeChange('compare')}
                                >
                                    Compare
                                </button>
                                <button
                                    type="button"
                                    className={[
                                        'customer-identity-toggle-button',
                                        queueMode === 'search' ? 'customer-identity-toggle-button-active' : '',
                                    ].filter(Boolean).join(' ')}
                                    onClick={() => onModeChange('search')}
                                >
                                    Search
                                </button>
                            </div>

                            <ActionButton
                                variant="secondary"
                                size="small"
                                className="customer-identity-refresh-button"
                                onClick={onRefresh}
                                disabled={loadingSimilar}
                            >
                                Refresh
                            </ActionButton>
                        </div>
                    </div>

                    <h3 className="customer-identity-section-title">Similarity Queue</h3>

                    <div className="customer-identity-copy">
                        Basic ML suggestions rank likely duplicate customers by name, phone, address, and order profile similarity.
                    </div>

                    {queueMode === 'search' && (
                        <div className="customer-identity-search-card">
                            <input
                                className="customer-identity-input"
                                value={searchQuery}
                                onChange={(event) => onSearchQueryChange(event.target.value)}
                                placeholder="Search customer name"
                            />
                            <div className="customer-identity-copy">
                                Shows likely merge pairs where the source or target customer name matches your search, sorted by score.
                            </div>
                        </div>
                    )}

                    {queueMode === 'compare' && (
                        <div className="customer-identity-search-card">
                            <div className="customer-identity-input-grid">
                                <input
                                    className="customer-identity-input"
                                    value={compareSourceCustomerId}
                                    onChange={(event) => onCompareSourceCustomerIdChange(event.target.value)}
                                    placeholder="Source customer ID"
                                    autoComplete="off"
                                    aria-label="Source customer ID"
                                />
                                <input
                                    className="customer-identity-input"
                                    value={compareTargetCustomerId}
                                    onChange={(event) => onCompareTargetCustomerIdChange(event.target.value)}
                                    placeholder="Target customer ID"
                                    autoComplete="off"
                                    aria-label="Target customer ID"
                                />
                            </div>
                            <div className="customer-identity-copy">
                                Orders and profile for the source customer move into the target. The same merge preview as the queue loads in Comparison View.
                            </div>
                        </div>
                    )}

                    {queueMode === 'compare' ? (
                        <div className="customer-identity-empty">
                            {!compareSourceCustomerId.trim() || !compareTargetCustomerId.trim()
                                ? 'Enter both customer IDs to load the side-by-side comparison.'
                                : loadingPreview
                                    ? 'Loading preview...'
                                    : 'When the pair is valid, the merge preview opens in Comparison View on the right.'}
                        </div>
                    ) : loadingSimilar ? (
                        <div className="customer-identity-empty">
                            {queueMode === 'search' ? 'Searching similar pairs...' : 'Loading suggestions...'}
                        </div>
                    ) : similarSuggestions.length === 0 ? (
                        <div className="customer-identity-empty">
                            {queueMode === 'search'
                                ? (searchQuery.trim()
                                    ? `No merge pairs match "${searchQuery.trim()}".`
                                    : 'Enter a customer name to search for merge pairs.')
                                : 'No strong duplicate suggestions are available right now.'}
                        </div>
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

            <CustomerComparisonPanel
                activeMergeRequest={activeMergeRequest}
                executingMerge={executingMerge}
                loadingPreview={loadingPreview}
                mergePreview={mergePreview}
                onMerge={onMerge}
            />
        </div>
    );
}
