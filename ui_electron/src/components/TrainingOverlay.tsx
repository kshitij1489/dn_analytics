import React, { useEffect, useRef } from 'react';

/**
 * TrainingOverlay — Semi-transparent overlay displayed over the forecast content
 * area while model training is in progress.  Shows phase, progress bar, and a
 * scrolling log feed pulled from /forecast/training-status.
 */

interface TrainingStatus {
    active: boolean;
    phase: string;
    progress: number;
    message: string;
    logs: string[];
}

interface TrainingOverlayProps {
    status: TrainingStatus;
}

const phaseLabels: Record<string, string> = {
    revenue: 'Revenue Models',
    items: 'Item Demand',
    volume: 'Volume Forecast',
};

const TrainingOverlay: React.FC<TrainingOverlayProps> = ({ status }) => {
    const logEndRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [status.logs]);

    const phaseLabel = phaseLabels[status.phase] || status.phase || 'Initialising';

    return (
        <div className="training-overlay-backdrop">
            <div className="training-overlay-card">
                <div className="training-overlay-header">
                    <span className="training-overlay-spinner" />
                    <h3>Training in Progress</h3>
                </div>

                <p className="training-overlay-phase">
                    Phase: <strong>{phaseLabel}</strong>
                </p>

                {/* Progress bar */}
                <div className="training-overlay-progress-track">
                    <div
                        className="training-overlay-progress-fill"
                        style={{ width: `${Math.min(status.progress, 100)}%` }}
                    />
                </div>
                <p className="training-overlay-percent">{status.progress}%</p>

                {/* Message */}
                <p className="training-overlay-message">{status.message}</p>

                {/* Log feed */}
                <div className="training-overlay-logs">
                    {status.logs.map((line, i) => (
                        <div key={i} className="training-overlay-log-line">{line}</div>
                    ))}
                    <div ref={logEndRef} />
                </div>
            </div>
        </div>
    );
};

export default TrainingOverlay;
export type { TrainingStatus };
