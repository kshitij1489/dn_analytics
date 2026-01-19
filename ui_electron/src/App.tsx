import { useState, useEffect } from 'react';
import './App.css';
import { endpoints } from './api';

// Components
import Insights from './pages/Insights';
import Operations from './pages/Operations';
import Menu from './pages/Menu';
import Resolutions from './pages/Resolutions';
import SQLConsole from './pages/SQLConsole';

type ConnectionStatus = 'connected' | 'connecting' | 'disconnected';

function App() {
  const [activeTab, setActiveTab] = useState('insights');
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');

  useEffect(() => {
    checkConnection();
    checkDataAndSync();

    // Check connection every 30 seconds
    const interval = setInterval(() => checkConnection(), 30000);
    return () => clearInterval(interval);
  }, []);

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
        await endpoints.sync.run();

        // Redirect to Operations tab so user sees the progress
        setActiveTab('operations');
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
          <button className={activeTab === 'operations' ? 'active' : ''} onClick={() => setActiveTab('operations')}>Operations</button>
          <button className={activeTab === 'resolutions' ? 'active' : ''} onClick={() => setActiveTab('resolutions')}>Resolutions</button>
          <button className={activeTab === 'sql' ? 'active' : ''} onClick={() => setActiveTab('sql')}>SQL Console</button>

          {/* Connection Status */}
          <div style={{
            marginTop: '10px',
            padding: '8px 12px',
            fontSize: '12px',
            backgroundColor: '#2d2d2d',
            borderRadius: '4px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            color: status.color,
            fontWeight: '500'
          }}>
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
        </nav>
      </aside>
      <main className="main-content">
        {activeTab === 'insights' && <Insights />}
        {activeTab === 'menu' && <Menu />}
        {activeTab === 'operations' && <Operations />}
        {activeTab === 'resolutions' && <Resolutions />}
        {activeTab === 'sql' && <SQLConsole />}
      </main>
    </div>
  );
}

export default App;
