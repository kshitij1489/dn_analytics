import type { Message, ResponsePart } from '../types';
import { TablePart } from './TablePart';
import { ChartPart } from './ChartPart';

interface AIMessageContentProps {
    msg: Message;
}

function renderPart(part: ResponsePart) {
    if (part.type === 'text') {
        return (
            <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
                {String(part.content ?? '')}
            </div>
        );
    }
    if (part.type === 'table') {
        const rows = (Array.isArray(part.content) ? part.content : []) as Record<string, unknown>[];
        return <TablePart rows={rows} explanation={part.explanation} />;
    }
    if (part.type === 'chart') {
        const config = (part.content && typeof part.content === 'object' ? part.content : {}) as Parameters<typeof ChartPart>[0];
        return <ChartPart {...config} />;
    }
    return null;
}

export function AIMessageContent({ msg }: AIMessageContentProps) {
    if (msg.type === 'multi' && Array.isArray(msg.content)) {
        const parts = msg.content as ResponsePart[];
        return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
                {parts.map((part, partIdx) => (
                    <div key={partIdx}>{renderPart(part)}</div>
                ))}
            </div>
        );
    }

    if (msg.type === 'table') {
        const rows = (Array.isArray(msg.content) ? msg.content : []) as Record<string, unknown>[];
        return <TablePart rows={rows} explanation={msg.explanation} />;
    }

    if (msg.type === 'chart') {
        const config = (msg.content && typeof msg.content === 'object' ? msg.content : {}) as Parameters<typeof ChartPart>[0];
        return <ChartPart {...config} />;
    }

    return (
        <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
            {String(msg.content ?? '')}
        </div>
    );
}
