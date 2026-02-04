import { useState, useCallback } from 'react';
import { endpoints, type DebugLogEntry } from '../../../api';

export function useDebug() {
    const [showDebug, setShowDebug] = useState(false);
    const [debugLogEntries, setDebugLogEntries] = useState<DebugLogEntry[]>([]);

    const loadDebugLogs = useCallback(async (keepOnError = false) => {
        try {
            const res = await endpoints.ai.getDebugLogs();
            setDebugLogEntries(res.data?.entries ?? []);
        } catch (err) {
            console.error('Failed to load debug logs:', err);
            if (!keepOnError) setDebugLogEntries([]);
        }
    }, []);

    const handleDebug = useCallback(async () => {
        const next = !showDebug;
        setShowDebug(next);
        if (next) {
            try {
                await endpoints.ai.initDebug();
                await loadDebugLogs(true);
            } catch (error) {
                console.error('Failed to init debug:', error);
            }
        }
    }, [showDebug, loadDebugLogs]);

    return {
        showDebug,
        setShowDebug,
        debugLogEntries,
        setDebugLogEntries,
        loadDebugLogs,
        handleDebug
    };
}
