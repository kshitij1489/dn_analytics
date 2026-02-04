import type { DebugLogEntry } from '../../../api';
import type { ResponsePart } from '../types';

export interface StreamCallbacks {
    onChunk: (content: string) => void;
    onPart: (part: ResponsePart) => void;
    onDebug: (entries: DebugLogEntry[]) => void;
    onComplete: (response: { content?: unknown; type?: string; [key: string]: unknown }) => void;
    onError: (message: string) => void;
}

export async function parseAIStream(
    body: ReadableStream<Uint8Array>,
    callbacks: StreamCallbacks
): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let sseBuffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        sseBuffer += decoder.decode(value, { stream: true });
        const eventBlocks = sseBuffer.split('\n\n');
        sseBuffer = eventBlocks.pop() ?? '';

        for (const line of eventBlocks) {
            if (!line.startsWith('data: ')) continue;
            try {
                const data = JSON.parse(line.substring(6)) as {
                    type: string;
                    content?: string;
                    part?: ResponsePart;
                    entries?: DebugLogEntry[];
                    response?: { content?: unknown; type?: string; [key: string]: unknown };
                    message?: string;
                };

                switch (data.type) {
                    case 'status':
                        break;
                    case 'chunk':
                        if (data.content != null) callbacks.onChunk(data.content);
                        break;
                    case 'part':
                        if (data.part) callbacks.onPart(data.part);
                        break;
                    case 'debug':
                        if (Array.isArray(data.entries)) callbacks.onDebug(data.entries);
                        break;
                    case 'complete':
                        if (data.response) callbacks.onComplete(data.response);
                        break;
                    case 'error':
                        callbacks.onError(data.message ?? 'Unknown error');
                        return;
                    default:
                        break;
                }
            } catch {
                // skip malformed or incomplete JSON (e.g. chunk boundary)
            }
        }
    }
}
