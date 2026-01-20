/**
 * Resizable Chart Component
 * 
 * Provides a resizable container for charts with fullscreen functionality.
 * Supports both horizontal and vertical resizing with intuitive drag behavior.
 * Used across Insights charts.
 */

import { useState, useEffect, useRef } from 'react';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';

interface ResizableChartProps {
    children: React.ReactNode;
    defaultHeight?: number;
    onFullscreen?: () => void;
}

export function ResizableChart({
    children,
    defaultHeight = 400,
    onFullscreen
}: ResizableChartProps) {
    const [height, setHeight] = useState(defaultHeight);
    const [width, setWidth] = useState(800);
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (containerRef.current) {
            setWidth(containerRef.current.offsetWidth);
        }
    }, []);

    const onResize = (_event: any, { size }: any) => {
        setHeight(size.height);
        setWidth(size.width);
    };

    return (
        <div ref={containerRef} style={{ position: 'relative', width: '100%' }}>
            {onFullscreen && (
                <button
                    onClick={onFullscreen}
                    title="Open in fullscreen"
                    style={{
                        position: 'absolute',
                        top: '10px',
                        right: '10px',
                        zIndex: 10,
                        background: 'var(--accent-color)',
                        border: 'none',
                        borderRadius: '4px',
                        padding: '6px 12px',
                        color: 'white',
                        cursor: 'pointer',
                        fontSize: '12px',
                        fontWeight: 'bold'
                    }}
                >
                    ⛶ Fullscreen
                </button>
            )}
            <Resizable
                height={height}
                width={width}
                onResize={onResize}
                resizeHandles={['s', 'e', 'se']}
                minConstraints={[300, 200]}
                maxConstraints={[2000, 800]}
                handle={(handleAxis, ref) => (
                    <div
                        ref={ref}
                        className={`react-resizable-handle react-resizable-handle-${handleAxis}`}
                        style={{
                            position: 'absolute',
                            userSelect: 'none',
                            width: handleAxis === 'se' ? '20px' : '10px',
                            height: handleAxis === 'se' ? '20px' : '10px',
                            bottom: handleAxis === 's' || handleAxis === 'se' ? 0 : undefined,
                            right: handleAxis === 'e' || handleAxis === 'se' ? 0 : undefined,
                            cursor: handleAxis === 'se' ? 'se-resize' : handleAxis === 's' ? 's-resize' : 'e-resize',
                            zIndex: 10,
                            background: handleAxis === 'se'
                                ? 'linear-gradient(135deg, transparent 50%, var(--accent-color) 50%)'
                                : 'transparent',
                            borderRadius: handleAxis === 'se' ? '0 0 4px 0' : '0'
                        }}
                    />
                )}
            >
                <div style={{
                    width: width + 'px',
                    height: height + 'px',
                    position: 'relative',
                    marginBottom: '20px',
                    background: 'var(--card-bg)',
                    padding: '15px',
                    borderRadius: '12px',
                    border: '1px solid var(--border-color)',
                    boxShadow: 'var(--shadow)'
                }}>
                    {children}
                </div>
            </Resizable>
        </div>
    );
}
