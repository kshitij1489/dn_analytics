import { useState, useCallback } from 'react';
import { endpoints, type DebugLogEntry, type CallTraceEntry, type CacheCounter } from '../../../api';

export function useDebug() {
    const [showDebug, setShowDebug] = useState(false);
    const [debugLogEntries, setDebugLogEntries] = useState<DebugLogEntry[]>([]);
    const [traceEntries, setTraceEntries] = useState<CallTraceEntry[]>([]);
    const [cacheCounters, setCacheCounters] = useState<CacheCounter[]>([]);

    const loadDebugLogs = useCallback(async (keepOnError = false, queryId?: string) => {
        try {
            const res = await endpoints.ai.getDebugLogs(queryId);
            setDebugLogEntries(res.data?.entries ?? []);
        } catch (err) {
            console.error('Failed to load debug logs:', err);
            if (!keepOnError) setDebugLogEntries([]);
        }
    }, []);

    // §4 telemetry: per-step trace (latency/tokens) for one query + global cache hit-rate.
    const loadTelemetry = useCallback(async (queryId?: string) => {
        try {
            const [trace, counters] = await Promise.all([
                queryId ? endpoints.ai.getTrace(queryId) : Promise.resolve(null),
                endpoints.ai.getCacheCounters()
            ]);
            setTraceEntries(trace?.data?.trace ?? []);
            setCacheCounters(counters.data?.counters ?? []);
        } catch (err) {
            console.error('Failed to load telemetry:', err);
        }
    }, []);

    const handleDebug = useCallback(
        async (lastQueryId?: string) => {
            const next = !showDebug;
            setShowDebug(next);
            if (next) {
                await Promise.all([loadDebugLogs(true, lastQueryId), loadTelemetry(lastQueryId)]);
            }
        },
        [showDebug, loadDebugLogs, loadTelemetry]
    );

    return {
        showDebug,
        setShowDebug,
        debugLogEntries,
        setDebugLogEntries,
        traceEntries,
        cacheCounters,
        loadDebugLogs,
        loadTelemetry,
        handleDebug
    };
}
