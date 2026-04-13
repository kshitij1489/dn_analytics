import { TabButton } from './TabButton';

export type CustomerAnalyticsTableViewMode = 'summary' | 'customerList';

interface CustomerAnalyticsViewToolbarProps {
    view: CustomerAnalyticsTableViewMode;
    onViewChange: (view: CustomerAnalyticsTableViewMode) => void;
}

export function CustomerAnalyticsViewToolbar({ view, onViewChange }: CustomerAnalyticsViewToolbarProps) {
    return (
        <div className="segmented-control customers-analytics-table-toggle" role="tablist" aria-label="Table view">
            <TabButton
                active={view === 'summary'}
                onClick={() => onViewChange('summary')}
                variant="segmented"
            >
                Summary
            </TabButton>
            <TabButton
                active={view === 'customerList'}
                onClick={() => onViewChange('customerList')}
                variant="segmented"
            >
                Customer List
            </TabButton>
        </div>
    );
}
