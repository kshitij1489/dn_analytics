import { useState, useCallback } from 'react';
import { endpoints } from '../../../api';

export function useSuggestions() {
    const [showSuggestions, setShowSuggestions] = useState(false);
    const [suggestions, setSuggestions] = useState<{ query: string; frequency: number }[]>([]);

    const loadSuggestions = useCallback(async () => {
        try {
            const res = await endpoints.ai.suggestions(10);
            setSuggestions(res.data || []);
        } catch (err) {
            console.error('Failed to load suggestions:', err);
        }
    }, []);

    return {
        suggestions,
        showSuggestions,
        setShowSuggestions,
        loadSuggestions
    };
}
