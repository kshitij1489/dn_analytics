import { HistoryPanel } from './HistoryPanel';
import { SuggestionsDropdown } from './SuggestionsDropdown';
import type { Conversation, Suggestion } from '../types';
import { segmentButtonStyle, pillButtonStyle, pillContainerStyle, segmentedControlContainerStyle } from '../styles';

type ViewMode = 'assistant' | 'telemetry';

/** Props for History panel functionality */
interface HistoryProps {
    show: boolean;
    onToggle: () => void;
    conversations: Conversation[];
    currentId: string | null;
    onSelect: (id: string) => void;
    onDelete: (id: string, e: React.MouseEvent) => void;
    panelRef: React.RefObject<HTMLDivElement | null>;
    triggerRef: React.RefObject<HTMLButtonElement | null>;
}

/** Props for Suggestions dropdown functionality */
interface SuggestionsProps {
    show: boolean;
    onToggle: () => void;
    onLoad: () => void;
    items: Suggestion[];
    onSelect: (query: string) => void;
    onClose: () => void;
    containerRef: React.RefObject<HTMLDivElement | null>;
}

/** Props for Debug functionality */
interface DebugProps {
    show: boolean;
    onToggle: () => void;
}

const RESET_ALL_CONFIRM_MESSAGE =
    'This will delete all entries in the LLM cache. Do you still want to proceed?';

interface AIModeHeaderProps {
    viewMode: ViewMode;
    onViewModeChange: (mode: ViewMode) => void;
    history: HistoryProps;
    suggestions: SuggestionsProps;
    debug: DebugProps;
    onNewChat: () => void;
    messageCount: number;
    onUndo: () => void;
    lastFailedPrompt: string | null;
    onRetry: () => void;
    onResetAll?: () => void | Promise<void>;
}

export function AIModeHeader({
    viewMode,
    onViewModeChange,
    history,
    suggestions,
    debug,
    onNewChat,
    messageCount,
    onUndo,
    lastFailedPrompt,
    onRetry,
    onResetAll
}: AIModeHeaderProps) {
    const handleResetAll = async () => {
        if (!onResetAll || !window.confirm(RESET_ALL_CONFIRM_MESSAGE)) return;
        try {
            await Promise.resolve(onResetAll());
        } catch (err) {
            console.error('Failed to reset LLM cache:', err);
            alert('Failed to reset cache. Please try again.');
        }
    };

    return (
        <div
            style={{
                padding: '20px 0',
                borderBottom: '1px solid var(--border-color)',
                marginBottom: '20px',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center'
            }}
        >
            <div>
                <div style={pillContainerStyle}>
                    <button
                        type="button"
                        onClick={() => onViewModeChange('assistant')}
                        style={pillButtonStyle(viewMode === 'assistant')}
                    >
                        AI Assistant
                    </button>
                    <button
                        type="button"
                        onClick={() => onViewModeChange('telemetry')}
                        style={pillButtonStyle(viewMode === 'telemetry')}
                    >
                        Telemetry
                    </button>
                </div>
                <p style={{ margin: '8px 0 0', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
                    {viewMode === 'assistant'
                        ? 'Ask questions, generate reports, and analyze your data.'
                        : 'Improving model accuracy through your feedback'}
                </p>
            </div>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                {viewMode === 'telemetry' && onResetAll && (
                    <button
                        type="button"
                        onClick={handleResetAll}
                        style={{
                            padding: '10px 16px',
                            background: 'rgba(156, 163, 175, 0.35)',
                            border: '1px solid rgba(156, 163, 175, 0.5)',
                            borderRadius: '12px',
                            cursor: 'pointer',
                            color: 'var(--text-color)',
                            fontSize: '0.9rem'
                        }}
                        title="Clear all LLM cache entries (with confirmation)"
                    >
                        Reset All
                    </button>
                )}
                {viewMode === 'assistant' && (
                    <div style={segmentedControlContainerStyle}>
                        <button
                            ref={history.triggerRef}
                            type="button"
                            onClick={history.onToggle}
                            style={segmentButtonStyle(history.show)}
                        >
                            📜 History
                        </button>
                        <button
                            type="button"
                            onClick={onNewChat}
                            style={segmentButtonStyle(false)}
                            onMouseEnter={e => {
                                e.currentTarget.style.background = 'var(--card-bg)';
                                e.currentTarget.style.color = 'var(--text-color)';
                            }}
                            onMouseLeave={e => {
                                e.currentTarget.style.background = 'transparent';
                                e.currentTarget.style.color = 'var(--text-secondary)';
                            }}
                        >
                            ➕ New Chat
                        </button>
                        <div ref={suggestions.containerRef} style={{ position: 'relative', display: 'flex' }}>
                            <button
                                type="button"
                                onClick={() => {
                                    suggestions.onToggle();
                                    if (!suggestions.show) suggestions.onLoad();
                                }}
                                style={segmentButtonStyle(suggestions.show)}
                            >
                                💡 Suggestions
                            </button>
                            {suggestions.show && (
                                <SuggestionsDropdown
                                    suggestions={suggestions.items}
                                    onSelect={suggestions.onSelect}
                                    onClose={suggestions.onClose}
                                />
                            )}
                        </div>
                        {messageCount >= 2 && (
                            <button
                                type="button"
                                onClick={onUndo}
                                style={segmentButtonStyle(false)}
                                onMouseEnter={e => {
                                    e.currentTarget.style.background = 'var(--card-bg)';
                                    e.currentTarget.style.color = 'var(--text-color)';
                                }}
                                onMouseLeave={e => {
                                    e.currentTarget.style.background = 'transparent';
                                    e.currentTarget.style.color = 'var(--text-secondary)';
                                }}
                            >
                                ↩️ Undo
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={debug.onToggle}
                            style={segmentButtonStyle(debug.show)}
                        >
                            🐞 Debug
                        </button>
                    </div>
                )}
                {lastFailedPrompt && (
                    <button
                        type="button"
                        onClick={onRetry}
                        style={{
                            padding: '8px 16px',
                            background: '#ef4444',
                            border: 'none',
                            borderRadius: '8px',
                            cursor: 'pointer',
                            color: 'white',
                            fontSize: '0.85rem'
                        }}
                    >
                        🔄 Retry
                    </button>
                )}
            </div>
            {history.show && (
                <HistoryPanel
                    conversations={history.conversations}
                    conversationId={history.currentId}
                    onSelect={history.onSelect}
                    onDelete={history.onDelete}
                    panelRef={history.panelRef}
                />
            )}
        </div>
    );
}
