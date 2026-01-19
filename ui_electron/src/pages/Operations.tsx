import { useState, useEffect } from 'react';
import { endpoints } from '../api';
import type { JobResponse } from '../api';

export default function Operations() {
    const [job, setJob] = useState<JobResponse | null>(null);
    const [polling, setPolling] = useState(false);

    const startSync = async () => {
        try {
            const res = await endpoints.sync.run();
            setJob(res.data);
            setPolling(true);
        } catch (err) {
            console.error(err);
            alert("Failed to start sync");
        }
    };

    useEffect(() => {
        let interval: any;
        if (polling && job) {
            interval = setInterval(async () => {
                try {
                    const res = await endpoints.sync.status(job.job_id);
                    setJob(res.data);
                    if (res.data.status === 'completed' || res.data.status === 'failed') {
                        setPolling(false);
                    }
                } catch (err) {
                    console.error("Polling error", err);
                    setPolling(false);
                }
            }, 1000);
        }
        return () => clearInterval(interval);
    }, [polling, job]);

    return (
        <div>
            <h1>Operations</h1>

            <div className="card">
                <h2>Database Sync</h2>
                <p>Sync orders from remote database to local analytics DB.</p>
                <button
                    onClick={startSync}
                    disabled={polling}
                    style={{
                        backgroundColor: polling ? '#555' : '#646cff',
                        color: 'white',
                        padding: '10px 20px',
                        border: 'none',
                        borderRadius: '4px',
                        cursor: polling ? 'not-allowed' : 'pointer'
                    }}
                >
                    {polling ? 'Syncing...' : 'Start Sync'}
                </button>
            </div>

            {job && (
                <div className="card">
                    <h3>Sync Status</h3>
                    <div style={{ marginBottom: '10px' }}>
                        <strong>Status:</strong> <span style={{
                            color: job.status === 'completed' ? '#4caf50' :
                                job.status === 'failed' ? '#f44336' : '#ff9800'
                        }}>{job.status.toUpperCase()}</span>
                    </div>
                    <div style={{ marginBottom: '10px' }}>
                        <strong>Message:</strong> {job.message}
                    </div>
                    {job.status === 'running' && (
                        <div style={{ width: '100%', backgroundColor: '#444', height: '10px', borderRadius: '5px' }}>
                            <div style={{
                                width: `${Math.round(job.progress * 100)}%`,
                                backgroundColor: '#646cff',
                                height: '100%',
                                borderRadius: '5px',
                                transition: 'width 0.3s ease'
                            }} />
                        </div>
                    )}
                    {job.stats && (
                        <div style={{ marginTop: '10px', fontSize: '0.9em', color: '#ccc' }}>
                            <pre>{JSON.stringify(job.stats, null, 2)}</pre>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
