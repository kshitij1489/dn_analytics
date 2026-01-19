import { useState, useEffect } from 'react';
import './App.css';
import { endpoints } from './api';
import type { JobResponse } from './api';

// Components
import Insights from './pages/Insights';
import Menu from './pages/Menu';
import Resolutions from './pages/Resolutions';
import SQLConsole from './pages/SQLConsole';
import ComingSoon from './pages/ComingSoon';

type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

function App() {
  const [activeTab, setActiveTab] = useState('insights');
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');

  // Sync State
  const [job, setJob] = useState<JobResponse | null>(null);
  const [polling, setPolling] = useState(false);

  useEffect(() => {
    checkConnection();
    checkDataAndSync();

    // Check connection every 30 seconds
    const interval = setInterval(() => checkConnection(), 30000);
    return () => clearInterval(interval);
  }, []);

  // Sync Polling Effect
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

  const checkConnection = async (attempt = 1) => {
    const maxAttempts = 3;
    if (attempt === 1) setConnectionStatus('connecting');

    try {
      await endpoints.health();
      setConnectionStatus('connected');
    } catch (err) {
      if (attempt < maxAttempts) {
        setTimeout(() => checkConnection(attempt + 1), 1000);
      } else {
        setConnectionStatus('disconnected');
      }
    }
  };

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

  const getStatusDisplay = () => {
    switch (connectionStatus) {
      case 'connected':
        return { icon: '🟢', text: 'Connected', color: '#10B981' };
      case 'connecting':
        return { icon: '🟡', text: 'Connecting...', color: '#F59E0B' };
      case 'disconnected':
        return { icon: '🔴', text: 'Not Connected', color: '#EF4444' };
    }
  };

  const status = getStatusDisplay();

  const checkDataAndSync = async () => {
    try {
      // Check if we have any menu types (cheap check for data existence)
      const res = await endpoints.menu.types();
      if (res.data.length === 0) {
        console.log("No data found, identifying as fresh install. Triggering auto-sync...");

        // Trigger Sync
        startSync();
        alert("Startup: No data found. Auto-sync started.");
      }
    } catch (e) {
      console.error("Auto-sync check failed", e);
    }
  };

  return (
    <div className="app-container">
      <aside className="sidebar">
        <div className="logo">Analytics</div>
        <nav>
          <button className={activeTab === 'insights' ? 'active' : ''} onClick={() => setActiveTab('insights')}>Insights</button>
          <button className={activeTab === 'menu' ? 'active' : ''} onClick={() => setActiveTab('menu')}>Menu</button>

          <button className={activeTab === 'orders' ? 'active' : ''} onClick={() => setActiveTab('orders')}>Orders</button>
          <button className={activeTab === 'inventory' ? 'active' : ''} onClick={() => setActiveTab('inventory')}>Inventory & COGS</button>

          <button className={activeTab === 'resolutions' ? 'active' : ''} onClick={() => setActiveTab('resolutions')}>Resolutions</button>
          <button className={activeTab === 'sql' ? 'active' : ''} onClick={() => setActiveTab('sql')}>SQL Console</button>

          <button className={activeTab === 'ai_mode' ? 'active' : ''} onClick={() => setActiveTab('ai_mode')}>AI Mode</button>

          {/* Connection Status */}
          <div style={{
            marginTop: '10px',
            padding: '8px 12px',
            fontSize: '12px',
            backgroundColor: '#2d2d2d',
            borderRadius: '4px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            color: status.color,
            fontWeight: '500'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span>{status.icon}</span>
              <span>{status.text}</span>
              {connectionStatus === 'disconnected' && (
                <button
                  onClick={() => checkConnection()}
                  style={{
                    marginLeft: 'auto',
                    padding: '2px 8px',
                    fontSize: '10px',
                    background: '#646cff',
                    color: 'white',
                    border: 'none',
                    borderRadius: '3px',
                    cursor: 'pointer'
                  }}
                >
                  Retry
                </button>
              )}
            </div>

            {/* Sync Button */}
            <button
              onClick={startSync}
              disabled={polling || connectionStatus !== 'connected'}
              title="Sync Database"
              style={{
                width: '100%',
                padding: '6px',
                fontSize: '12px',
                background: polling ? '#555' : '#646cff',
                color: 'white',
                border: 'none',
                borderRadius: '3px',
                cursor: (polling || connectionStatus !== 'connected') ? 'not-allowed' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '5px'
              }}
            >
              {polling ? '🔄 Syncing...' : '🔄 Sync DB'}
            </button>

            {/* Sync Progress */}
            {job && polling && (
              <div style={{ width: '100%', backgroundColor: '#444', height: '4px', borderRadius: '2px', overflow: 'hidden' }}>
                <div style={{
                  width: `${Math.round(job.progress * 100)}%`,
                  backgroundColor: '#10B981',
                  height: '100%',
                  transition: 'width 0.3s ease'
                }} />
              </div>
            )}

            {/* Sync Result Label */}
            {job && job.status === 'completed' && !polling && (
              <div style={{
                fontSize: '11px',
                color: '#aaa',
                textAlign: 'center',
                marginTop: '4px'
              }}>
                {(job.stats?.count === 0 || job.stats?.orders === 0)
                  ? 'No orders to Update'
                  : `${job.stats?.orders || 0} orders updated`
                }
              </div>
            )}
          </div>
        </nav>
      </aside>
      <main className="main-content">
        {activeTab === 'insights' && <Insights />}
        {activeTab === 'menu' && <Menu />}
        {activeTab === 'orders' && <ComingSoon title="Orders" />}
        {activeTab === 'inventory' && <ComingSoon title="Inventory & COGS" />}
        {activeTab === 'resolutions' && <Resolutions />}
        {activeTab === 'sql' && <SQLConsole />}
        {activeTab === 'ai_mode' && <ComingSoon title="AI Mode" />}
      </main>
    </div>
  );
}

export default App;
