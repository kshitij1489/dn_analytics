/**
 * Resizable Table Wrapper Component
 * 
 * Provides a resizable container for tables with export functionality.
 * Used across Insights, Menu, and Orders pages.
 */

import { useState, useEffect, useRef } from 'react';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';

interface ResizableTableWrapperProps {
    children: React.ReactNode;
    onExportCSV?: () => void;
    defaultHeight?: number;
}

export function ResizableTableWrapper({
    children,
    onExportCSV,
    defaultHeight = 600
}: ResizableTableWrapperProps) {
    const [width, setWidth] = useState(1000);
    const [height, setHeight] = useState(defaultHeight);
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
        <div ref={containerRef} style={{ width: '100%', marginBottom: '20px' }}>
            <div style={{
                display: 'flex',
                justifyContent: 'flex-end',
                marginBottom: '10px',
            }}>
                {onExportCSV && (
                    <button
                        onClick={onExportCSV}
                        title="Export to CSV"
                        style={{
                            background: '#3B82F6',
                            border: 'none',
                            borderRadius: '8px',
                            padding: '8px 16px',
                            color: 'white',
                            cursor: 'pointer',
                            fontSize: '13px',
                            fontWeight: 'bold'
                        }}
                    >
                        📥 Export CSV
                    </button>
                )}
            </div>

            <Resizable
                height={height}
                width={width}
                onResize={onResize}
                resizeHandles={['s', 'e', 'se']}
                minConstraints={[400, 300]}
                maxConstraints={[2400, 1200]}
                handle={(handleAxis, ref) => (
                    <div
                        ref={ref}
                        className={`react-resizable-handle react-resizable-handle-${handleAxis}`}
                        style={{
                            position: 'absolute',
                            userSelect: 'none',
                            width: '20px',
                            height: '20px',
                            bottom: 0,
                            right: 0,
                            cursor: 'se-resize',
                            zIndex: 10,
                            background: handleAxis === 'se'
                                ? 'linear-gradient(135deg, transparent 50%, var(--accent-color) 50%)'
                                : 'transparent',
                            borderRadius: '0 0 4px 0'
                        }}
                    />
                )}
            >
                <div style={{
                    width: width + 'px',
                    height: height + 'px',
                    position: 'relative',
                    border: '1px solid var(--border-color)',
                    borderRadius: '8px',
                    background: 'var(--card-bg)',
                    boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
                    display: 'flex',
                    flexDirection: 'column'
                }}>
                    <div style={{
                        flex: 1,
                        overflow: 'auto',
                        width: '100%',
                        height: '100%',
                        paddingBottom: '10px'
                    }}>
                        {children}
                    </div>
                </div>
            </Resizable>
        </div>
    );
}
