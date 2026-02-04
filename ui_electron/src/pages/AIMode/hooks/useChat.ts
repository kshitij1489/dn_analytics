import { useCallback } from 'react';
import { endpoints, type DebugLogEntry } from '../../../api';
import type { Message, ResponsePart } from '../types';
import { parseAIStream } from '../utils/parseAIStream';

export interface UseChatOptions {
    messages: Message[];
    setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
    prompt: string;
    setPrompt: (v: string) => void;
    loading: boolean;
    setLoading: (v: boolean) => void;
    lastFailedPrompt: string | null;
    setLastFailedPrompt: (v: string | null) => void;
    conversationId: string | null;
    setConversationId: (v: string | null) => void;
    loadConversations: () => void;
    loadDebugLogs: (keepOnError?: boolean) => Promise<void>;
    setDebugLogEntries: (entries: DebugLogEntry[]) => void;
}

export function useChat(options: UseChatOptions) {
    const {
        messages,
        setMessages,
        prompt,
        setPrompt,
        setLoading,
        lastFailedPrompt,
        setLastFailedPrompt,
        conversationId,
        setConversationId,
        loadConversations,
        loadDebugLogs,
        setDebugLogEntries
    } = options;

    const handleAsk = useCallback(
        async (customPrompt?: string) => {
            const queryPrompt = customPrompt ?? prompt;
            if (!queryPrompt.trim()) return;

            let currentConvId = conversationId;
            if (!currentConvId && messages.length === 0) {
                try {
                    const res = await endpoints.conversations.create({ title: queryPrompt.slice(0, 50) });
                    currentConvId = res.data.conversation_id;
                    setConversationId(currentConvId);
                } catch (err) {
                    console.error('Failed to create conversation:', err);
                }
            }

            const userMsg: Message = {
                role: 'user',
                content: queryPrompt
            };
            setMessages(prev => [...prev, userMsg]);
            setPrompt('');
            setLoading(true);
            setLastFailedPrompt(null);

            if (currentConvId) {
                endpoints.conversations
                    .addMessage(currentConvId, { ...userMsg, type: 'text' })
                    .then((res: { data: { message_id: string } }) => {
                        setMessages(prev =>
                            prev.map(m => (m === userMsg ? { ...m, message_id: res.data.message_id } : m))
                        );
                    })
                    .catch((err: unknown) => console.error('Failed to persist user msg', err));
            }

            const isStreamingRequest =
                queryPrompt.toLowerCase().includes('report') || queryPrompt.toLowerCase().includes('summary');
            const history = messages.slice(-10).map(m => ({
                role: m.role,
                content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content)
            }));
            const lastMsg = messages[messages.length - 1];
            const lastAiWasClarification = lastMsg?.role === 'ai' && lastMsg?.query_status === 'incomplete';

            try {
                if (isStreamingRequest) {
                    const aiMsgPlaceholder: Message = { role: 'ai', content: '', type: 'text' };
                    setMessages(prev => [...prev, aiMsgPlaceholder]);

                    const response = await endpoints.ai.chatStream({
                        prompt: queryPrompt,
                        history,
                        last_ai_was_clarification: lastAiWasClarification
                    });
                    if (!response.body) throw new Error('No response body');

                    let accumulatedContent = '';
                    const finalResponseDataRef: { current: { content?: unknown; type?: string; [key: string]: unknown } | null } = { current: null };
                    const parts: ResponsePart[] = [];

                    await parseAIStream(response.body, {
                        onChunk(content) {
                            accumulatedContent += content;
                            setMessages(prev => {
                                const newMsgs = [...prev];
                                const last = newMsgs[newMsgs.length - 1];
                                const currentParts = [...parts];
                                if (accumulatedContent) {
                                    currentParts.push({ type: 'text', content: accumulatedContent });
                                }
                                newMsgs[newMsgs.length - 1] = { ...last, type: 'multi', content: currentParts };
                                return newMsgs;
                            });
                        },
                        onPart(part) {
                            parts.push(part);
                            if (part.type === 'text' && accumulatedContent) accumulatedContent = '';
                            setMessages(prev => {
                                const newMsgs = [...prev];
                                const last = newMsgs[newMsgs.length - 1];
                                const currentParts = [...parts];
                                if (accumulatedContent) {
                                    currentParts.push({ type: 'text', content: accumulatedContent });
                                }
                                newMsgs[newMsgs.length - 1] = { ...last, type: 'multi', content: currentParts };
                                return newMsgs;
                            });
                        },
                        onDebug(entries) {
                            setDebugLogEntries(entries);
                        },
                        onComplete(response) {
                            finalResponseDataRef.current = response;
                        },
                        onError(message) {
                            throw new Error(message);
                        }
                    });

                    const finalResponseData = finalResponseDataRef.current;
                    const resolvedType: Message['type'] =
                        (finalResponseData?.type as Message['type'] | undefined) ?? (parts.length > 0 ? 'multi' : 'text');
                    const resolvedContent: Message['content'] =
                        (finalResponseData?.content ?? (parts.length > 0 ? parts : accumulatedContent)) as Message['content'];
                    const finalAiMsg: Message = {
                        ...aiMsgPlaceholder,
                        ...(finalResponseData || {}),
                        content: resolvedContent,
                        type: resolvedType
                    };

                    if (currentConvId) {
                        endpoints.conversations
                            .addMessage(currentConvId, {
                                ...finalAiMsg,
                                type: finalAiMsg.type ?? 'text',
                                query_status: 'complete'
                            } as Parameters<typeof endpoints.conversations.addMessage>[1])
                            .then((res: { data: { message_id: string } }) => {
                                setMessages(prev =>
                                    prev.map(m =>
                                        m === aiMsgPlaceholder ? { ...finalAiMsg, message_id: res.data.message_id } : m
                                    )
                                );
                            });
                        loadConversations();
                    }
                } else {
                    const res = await endpoints.ai.chat({
                        prompt: queryPrompt,
                        history,
                        last_ai_was_clarification: lastAiWasClarification
                    });
                    const aiMsg: Message = {
                        role: 'ai',
                        content: res.data.content,
                        type: res.data.type as Message['type'],
                        sql_query: res.data.sql_query,
                        explanation: res.data.explanation,
                        query_id: res.data.query_id,
                        corrected_prompt: res.data.corrected_prompt,
                        query_status: res.data.query_status,
                        pending_clarification_question: res.data.pending_clarification_question,
                        previous_query_ignored: res.data.previous_query_ignored
                    };
                    setMessages(prev => [...prev, aiMsg]);
                    loadDebugLogs();
                    if (currentConvId) {
                        endpoints.conversations.addMessage(currentConvId, aiMsg).then((res: { data: { message_id: string } }) => {
                            setMessages(prev =>
                                prev.map(m => (m === aiMsg ? { ...m, message_id: res.data.message_id } : m))
                            );
                        });
                        loadConversations();
                    }
                }
            } catch (err) {
                const errorMsg: Message = {
                    role: 'ai',
                    content: "Sorry, I encountered an error processing your request. Click 'Retry' to try again.",
                    type: 'text'
                };
                setMessages(prev => [...prev, errorMsg]);
                setLastFailedPrompt(queryPrompt);
            } finally {
                setLoading(false);
            }
        },
        [
            prompt,
            messages,
            conversationId,
            setConversationId,
            setMessages,
            setPrompt,
            setLoading,
            setLastFailedPrompt,
            loadConversations,
            loadDebugLogs,
            setDebugLogEntries
        ]
    );

    const handleRetry = useCallback(() => {
        if (lastFailedPrompt) handleAsk(lastFailedPrompt);
    }, [lastFailedPrompt, handleAsk]);

    const handleUndo = useCallback(async () => {
        if (messages.length < 2) return;
        const lastAiMsg = messages[messages.length - 1];
        const lastUserMsg = messages[messages.length - 2];
        setMessages(prev => prev.slice(0, -2));
        setLastFailedPrompt(null);
        if (conversationId) {
            try {
                if (lastAiMsg.message_id) {
                    await endpoints.conversations.deleteMessage(conversationId, lastAiMsg.message_id);
                }
                if (lastUserMsg.message_id) {
                    await endpoints.conversations.deleteMessage(conversationId, lastUserMsg.message_id);
                }
            } catch (err) {
                console.error('Failed to delete undone messages', err);
            }
        }
    }, [messages, conversationId, setMessages, setLastFailedPrompt]);

    const handleFeedback = useCallback(
        async (index: number, isPositive: boolean) => {
            const msg = messages[index];
            if (!msg.query_id || msg.feedback) return;
            try {
                await endpoints.ai.feedback({ query_id: msg.query_id, is_positive: isPositive });
                setMessages(prev =>
                    prev.map((m, i) =>
                        i === index ? { ...m, feedback: isPositive ? 'positive' : 'negative' } : m
                    )
                );
            } catch (err) {
                console.error('Feedback failed', err);
            }
        },
        [messages, setMessages]
    );

    return { handleAsk, handleRetry, handleUndo, handleFeedback };
}
