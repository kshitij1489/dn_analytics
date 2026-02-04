import { useState, useCallback } from 'react';
import { endpoints, type LlmCacheEntry } from '../../../api';

const CACHE_ENTRIES_LIMIT = 500;

export function useCacheEntries() {
    const [entries, setEntries] = useState<LlmCacheEntry[]>([]);
    const [loading, setLoading] = useState(false);

    const loadCacheEntries = useCallback(async () => {
        setLoading(true);
        try {
            const res = await endpoints.ai.getCacheEntries(CACHE_ENTRIES_LIMIT);
            setEntries(res.data.entries ?? []);
        } catch (err) {
            console.error('Failed to load LLM cache entries:', err);
            setEntries([]);
        } finally {
            setLoading(false);
        }
    }, []);

    return { entries, loading, loadCacheEntries };
}
