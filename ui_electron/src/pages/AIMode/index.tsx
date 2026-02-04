import { useState, useRef, useEffect } from 'react';
import { LoadingSpinner } from '../../components';
import {
    useConversations,
    useSuggestions,
    useDebug,
    useChat,
    useCacheEntries
} from './hooks';
import {
    AIModeHeader,
    ChatMessage,
    EmptyState,
    ChatInput,
    DebugOverlay,
    TelemetryView
} from './components';
import type { Message } from './types';
import { endpoints } from '../../api';

export default function AIMode() {
    const [viewMode, setViewMode] = useState<'assistant' | 'telemetry'>('assistant');
    const [messages, setMessages] = useState<Message[]>([]);
    const [prompt, setPrompt] = useState('');
    const [loading, setLoading] = useState(false);
    const [lastFailedPrompt, setLastFailedPrompt] = useState<string | null>(null);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const historyPanelRef = useRef<HTMLDivElement>(null);
    const historyTriggerRef = useRef<HTMLButtonElement>(null);
    const suggestionsContainerRef = useRef<HTMLDivElement>(null);

    const [showHistory, setShowHistory] = useState(false);

    const conversations = useConversations({
        setMessages,
        onLoadSuccess: () => {
            setLastFailedPrompt(null);
            setShowHistory(false);
        }
    });

    const { suggestions, showSuggestions, setShowSuggestions, loadSuggestions } = useSuggestions();
    const debug = useDebug();
    const cacheEntries = useCacheEntries();
    const chat = useChat({
        messages,
        setMessages,
        prompt,
        setPrompt,
        loading,
        setLoading,
        lastFailedPrompt,
        setLastFailedPrompt,
        conversationId: conversations.conversationId,
        setConversationId: conversations.setConversationId,
        loadConversations: conversations.loadConversations,
        loadDebugLogs: debug.loadDebugLogs,
        setDebugLogEntries: (entries) => debug.setDebugLogEntries(entries)
    });

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, loading]);

    useEffect(() => {
        if (viewMode === 'telemetry') {
            cacheEntries.loadCacheEntries();
        }
    }, [viewMode, cacheEntries.loadCacheEntries]);

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            const target = e.target as Node;
            if (
                showHistory &&
                historyTriggerRef.current &&
                !historyTriggerRef.current.contains(target) &&
                historyPanelRef.current &&
                !historyPanelRef.current.contains(target)
            ) {
                setShowHistory(false);
            }
            if (
                showSuggestions &&
                suggestionsContainerRef.current &&
                !suggestionsContainerRef.current.contains(target)
            ) {
                setShowSuggestions(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showHistory, showSuggestions, setShowSuggestions]);

    return (
        <div
            style={{
                maxWidth: '1200px',
                margin: '0 auto',
                height: viewMode === 'assistant' ? 'calc(100vh - 40px)' : 'auto',
                minHeight: viewMode === 'telemetry' ? 'calc(100vh - 40px)' : undefined,
                display: 'flex',
                flexDirection: 'column'
            }}
        >
            <AIModeHeader
                viewMode={viewMode}
                onViewModeChange={setViewMode}
                history={{
                    show: showHistory,
                    onToggle: () => setShowHistory(s => !s),
                    conversations: conversations.conversations,
                    currentId: conversations.conversationId,
                    onSelect: conversations.loadConversation,
                    onDelete: conversations.deleteConversation,
                    panelRef: historyPanelRef,
                    triggerRef: historyTriggerRef
                }}
                suggestions={{
                    show: showSuggestions,
                    onToggle: () => setShowSuggestions(s => !s),
                    onLoad: loadSuggestions,
                    items: suggestions,
                    onSelect: (q: string) => {
                        chat.handleAsk(q);
                        setShowSuggestions(false);
                    },
                    onClose: () => setShowSuggestions(false),
                    containerRef: suggestionsContainerRef
                }}
                debug={{
                    show: debug.showDebug,
                    onToggle: debug.handleDebug
                }}
                onNewChat={conversations.startNewConversation}
                messageCount={messages.length}
                onUndo={chat.handleUndo}
                lastFailedPrompt={lastFailedPrompt}
                onRetry={chat.handleRetry}
                onResetAll={async () => {
                    await endpoints.ai.clearCache();
                    cacheEntries.loadCacheEntries();
                }}
            />

            {viewMode === 'assistant' ? (
                <>
                    <div
                        style={{
                            flex: 1,
                            overflowY: 'auto',
                            padding: '0 10px',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: '20px'
                        }}
                    >
                        {messages.length === 0 && <EmptyState onAsk={chat.handleAsk} />}
                        {messages.map((msg, idx) => (
                            <ChatMessage
                                key={idx}
                                msg={msg}
                                index={idx}
                                onFeedback={chat.handleFeedback}
                            />
                        ))}
                        {loading && (
                            <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                                <div
                                    className="card"
                                    style={{
                                        padding: '15px 20px',
                                        borderRadius: '20px 20px 20px 0',
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: '10px'
                                    }}
                                >
                                    <LoadingSpinner size="small" />
                                    <span style={{ color: 'var(--text-secondary)' }}>Thinking...</span>
                                </div>
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </div>
                    <ChatInput
                        value={prompt}
                        onChange={setPrompt}
                        onSubmit={() => chat.handleAsk()}
                        loading={loading}
                    />
                </>
            ) : (
                <TelemetryView
                    entries={cacheEntries.entries}
                    loading={cacheEntries.loading}
                    onRefresh={cacheEntries.loadCacheEntries}
                    onMarkIncorrect={async (keyHash, isIncorrect) => {
                        await endpoints.ai.patchCacheEntry(keyHash, isIncorrect);
                        cacheEntries.loadCacheEntries();
                    }}
                />
            )}

            {debug.showDebug && (
                <DebugOverlay
                    entries={debug.debugLogEntries}
                    onClose={() => debug.setShowDebug(false)}
                />
            )}
        </div>
    );
}
