import { describe, expect, it } from 'vitest';
import {
    collapseGroupHistoryBursts,
    groupHistoryAction,
    groupHistoryDetail,
} from './groupHistory';

describe('group history presentation', () => {
    it('uses honest copy when legacy snapshots have no names', () => {
        const entry = {
            source_kind: 'legacy_restaurant_event' as const,
            event_type: 'menu_merge.applied',
            source_name: '',
            target_name: '',
            merged_at: '2026-08-15T01:09:19+05:30',
        };
        expect(groupHistoryAction(entry)).toBe('Legacy assignment update');
        expect(groupHistoryDetail(entry)).toBe('Item names unavailable');
    });

    it('translates global mutation codes and includes known targets', () => {
        expect(groupHistoryAction({
            source_kind: 'global_menu_event',
            event_type: 'global_locator.map',
            source_name: 'Matcha Berries 500 ML',
            target_name: 'Matcha Berries Ice Cream',
            merged_at: '2026-08-15T01:12:43+05:30',
        })).toBe('Mapped Matcha Berries 500 ML to Matcha Berries Ice Cream');
        expect(groupHistoryAction({
            source_kind: 'global_menu_event',
            event_type: 'global_locator.map',
            target_name: 'Matcha Berries Ice Cream',
            merged_at: '2026-08-15T01:12:43+05:30',
        })).toBe('Mapped item (raw name unavailable) to Matcha Berries Ice Cream');
        expect(groupHistoryAction({
            source_kind: 'global_menu_event',
            event_type: 'global_variant.create',
            merged_at: '2026-08-14T20:47:50+05:30',
        })).toBe('Created canonical variant');
        expect(groupHistoryAction({
            source_kind: 'global_menu_event',
            event_type: 'global_menu.genesis',
            actor: 'backfill',
            is_undoable: false,
            merged_at: '2026-08-09T08:00:00+00:00',
        })).toBe('Catalog backfill (genesis)');
    });

    it('collapses adjacent nameless legacy events from the same store', () => {
        const rows = [
            { history_id: '3', source_kind: 'legacy_restaurant_event' as const, event_type: 'menu_merge.applied', origin_restaurant_id: 'one', source_name: '', target_name: '', merged_at: '2026-08-15T01:09:19Z' },
            { history_id: '2', source_kind: 'legacy_restaurant_event' as const, event_type: 'menu_merge.applied', origin_restaurant_id: 'one', source_name: '', target_name: '', merged_at: '2026-08-15T01:09:18Z' },
            { history_id: '1', source_kind: 'legacy_restaurant_event' as const, event_type: 'menu_merge.applied', origin_restaurant_id: 'two', source_name: '', target_name: '', merged_at: '2026-08-15T01:09:17Z' },
        ];
        const collapsed = collapseGroupHistoryBursts(rows);
        expect(collapsed).toHaveLength(2);
        expect(collapsed[0].count).toBe(2);
        expect(collapsed[1].count).toBe(1);
    });
});
