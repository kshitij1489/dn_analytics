import { ActionButton } from '../../../components';

interface ChatInputProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    loading: boolean;
}

export function ChatInput({ value, onChange, onSubmit, loading }: ChatInputProps) {
    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            onSubmit();
        }
    };

    return (
        <div style={{ padding: '20px 0', marginTop: '10px' }}>
            <div style={{ position: 'relative' }}>
                <textarea
                    value={value}
                    onChange={e => onChange(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Type your question..."
                    rows={1}
                    style={{
                        width: '100%',
                        padding: '16px 60px 16px 20px',
                        fontSize: '1rem',
                        borderRadius: '30px',
                        border: '1px solid var(--border-color)',
                        background: 'var(--input-bg)',
                        color: 'var(--text-color)',
                        resize: 'none',
                        outline: 'none',
                        boxSizing: 'border-box',
                        boxShadow: '0 4px 12px rgba(0,0,0,0.1)'
                    }}
                />
                <div
                    style={{
                        position: 'absolute',
                        right: '8px',
                        top: '50%',
                        transform: 'translateY(-50%)'
                    }}
                >
                    <ActionButton
                        variant="primary"
                        onClick={onSubmit}
                        disabled={!value.trim() || loading}
                        style={{
                            borderRadius: '50%',
                            width: '40px',
                            height: '40px',
                            padding: '0',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center'
                        }}
                    >
                        ➤
                    </ActionButton>
                </div>
            </div>
        </div>
    );
}
