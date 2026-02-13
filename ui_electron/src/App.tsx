import { useState, useEffect } from 'react';
import './App.css';
import { endpoints } from './api';
import type { JobResponse } from './api';
import { NavigationProvider, useNavigation } from './contexts/NavigationContext'; // Import Context
import { ErrorPopup } from './components';
import type { PopupMessage } from './components';

// Components
import Insights from './pages/Insights';
import Menu from './pages/Menu';
import Orders from './pages/Orders';
import SQLConsole from './pages/SQLConsole';
import ComingSoon from './pages/ComingSoon';
import TodayPage from './pages/TodayPage';
import ChartPage from './pages/ChartPage';
import Configuration from './pages/Configuration';
import AIMode from './pages/AIMode';
import ForecastPage from './pages/ForecastPage';

type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

function AppContent() {
  const { activeTab, setActiveTab } = useNavigation();
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');
  const [theme, setTheme] = useState<'dark' | 'light'>('light');

  // Sync State
  const [job, setJob] = useState<JobResponse | null>(null);
  const [polling, setPolling] = useState(false);
  const [lastDbSync, setLastDbSync] = useState<number>(Date.now());
  const [showSyncStatus, setShowSyncStatus] = useState(false);
  const [popup, setPopup] = useState<PopupMessage | null>(null);

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
          if (res.data.status === 'completed' || res.data.status === 'failed') {
            setPolling(false);
            setJob(res.data);
            if (res.data.status === 'completed') {
              setLastDbSync(Date.now());
              setShowSyncStatus(true);
            } else if (res.data.status === 'failed') {
              setPopup({ type: 'error', message: `Sync Failed: ${res.data.message}` });
            }
          } else {
            setJob(res.data);
          }
        } catch (err) {
          console.error("Polling error", err);
          setPolling(false);
        }
      }, 500);
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

      // Handle fast-completing syncs with an immediate check
      setTimeout(async () => {
        try {
          const statusRes = await endpoints.sync.status(res.data.job_id);
          if (statusRes.data.status === 'completed' || statusRes.data.status === 'failed') {
            setPolling(false);
            setJob(statusRes.data);
            if (statusRes.data.status === 'completed') {
              setLastDbSync(Date.now());
              setShowSyncStatus(true);
            }
          }
        } catch (e) {
          // Ignore - polling will handle it
        }
      }, 300);
    } catch (err) {
      console.error(err);
      setPopup({ type: 'error', message: "Failed to start sync" });
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
        setPopup({ type: 'info', message: "No data found. Auto-sync started." });
      }
    } catch (e) {
      console.error("Auto-sync check failed", e);
    }
  };

  // Auto-hide sync status after 30 seconds
  useEffect(() => {
    if (showSyncStatus) {
      const timer = setTimeout(() => setShowSyncStatus(false), 30000);
      return () => clearTimeout(timer);
    }
  }, [showSyncStatus]);

  return (
    <div className="app-container">
      <ErrorPopup popup={popup} onClose={() => setPopup(null)} />
      <aside className="sidebar">
        <img
          src="logo.png"
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
          <button className={activeTab === 'today' ? 'active' : ''} onClick={() => setActiveTab('today')}>Today</button>
          <button className={activeTab === 'forecast' ? 'active' : ''} onClick={() => setActiveTab('forecast')}>Forecast</button>
          <button className={activeTab === 'chart' ? 'active' : ''} onClick={() => setActiveTab('chart')}>Chart</button>
          <button className={activeTab === 'menu' ? 'active' : ''} onClick={() => setActiveTab('menu')}>Menu</button>
          <button className={activeTab === 'orders' ? 'active' : ''} onClick={() => setActiveTab('orders')}>Orders</button>
          <button className={activeTab === 'inventory' ? 'active' : ''} onClick={() => setActiveTab('inventory')}>Inventory & COGS</button>
          <button className={activeTab === 'sql' ? 'active' : ''} onClick={() => setActiveTab('sql')}>SQL Console</button>

          <button
            className={`ai-button-base ${activeTab === 'ai_mode' ? 'ai-button-active' : 'ai-button-unselected-wavy'}`}
            onClick={() => setActiveTab('ai_mode')}
          >
            AI Mode
          </button>
          <button className={activeTab === 'configuration' ? 'active' : ''} onClick={() => setActiveTab('configuration')}>Configuration</button>

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
                {/* Display sync result message based on stats */}
                {!job.stats
                  ? 'Sync complete'
                  : (job.stats.fetched === 0 || job.stats.count === 0)
                    ? 'No new orders to sync'
                    : `Synced ${job.stats.orders || job.stats.fetched || 0} new orders`
                }
              </div>
            )}

            {/* Sync Error Label */}
            {job && job.status === 'failed' && !polling && (
              <div style={{
                fontSize: '11px',
                color: '#EF4444',
                textAlign: 'center',
                marginTop: '4px',
                fontWeight: 'bold'
              }}>
                Sync Failed!
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
        {activeTab === 'today' && <TodayPage lastDbSync={lastDbSync} />}
        {activeTab === 'forecast' && <ForecastPage lastDbSync={lastDbSync} />}
        {activeTab === 'chart' && <ChartPage lastDbSync={lastDbSync} />}
        {activeTab === 'menu' && <Menu lastDbSync={lastDbSync} />}
        {activeTab === 'orders' && <Orders lastDbSync={lastDbSync} />}
        {activeTab === 'inventory' && <ComingSoon title="Inventory & COGS" />}
        {activeTab === 'sql' && <SQLConsole />}
        {activeTab === 'ai_mode' && <AIMode />}
        {activeTab === 'configuration' && <Configuration />}
      </main>
    </div >
  );
}

function App() {
  const [activeTab, setActiveTab] = useState('insights');

  return (
    <NavigationProvider activeTab={activeTab} setActiveTab={setActiveTab}>
      <AppContent />
    </NavigationProvider>
  );
}

export default App;
