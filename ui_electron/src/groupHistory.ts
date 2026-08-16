export type GroupHistoryFilter = 'global' | 'undoable' | 'legacy' | 'system' | 'all';

export interface GroupHistoryEntryLike {
    history_id?: string;
    source_kind?: 'legacy_restaurant_event' | 'global_menu_event';
    event_type?: string;
    source_name?: string | null;
    target_name?: string | null;
    origin_restaurant_id?: string | null;
    actor?: string | null;
    merged_at: string;
    is_undoable?: boolean;
    is_system_event?: boolean;
    detail?: Record<string, unknown> | null;
}

export interface CollapsedGroupHistoryEntry<T extends GroupHistoryEntryLike> {
    entry: T;
    count: number;
    earliestAt: string;
    latestAt: string;
}

const FILTER_LABELS: Record<GroupHistoryFilter, string> = {
    global: 'Global changes',
    undoable: 'Undoable',
    legacy: 'Legacy history',
    system: 'System activity',
    all: 'All activity',
};

export const GROUP_HISTORY_FILTERS = (
    Object.entries(FILTER_LABELS) as Array<[GroupHistoryFilter, string]>
).map(([id, label]) => ({ id, label }));

const ACTION_LABELS: Record<string, string> = {
    'global_item.create': 'Created canonical item',
    'global_item.merge': 'Merged canonical item',
    'global_item.rename': 'Renamed canonical item',
    'global_item.verify': 'Verified canonical item',
    'global_variant.create': 'Created canonical variant',
    'global_variant.merge': 'Merged canonical variant',
    'global_locator.map': 'Mapped item (raw name unavailable)',
    'global_menu.undo': 'Undid group menu change',
    'global_catalog.verification_backfill': 'Backfilled catalog verification',
    'menu_merge.applied': 'Applied legacy menu change',
    'menu_merge.undone': 'Undid legacy menu change',
};

export function groupHistoryAction(entry: GroupHistoryEntryLike): string {
    const eventType = entry.event_type || '';
    const sourceName = String(entry.source_name || '').trim();
    const targetName = String(entry.target_name || '').trim();
    const namesUnavailable = !sourceName && !targetName;

    if (entry.source_kind === 'legacy_restaurant_event' && namesUnavailable) {
        return 'Legacy assignment update';
    }
    if (eventType === 'global_locator.map' && sourceName && targetName) {
        return `Mapped ${sourceName} to ${targetName}`;
    }
    if (eventType === 'global_locator.map' && targetName) {
        return `Mapped item (raw name unavailable) to ${targetName}`;
    }
    if (eventType === 'global_item.merge' && sourceName && targetName) {
        return `Merged ${sourceName} into ${targetName}`;
    }
    if (eventType === 'menu_merge.applied' && sourceName && targetName) {
        return `Merged ${sourceName} into ${targetName}`;
    }
    if (eventType === 'global_item.rename' && targetName) {
        return `Renamed canonical item to ${targetName}`;
    }
    if (eventType === 'global_item.create' && targetName) {
        return `Created ${targetName}`;
    }
    return ACTION_LABELS[eventType] || humanizeEventType(eventType);
}

export function groupHistoryDetail(entry: GroupHistoryEntryLike): string | null {
    const sourceName = String(entry.source_name || '').trim();
    const targetName = String(entry.target_name || '').trim();
    if (
        entry.source_kind === 'legacy_restaurant_event'
        && !sourceName
        && !targetName
    ) {
        return 'Item names unavailable';
    }
    return null;
}

export function collapseGroupHistoryBursts<T extends GroupHistoryEntryLike>(
    entries: T[],
    maximumGapMs = 60_000,
): Array<CollapsedGroupHistoryEntry<T>> {
    const collapsed: Array<CollapsedGroupHistoryEntry<T>> = [];
    for (const entry of entries) {
        const timestamp = new Date(entry.merged_at).getTime();
        const namesUnavailable = !String(entry.source_name || '').trim()
            && !String(entry.target_name || '').trim();
        const canCollapse = entry.source_kind === 'legacy_restaurant_event'
            && namesUnavailable
            && !entry.is_undoable
            && Number.isFinite(timestamp);
        const previous = collapsed.at(-1);
        const previousTimestamp = previous
            ? new Date(previous.earliestAt).getTime()
            : Number.NaN;
        const sameBurst = canCollapse
            && previous
            && previous.entry.source_kind === entry.source_kind
            && previous.entry.event_type === entry.event_type
            && previous.entry.origin_restaurant_id === entry.origin_restaurant_id
            && !String(previous.entry.source_name || '').trim()
            && !String(previous.entry.target_name || '').trim()
            && Math.abs(previousTimestamp - timestamp) <= maximumGapMs;
        if (sameBurst && previous) {
            previous.count += 1;
            previous.earliestAt = entry.merged_at;
            continue;
        }
        collapsed.push({
            entry,
            count: 1,
            earliestAt: entry.merged_at,
            latestAt: entry.merged_at,
        });
    }
    return collapsed;
}

function humanizeEventType(eventType: string): string {
    if (!eventType) return 'Menu history event';
    const words = eventType.replace(/[._-]+/g, ' ').trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
}
