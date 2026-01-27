import { useState, useEffect } from 'react';
import { Card } from '../components/Card';
import { endpoints } from '../api';

// Type declaration for Electron IPC
declare global {
    interface Window {
        electron?: {
            checkForUpdate: () => void;
            downloadUpdate: () => void;
            quitAndInstall: () => void;
            onUpdateStatus: (callback: (data: any) => void) => void;
            getAppVersion: () => Promise<string>;
        };
    }
}

type Tab = 'ai_models' | 'integrations' | 'repository' | 'updates';
type UpdateStatus = 'checking' | 'available' | 'up-to-date' | 'downloading' | 'ready' | 'error' | null;

export default function Configuration() {
    const [activeTab, setActiveTab] = useState<Tab>('ai_models');
    const [settings, setSettings] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);

    // Updates Tab State
    const [updateStatus, setUpdateStatus] = useState<UpdateStatus>(null);
    const [updateVersion, setUpdateVersion] = useState<string | null>(null);
    const [downloadProgress, setDownloadProgress] = useState<number | null>(null);
    const [currentVersion, setCurrentVersion] = useState<string>('0.0.0');

    useEffect(() => {
        loadSettings();

        // Get current app version
        if (window.electron) {
            window.electron.getAppVersion().then((ver: string) => setCurrentVersion(ver));
        }

        // Subscribe to Electron update events
        if (window.electron) {
            window.electron.onUpdateStatus((data) => {
                setUpdateStatus(data.status);
                if (data.version) setUpdateVersion(data.version);
                if (data.progress) setDownloadProgress(data.progress);
            });
        }
    }, []);

    const loadSettings = async () => {
        setLoading(true);
        try {
            const res = await endpoints.config.getAll();
            setSettings(res.data);
        } catch (e) {
            console.error("Failed to load settings", e);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await endpoints.config.update(settings);
            alert("Settings saved successfully!");
        } catch (e) {
            alert("Failed to save settings.");
            console.error(e);
        } finally {
            setSaving(false);
        }
    };

    const handleChange = (key: string, value: string) => {
        setSettings(prev => ({ ...prev, [key]: value }));
    };

    const checkForUpdates = () => {
        setUpdateStatus('checking');
        if (window.electron) {
            window.electron.checkForUpdate();
        } else {
            // Fallback for dev mode (no Electron)
            setTimeout(() => setUpdateStatus('up-to-date'), 1500);
        }
    };

    const handleDownload = () => {
        window.electron?.downloadUpdate();
    };

    const handleInstall = () => {
        window.electron?.quitAndInstall();
    };

    const handleTestConnection = async (type: string) => {
        try {
            const res = await endpoints.config.verify(type, settings);
            alert(res.data.message);
        } catch (e: any) {
            const errorMsg = e.response?.data?.detail || "Connection Failed";
            alert(`❌ ${errorMsg}`);
        }
    };

    const renderTestButton = (type: string) => (
        <button
            onClick={() => handleTestConnection(type)}
            style={{
                padding: '5px 15px',
                background: 'transparent',
                border: '1px solid var(--accent-color)',
                color: 'var(--accent-color)',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '0.9em',
                marginBottom: '15px'
            }}
        >
            Test Connection
        </button>
    );

    const renderInput = (label: string, key: string, type: string = "text", placeholder: string = "") => (
        <div style={{ marginBottom: '15px' }}>
            <label style={{ display: 'block', marginBottom: '5px', fontWeight: 500, color: 'var(--text-color)' }}>{label}</label>
            <input
                type={type}
                value={settings[key] || ''}
                onChange={e => handleChange(key, e.target.value)}
                placeholder={placeholder}
                style={{
                    width: '100%',
                    padding: '10px',
                    borderRadius: '6px',
                    border: '1px solid var(--border-color)',
                    background: 'var(--input-bg)',
                    color: 'var(--text-color)',
                    fontSize: '1em'
                }}
            />
        </div>
    );

    const renderSaveButton = () => (
        <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'flex-end' }}>
            <button
                onClick={handleSave}
                disabled={saving}
                style={{
                    padding: '10px 20px',
                    background: 'var(--accent-color)',
                    color: 'white',
                    border: 'none',
                    borderRadius: '8px',
                    cursor: saving ? 'wait' : 'pointer',
                    fontSize: '1em',
                    fontWeight: 600
                }}
            >
                {saving ? "Saving..." : "Save Changes"}
            </button>
        </div>
    );

    return (
        <div className="page-container" style={{ padding: '20px', fontFamily: 'Inter, sans-serif', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>


            {/* Segmented Control */}
            <div style={{ display: 'flex', gap: '5px', marginBottom: '30px', background: 'var(--card-bg)', padding: '5px', borderRadius: '30px', border: '1px solid var(--border-color)', width: 'fit-content' }}>
                {[
                    { id: 'ai_models', label: '🧠 AI Models' },
                    { id: 'integrations', label: '🔌 Integrations' },
                    { id: 'repository', label: '📦 Repository' },
                    {
                        id: 'updates',
                        label: (
                            <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242" />
                                    <path d="M12 12v9" />
                                    <path d="m8 17 4 4 4-4" />
                                </svg>
                                Software Update
                            </span>
                        )
                    }
                ].map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id as Tab)}
                        style={{
                            padding: '10px 20px',
                            background: activeTab === tab.id ? 'var(--accent-color)' : 'transparent',
                            border: 'none',
                            color: activeTab === tab.id ? 'white' : 'var(--text-color)',
                            cursor: 'pointer',
                            borderRadius: '25px',
                            transition: 'all 0.2s ease',
                            fontWeight: activeTab === tab.id ? 600 : 500,
                            display: 'flex',
                            alignItems: 'center',
                        }}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Content Area */}
            {loading ? <div>Loading settings...</div> : (
                <div style={{ maxWidth: '1200px', width: '100%' }}>
                    {activeTab === 'ai_models' && (
                        <Card title="AI Model Configuration">
                            <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>Configure API keys and models for AI services.</p>
                            <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', marginBottom: '20px' }} />

                            <h4 style={{ color: 'var(--accent-color)', marginBottom: '10px' }}>OpenAI (GPT)</h4>
                            {renderInput("API Key", "openai_api_key", "password", "sk-...")}
                            {renderInput("Model Name", "openai_model", "text", "gpt-4-turbo")}
                            {renderTestButton('openai')}

                            <h4 style={{ color: 'var(--accent-color)', marginBottom: '10px', marginTop: '20px' }}>Anthropic (Claude)</h4>
                            {renderInput("API Key", "anthropic_api_key", "password", "sk-ant-...")}
                            {renderInput("Model Name", "anthropic_model", "text", "claude-3-opus-20240229")}

                            <h4 style={{ color: 'var(--accent-color)', marginBottom: '10px', marginTop: '20px' }}>Google (Gemini)</h4>
                            {renderInput("API Key", "gemini_api_key", "password", "AIza...")}
                            {renderInput("Model Name", "gemini_model", "text", "gemini-1.5-pro")}

                            {renderSaveButton()}
                        </Card>
                    )}

                    {activeTab === 'integrations' && (
                        <Card title="External Integrations">
                            <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>Connect external services for data syncing.</p>
                            <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', marginBottom: '20px' }} />

                            <h4 style={{ marginBottom: '10px' }}>Orders Service</h4>
                            {renderInput("Orders URL", "integration_orders_url", "url", "https://api.example.com/orders")}
                            {renderInput("Orders API Key", "integration_orders_key", "password")}
                            {renderTestButton('orders')}

                            <h4 style={{ marginBottom: '10px', marginTop: '20px' }}>Inventory Service</h4>
                            {renderInput("Inventory URL", "integration_inventory_url", "url")}
                            {renderInput("Inventory API Key", "integration_inventory_key", "password")}

                            <h4 style={{ marginBottom: '10px', marginTop: '20px' }}>COGS Service</h4>
                            {renderInput("COGS URL", "integration_cogs_url", "url")}
                            {renderInput("COGS API Key", "integration_cogs_key", "password")}

                            <h4 style={{ marginBottom: '10px', marginTop: '20px' }}>Warehouse Management</h4>
                            {renderInput("Warehouse URL", "integration_warehouse_url", "url")}
                            {renderInput("Warehouse API Key", "integration_warehouse_key", "password")}

                            {renderSaveButton()}
                        </Card>
                    )}

                    {activeTab === 'repository' && (
                        <Card title="Git Repository Setup">
                            <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>Configure upstream repository for updates and version control.</p>
                            <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', marginBottom: '20px' }} />

                            {renderInput("Git Remote URL", "git_remote_url", "url", "https://github.com/user/repo.git")}
                            {renderInput("Git Personal Access Token (PAT)", "git_pat", "password", "ghp_...")}

                            {renderSaveButton()}
                        </Card>
                    )}

                    {activeTab === 'updates' && (
                        <Card title="System Updates">
                            <div style={{ textAlign: 'center', padding: '20px' }}>
                                <div style={{ fontSize: '1.2em', marginBottom: '10px' }}>
                                    Current Version: <b>{currentVersion}</b>
                                    {updateVersion && updateStatus === 'available' && (
                                        <span style={{ color: 'orange', marginLeft: '10px' }}>→ {updateVersion}</span>
                                    )}
                                </div>
                                <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', margin: '20px 0' }} />

                                {updateStatus === null && (
                                    <p style={{ color: 'var(--text-secondary)' }}>Check for the latest updates from GitHub Releases.</p>
                                )}
                                {updateStatus === 'checking' && <p>Checking for updates...</p>}
                                {updateStatus === 'up-to-date' && <p style={{ color: 'green', fontWeight: 'bold' }}>✅ You are on the latest version.</p>}
                                {updateStatus === 'available' && <p style={{ color: 'orange', fontWeight: 'bold' }}>⬇️ New update available!</p>}
                                {updateStatus === 'downloading' && (
                                    <div>
                                        <p>Downloading update...</p>
                                        {downloadProgress !== null && (
                                            <div style={{ width: '100%', backgroundColor: '#444', height: '8px', borderRadius: '4px', overflow: 'hidden', marginTop: '10px' }}>
                                                <div style={{ width: `${downloadProgress}%`, backgroundColor: '#10B981', height: '100%', transition: 'width 0.3s' }} />
                                            </div>
                                        )}
                                    </div>
                                )}
                                {updateStatus === 'ready' && <p style={{ color: 'green', fontWeight: 'bold' }}>✅ Update downloaded! Restart to apply.</p>}
                                {updateStatus === 'error' && (
                                    <div>
                                        <p style={{ color: 'red', marginBottom: '10px' }}>❌ Auto-update failed.</p>
                                        <p style={{ color: 'var(--text-secondary)', fontSize: '0.9em' }}>You can download the update manually below.</p>
                                    </div>
                                )}

                                <div style={{ marginTop: '20px', display: 'flex', gap: '10px', justifyContent: 'center', flexWrap: 'wrap' }}>
                                    {(updateStatus === null || updateStatus === 'up-to-date' || updateStatus === 'error') && (
                                        <button
                                            onClick={checkForUpdates}
                                            style={{
                                                padding: '10px 20px',
                                                background: 'var(--card-bg)',
                                                border: '1px solid var(--border-color)',
                                                borderRadius: '8px',
                                                cursor: 'pointer'
                                            }}
                                        >
                                            Check for Updates
                                        </button>
                                    )}

                                    {updateStatus === 'available' && (
                                        <button
                                            onClick={handleDownload}
                                            style={{
                                                padding: '10px 20px',
                                                background: 'var(--accent-color)',
                                                color: 'white',
                                                border: 'none',
                                                borderRadius: '8px',
                                                cursor: 'pointer'
                                            }}
                                        >
                                            Auto-Install Update
                                        </button>
                                    )}

                                    {updateStatus === 'ready' && (
                                        <button
                                            onClick={handleInstall}
                                            style={{
                                                padding: '10px 20px',
                                                background: '#10B981',
                                                color: 'white',
                                                border: 'none',
                                                borderRadius: '8px',
                                                cursor: 'pointer'
                                            }}
                                        >
                                            Restart & Install
                                        </button>
                                    )}

                                    {(updateStatus === 'available' || updateStatus === 'error') && (
                                        <a
                                            href="https://github.com/kshitij1489/dn_analytics_releases/releases/latest"
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            style={{
                                                padding: '10px 20px',
                                                background: 'transparent',
                                                color: 'var(--accent-color)',
                                                border: '1px solid var(--accent-color)',
                                                borderRadius: '8px',
                                                cursor: 'pointer',
                                                textDecoration: 'none',
                                                display: 'inline-block'
                                            }}
                                        >
                                            Download from GitHub
                                        </a>
                                    )}
                                </div>
                            </div>
                        </Card>
                    )}
                </div>
            )}
        </div>
    );
}
