import { useState, useEffect } from 'react';
import './App.css';
import { endpoints } from './api';
import type { JobResponse } from './api';

// Components
import Insights from './pages/Insights';
import Menu from './pages/Menu';
import Orders from './pages/Orders';
import SQLConsole from './pages/SQLConsole';
import ComingSoon from './pages/ComingSoon';

type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

function App() {
  const [activeTab, setActiveTab] = useState('insights');
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');
  const [theme, setTheme] = useState<'dark' | 'light'>('light');

  // Sync State
  const [job, setJob] = useState<JobResponse | null>(null);
  const [polling, setPolling] = useState(false);
  const [lastDbSync, setLastDbSync] = useState<number>(Date.now());

  // Theme Config
  useEffect(() => {
    if (theme === 'light') {
      document.body.classList.add('light-mode');
    } else {
      document.body.classList.remove('light-mode');
    }
  }, [theme]);

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
            if (res.data.status === 'completed') {
              setLastDbSync(Date.now());
            }
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

  const [showSyncStatus, setShowSyncStatus] = useState(false);
  useEffect(() => {
    if (job?.status === 'completed') {
      setShowSyncStatus(true);
      const timer = setTimeout(() => setShowSyncStatus(false), 30000); // Hide after 30s
      return () => clearTimeout(timer);
    }
  }, [job]);

  return (
    <div className="app-container">
      <aside className="sidebar">
        <img
          src="/logo.png"
          alt="D&N Dashboard"
          style={{
            display: 'block',
            margin: '0 auto 30px',
            maxWidth: '100%',
            objectFit: 'contain',
            height: 'auto'
          }}
        />
        <nav>
          <button className={activeTab === 'insights' ? 'active' : ''} onClick={() => setActiveTab('insights')}>Insights</button>
          <button className={activeTab === 'menu' ? 'active' : ''} onClick={() => setActiveTab('menu')}>Menu</button>

          <button className={activeTab === 'orders' ? 'active' : ''} onClick={() => setActiveTab('orders')}>Orders</button>
          <button className={activeTab === 'inventory' ? 'active' : ''} onClick={() => setActiveTab('inventory')}>Inventory & COGS</button>
          <button className={activeTab === 'sql' ? 'active' : ''} onClick={() => setActiveTab('sql')}>SQL Console</button>

          <button className={activeTab === 'ai_mode' ? 'active' : ''} onClick={() => setActiveTab('ai_mode')}>AI Mode</button>

          <hr style={{ borderTop: '1px solid #444', margin: '15px 0' }} />

          {/* Connection Status */}
          <div style={{
            marginTop: '10px',
            padding: '8px 12px',
            fontSize: '12px',
            // backgroundColor: '#2d2d2d', Removed black background section
            borderRadius: '4px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            color: status.color,
            fontWeight: '500'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span>{status.icon}</span>
              <span style={{ color: 'var(--text-color)' }}>{status.text}</span>
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
                padding: '12px',
                fontSize: '14px',
                fontWeight: '600',
                background: polling ? '#555' : '#E65100', // Dark Orange
                color: 'white',
                border: 'none',
                borderRadius: '8px',
                cursor: (polling || connectionStatus !== 'connected') ? 'not-allowed' : 'pointer',
                display: 'block',
                marginTop: '10px',
                textAlign: 'center'
              }}
            >
              {polling ? 'Syncing...' : 'Sync DB'}
            </button>

            {/* Sync Progress */}
            {job && polling && (
              <div style={{ width: '100%', backgroundColor: '#444', height: '4px', borderRadius: '2px', overflow: 'hidden' }}>
                <div style={{
                  width: `${Math.round(job.progress * 100)}% `,
                  backgroundColor: '#10B981',
                  height: '100%',
                  transition: 'width 0.3s ease'
                }} />
              </div>
            )}

            {/* Sync Result Label */}
            {job && job.status === 'completed' && !polling && showSyncStatus && (
              <div style={{
                fontSize: '11px',
                color: 'var(--text-color)', // Adapted to theme
                textAlign: 'center',
                marginTop: '4px',
                fontWeight: 'bold'
              }}>
                {(job.stats?.count === 0 || job.stats?.orders === 0)
                  ? 'No orders to Update'
                  : `${job.stats?.orders || 0} orders updated`
                }
              </div>
            )}
          </div>

          <hr style={{ borderTop: '1px solid #444', margin: '15px 0' }} />

        </nav>

        {/* Theme Toggle Slider box */}
        <div style={{ marginTop: 'auto', padding: '0 0 20px 0' }}>
          <div style={{
            textAlign: 'center',
            color: 'var(--text-secondary)',
            fontSize: '12px',
            marginBottom: '8px',
            textTransform: 'uppercase',
            letterSpacing: '0.5px'
          }}>
            Appearance
          </div>
          <div style={{
            display: 'flex',
            background: 'var(--input-bg)',
            borderRadius: '20px',
            padding: '4px',
            border: '1px solid var(--border-color)',
            userSelect: 'none'
          }}>
            <div
              onClick={() => setTheme('light')}
              style={{
                flex: 1,
                textAlign: 'center',
                padding: '6px',
                borderRadius: '16px',
                background: theme === 'light' ? 'var(--card-bg)' : 'transparent',
                color: theme === 'light' ? 'var(--text-color)' : 'var(--text-secondary)',
                boxShadow: theme === 'light' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                transition: 'all 0.2s',
                cursor: 'pointer',
                fontWeight: '600',
                fontSize: '0.85em'
              }}
            >
              Light
            </div>
            <div
              onClick={() => setTheme('dark')}
              style={{
                flex: 1,
                textAlign: 'center',
                padding: '6px',
                borderRadius: '16px',
                background: theme === 'dark' ? 'var(--card-bg)' : 'transparent',
                color: theme === 'dark' ? 'var(--text-color)' : 'var(--text-secondary)',
                boxShadow: theme === 'dark' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
                transition: 'all 0.2s',
                cursor: 'pointer',
                fontWeight: '600',
                fontSize: '0.85em'
              }}
            >
              Dark
            </div>
          </div>
        </div>
      </aside>
      <main className="main-content">
        {activeTab === 'insights' && <Insights lastDbSync={lastDbSync} />}
        {activeTab === 'menu' && <Menu lastDbSync={lastDbSync} />}
        {activeTab === 'orders' && <Orders lastDbSync={lastDbSync} />}
        {activeTab === 'inventory' && <ComingSoon title="Inventory & COGS" />}
        {activeTab === 'sql' && <SQLConsole />}
        {activeTab === 'ai_mode' && <ComingSoon title="AI Mode" />}
      </main>
    </div >
  );
}

export default App;
