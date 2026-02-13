import { useState, useEffect } from 'react';
import { Card } from '../components/Card';
import { ErrorPopup } from '../components';
import type { PopupMessage } from '../components';
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

type Tab = 'users' | 'ai_models' | 'integrations' | 'repository' | 'databases' | 'updates';
type UpdateStatus = 'checking' | 'available' | 'up-to-date' | 'downloading' | 'ready' | 'error' | null;

export default function Configuration() {
    const [activeTab, setActiveTab] = useState<Tab>('ai_models');
    const [settings, setSettings] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [selectedDbSection, setSelectedDbSection] = useState<string | null>(null);

    // Users Tab State
    const [userForm, setUserForm] = useState({ name: '', employee_id: '' });
    const [savingUser, setSavingUser] = useState(false);
    const [userLoadError, setUserLoadError] = useState<string | null>(null);
    const [popup, setPopup] = useState<PopupMessage | null>(null);

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

    useEffect(() => {
        if (activeTab === 'users') loadUsers();
    }, [activeTab]);

    const loadUsers = async () => {
        setUserLoadError(null);
        try {
            const res = await endpoints.config.getUsers();
            if (res.data && res.data.length > 0) {
                const user = res.data[0];
                setUserForm({
                    name: user.name,
                    employee_id: user.employee_id
                });
            } else {
                setUserLoadError("No user profile found. Save to create one.");
            }
        } catch (e) {
            console.error("Failed to load user profile", e);
            setUserLoadError("Failed to load user profile. Please try again.");
        }
    };

    const handleSaveUser = async () => {
        if (!userForm.name || !userForm.employee_id) {
            setPopup({ type: 'error', message: "Name and Employee ID are required" });
            return;
        }
        setSavingUser(true);
        try {
            await endpoints.config.saveUser(userForm);
            setPopup({ type: 'success', message: "User saved successfully!" });
            setUserLoadError(null);
            loadUsers();
        } catch (e: any) {
            const errorMsg = e.response?.data?.detail || "Failed to save user";
            setPopup({ type: 'error', message: errorMsg });
        } finally {
            setSavingUser(false);
        }
    };


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
            setPopup({ type: 'success', message: "Settings saved successfully!" });
        } catch (e) {
            setPopup({ type: 'error', message: "Failed to save settings." });
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
            setPopup({ type: 'success', message: res.data.message });
        } catch (e: any) {
            const errorMsg = e.response?.data?.detail || "Connection Failed";
            setPopup({ type: 'error', message: errorMsg });
        }
    };

    const handleResetSection = async (section: string) => {
        if (!window.confirm(`Are you sure you want to reset the ${section} database?`)) return;
        if (!window.confirm(`Confirm: This will clear all local data for ${section}. This cannot be undone.`)) return;

        try {
            const res = await endpoints.config.resetDb(section.toLowerCase());
            setPopup({ type: 'success', message: res.data.message });
            setSelectedDbSection(null);
        } catch (e: any) {
            const errorMsg = e.response?.data?.detail || "Reset Failed";
            setPopup({ type: 'error', message: errorMsg });
        }
    };


    const handlePetpoojaBackfill = async () => {
        const apiKey = settings['integration_orders_key'];
        if (!apiKey) {
            setPopup({ type: 'error', message: "Orders API Key is missing in Integrations settings." });
            return;
        }

        if (!window.confirm("Are you sure you want to trigger Petpooja Sync?")) return;

        try {
            await endpoints.petpooja.backfill(apiKey);
            setPopup({ type: 'success', message: "Sync triggered successfully" });
        } catch (e: any) {
            setPopup({ type: 'error', message: "Sync Failed" });
        }
    };


    const handleResetIntegrations = async () => {
        if (!window.confirm("Are you sure you want to reset all integration settings?")) return;
        if (!window.confirm("This will clear all configured URLs and API keys for external services.")) return;

        try {
            const res = await endpoints.config.resetDb("integrations");
            setPopup({ type: 'success', message: res.data.message });
            // Reload settings to reflect changes
            loadSettings();
        } catch (e: any) {
            const errorMsg = e.response?.data?.detail || "Reset Failed";
            setPopup({ type: 'error', message: errorMsg });
        }
    };


    const handleResetAIModels = async () => {
        if (!window.confirm("Are you sure you want to reset all AI Model settings?")) return;
        if (!window.confirm("This will clear all configured API keys and model selections.")) return;

        try {
            const res = await endpoints.config.resetDb("ai_models");
            setPopup({ type: 'success', message: res.data.message });
            // Reload settings to reflect changes
            loadSettings();
        } catch (e: any) {
            const errorMsg = e.response?.data?.detail || "Reset Failed";
            setPopup({ type: 'error', message: errorMsg });
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
            <ErrorPopup popup={popup} onClose={() => setPopup(null)} />

            {/* Segmented Control */}
            <div style={{ display: 'flex', gap: '5px', marginBottom: '30px', background: 'var(--card-bg)', padding: '5px', borderRadius: '30px', border: '1px solid var(--border-color)', width: 'fit-content' }}>
                {[
                    { id: 'users', label: '👤 User Profile' },
                    { id: 'ai_models', label: '🧠 AI Models' },
                    { id: 'integrations', label: '🔌 Integrations' },
                    { id: 'repository', label: '📦 Repository' },
                    {
                        id: 'databases',
                        label: (
                            <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <ellipse cx="11" cy="5" rx="8" ry="3"></ellipse>
                                    <path d="M3 5v14c0 1.66 3.58 3 8 3 .48 0 .95-.02 1.4-.05"></path>
                                    <path d="M3 10c0 1.66 3.58 3 8 3 .48 0 .95-.02 1.4-.05"></path>
                                    <path d="M3 15c0 1.66 3.58 3 8 3"></path>
                                    <path d="M19 5v7"></path>
                                    <circle cx="18" cy="18" r="3"></circle>
                                    <path d="M18 15v1"></path>
                                    <path d="M18 20v1"></path>
                                    <path d="M21 18h-1"></path>
                                    <path d="M15 18h-1"></path>
                                    <path d="m16 16 .7.7"></path>
                                    <path d="m19.3 19.3.7.7"></path>
                                    <path d="m19.3 16.7.7-.7"></path>
                                    <path d="m16 20 .7-.7"></path>
                                </svg>
                                Databases
                            </span>
                        )
                    },
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
                    {activeTab === 'users' && (
                        <Card title="User Profile">
                            <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>
                                Configure the current user identity. This information is used for cloud data synchronization.
                            </p>
                            {userLoadError && (
                                <p style={{ color: 'var(--error-color, #c53030)', marginBottom: '12px', fontSize: '0.9em' }}>
                                    {userLoadError}
                                </p>
                            )}
                            <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', marginBottom: '20px' }} />

                            {/* Profile Form */}
                            <div style={{ maxWidth: '600px' }}>
                                <div style={{ marginBottom: '20px' }}>
                                    <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Full Name</label>
                                    <input
                                        type="text"
                                        value={userForm.name}
                                        onChange={e => setUserForm(prev => ({ ...prev, name: e.target.value }))}
                                        placeholder="e.g. John Doe"
                                        style={{
                                            width: '100%',
                                            padding: '12px',
                                            borderRadius: '8px',
                                            border: '1px solid var(--border-color)',
                                            background: 'var(--input-bg)',
                                            color: 'var(--text-color)',
                                            fontSize: '1em'
                                        }}
                                    />
                                </div>
                                <div style={{ marginBottom: '30px' }}>
                                    <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Employee ID</label>
                                    <input
                                        type="text"
                                        value={userForm.employee_id}
                                        onChange={e => setUserForm(prev => ({ ...prev, employee_id: e.target.value }))}
                                        placeholder="e.g. EMP-001"
                                        style={{
                                            width: '100%',
                                            padding: '12px',
                                            borderRadius: '8px',
                                            border: '1px solid var(--border-color)',
                                            background: 'var(--input-bg)',
                                            color: 'var(--text-color)',
                                            fontSize: '1em',
                                            fontFamily: 'monospace'
                                        }}
                                    />
                                </div>

                                <button
                                    onClick={handleSaveUser}
                                    disabled={savingUser}
                                    style={{
                                        padding: '12px 24px',
                                        background: savingUser ? 'var(--text-secondary)' : 'var(--accent-color)',
                                        color: 'white',
                                        border: 'none',
                                        borderRadius: '8px',
                                        cursor: savingUser ? 'not-allowed' : 'pointer',
                                        fontWeight: 600,
                                        fontSize: '1em',
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: '8px'
                                    }}
                                >
                                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
                                        <polyline points="17 21 17 13 7 13 7 21"></polyline>
                                        <polyline points="7 3 7 8 15 8"></polyline>
                                    </svg>
                                    {savingUser ? 'Saving...' : 'Save Profile'}
                                </button>
                            </div>
                        </Card>
                    )}

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

                            <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <button
                                    onClick={handleResetAIModels}
                                    style={{
                                        padding: '10px 20px',
                                        background: 'rgba(239, 68, 68, 0.1)',
                                        color: '#ef4444',
                                        border: '1px solid #ef4444',
                                        borderRadius: '8px',
                                        cursor: 'pointer',
                                        fontSize: '1em',
                                        fontWeight: 600,
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: '6px'
                                    }}
                                >
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M3 6h18"></path>
                                        <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path>
                                        <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path>
                                    </svg>
                                    Reset
                                </button>
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
                        </Card>
                    )}

                    {activeTab === 'integrations' && (
                        <Card title="External Integrations">
                            <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>Connect external services for data syncing.</p>
                            <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', marginBottom: '20px' }} />

                            <h4 style={{ marginBottom: '10px', color: 'var(--accent-color)' }}>Cloud Synchronization</h4>
                            <p style={{ fontSize: '0.9em', color: 'var(--text-secondary)', marginBottom: '15px' }}>
                                Automatically sync Errors, Learning Logs, and Conversations to your cloud server every 5 minutes.
                            </p>
                            {renderInput("Cloud Server URL", "cloud_sync_url", "url", "https://api.example.com")}
                            {renderInput("Cloud API Key", "cloud_sync_api_key", "password")}
                            {renderTestButton('cloud_sync')}
                            <div style={{ marginBottom: '20px' }}></div>
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

                            <div style={{ marginTop: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                <button
                                    onClick={handleResetIntegrations}
                                    style={{
                                        padding: '10px 20px',
                                        background: 'rgba(239, 68, 68, 0.1)',
                                        color: '#ef4444',
                                        border: '1px solid #ef4444',
                                        borderRadius: '8px',
                                        cursor: 'pointer',
                                        fontSize: '1em',
                                        fontWeight: 600,
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: '6px'
                                    }}
                                >
                                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                        <path d="M3 6h18"></path>
                                        <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path>
                                        <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path>
                                    </svg>
                                    Reset
                                </button>
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

                    {activeTab === 'databases' && (
                        <Card title="Database Connections">
                            <p style={{ color: 'var(--text-secondary)', marginBottom: '20px' }}>Configure database connections and settings.</p>
                            <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', marginBottom: '20px' }} />

                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '20px', marginBottom: '40px' }}>
                                {[
                                    { name: 'Orders', status: 'Active' },
                                    { name: 'Inventory', status: 'Active' },
                                    { name: 'COGS', status: 'Active' },
                                    { name: 'Warehouse', status: 'Active' },
                                    { name: 'AI Mode', status: 'Active' },
                                    { name: 'Forecast', status: 'Active' }
                                ].map(section => (
                                    <div
                                        key={section.name}
                                        onClick={() => setSelectedDbSection(section.name)}
                                        style={{
                                            background: 'var(--input-bg)',
                                            border: '1px solid var(--border-color)',
                                            borderRadius: '12px',
                                            padding: '20px',
                                            display: 'flex',
                                            flexDirection: 'column',
                                            gap: '10px',
                                            cursor: 'pointer',
                                            transition: 'transform 0.2s ease, border-color 0.2s ease',
                                        }}
                                        onMouseEnter={(e) => {
                                            e.currentTarget.style.transform = 'translateY(-2px)';
                                            e.currentTarget.style.borderColor = 'var(--accent-color)';
                                        }}
                                        onMouseLeave={(e) => {
                                            e.currentTarget.style.transform = 'translateY(0)';
                                            e.currentTarget.style.borderColor = 'var(--border-color)';
                                        }}
                                    >
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                            <h4 style={{ margin: 0 }}>{section.name}</h4>
                                            <span style={{
                                                fontSize: '0.75rem',
                                                background: '#10B981',
                                                color: 'white',
                                                padding: '2px 8px',
                                                borderRadius: '10px',
                                                fontWeight: 600
                                            }}>
                                                {section.status}
                                            </span>
                                        </div>
                                        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', margin: 0 }}>
                                            Local database storage for {section.name} modules.
                                        </p>
                                    </div>
                                ))}
                            </div>

                            <hr style={{ border: 0, borderTop: '1px solid var(--border-color)', marginBottom: '20px' }} />

                            <div style={{ padding: '20px', background: 'rgba(239, 68, 68, 0.05)', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
                                <h4 style={{ color: '#ef4444', marginTop: 0 }}>Caution</h4>
                                <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '15px' }}>
                                    Resetting the local database will clear all locally stored data, configurations, and chat logs. This action cannot be undone.
                                </p>
                                <button
                                    onClick={() => {
                                        if (window.confirm("Are you sure you want to delete the local database?")) {
                                            if (window.confirm("Are you sure you would like to clear the DB from this app, locally. (Under construction)")) {
                                                endpoints.resetAll().then(res => {
                                                    setPopup({ type: 'success', message: res.data.message });
                                                    setTimeout(() => window.location.reload(), 1500);
                                                }).catch(err => {
                                                    setPopup({ type: 'error', message: `Reset Failed: ${err.message}` });
                                                });
                                            }
                                        }
                                    }}
                                    style={{
                                        padding: '10px 20px',
                                        background: 'rgba(239, 68, 68, 0.85)',
                                        color: 'white',
                                        border: 'none',
                                        borderRadius: '8px',
                                        cursor: 'pointer',
                                        fontWeight: 600,
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: '8px'
                                    }}
                                >
                                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none">
                                        <path d="M12 2.5l10 18H2l10-18z" fill="#facc15" stroke="black" strokeWidth="1.5" strokeLinejoin="round" />
                                        <circle cx="12" cy="14.5" r="1.5" fill="black" />
                                        <path d="M12 14.5l-2.5-4.3a5 5 0 0 1 5 0L12 14.5z" fill="black" />
                                        <path d="M12 14.5l-5 0a5 5 0 0 0 2.5 4.3L12 14.5z" fill="black" />
                                        <path d="M12 14.5l2.5 4.3a5 5 0 0 0 2.5-4.3L12 14.5z" fill="black" />
                                    </svg>
                                    Delete ALL
                                </button>
                            </div>
                        </Card>
                    )}

                    {/* Database Section Reset Modal */}
                    {selectedDbSection && (
                        <div style={{
                            position: 'fixed',
                            top: 0,
                            left: 0,
                            right: 0,
                            bottom: 0,
                            background: 'rgba(0,0,0,0.7)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            zIndex: 2000,
                            backdropFilter: 'blur(4px)'
                        }} onClick={() => setSelectedDbSection(null)}>
                            <div style={{
                                background: 'var(--card-bg)',
                                width: '400px',
                                padding: '30px',
                                borderRadius: '16px',
                                border: '1px solid var(--border-color)',
                                boxShadow: '0 20px 50px rgba(0,0,0,0.4)',
                                position: 'relative'
                            }} onClick={e => e.stopPropagation()}>
                                <h3 style={{ marginTop: 0, marginBottom: '10px' }}>{selectedDbSection} Database</h3>
                                <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '25px' }}>
                                    Manage local storage and settings for the {selectedDbSection} module.
                                </p>

                                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                                    {selectedDbSection === 'Orders' && (
                                        <button
                                            onClick={handlePetpoojaBackfill}
                                            style={{
                                                padding: '12px',
                                                background: 'rgba(128, 128, 128, 0.08)',
                                                color: 'var(--text-color)',
                                                border: '1px solid var(--text-color)',
                                                borderRadius: '8px',
                                                cursor: 'pointer',
                                                fontWeight: 600
                                            }}
                                        >
                                            Petpooja Sync
                                        </button>
                                    )}
                                    {selectedDbSection === 'Forecast' && (
                                        <>
                                            <button
                                                onClick={async () => {
                                                    if (!window.confirm("⚠️ Are you sure you want to HARD RESET Item Demand Forecasts?")) return;
                                                    if (!window.confirm("This will delete all trained models and history. Charts will be empty — use Pull from Cloud or Full Retrain to populate.")) return;
                                                    try {
                                                        const res = await endpoints.config.resetDb("item_demand");
                                                        setPopup({ type: 'success', message: res.data.message });
                                                    } catch (e: any) {
                                                        const errorMsg = e.response?.data?.detail || "Reset Failed";
                                                        setPopup({ type: 'error', message: errorMsg });
                                                    }
                                                }}
                                                style={{
                                                    padding: '12px',
                                                    background: 'rgba(239, 68, 68, 0.1)',
                                                    color: '#ef4444',
                                                    border: '1px solid rgba(239, 68, 68, 0.2)',
                                                    borderRadius: '8px',
                                                    cursor: 'pointer',
                                                    fontWeight: 600
                                                }}
                                            >
                                                📉 Reset Item Demand Forecast
                                            </button>
                                            <button
                                                onClick={async () => {
                                                    if (!window.confirm("⚠️ Are you sure you want to HARD RESET Sales Forecasts?")) return;
                                                    if (!window.confirm("This will delete the GP model and clear all forecast caches. Charts will be empty — use Pull from Cloud or Full Retrain to populate.")) return;
                                                    try {
                                                        const res = await endpoints.config.resetDb("sales_forecast");
                                                        setPopup({ type: 'success', message: res.data.message });
                                                    } catch (e: any) {
                                                        const errorMsg = e.response?.data?.detail || "Reset Failed";
                                                        setPopup({ type: 'error', message: errorMsg });
                                                    }
                                                }}
                                                style={{
                                                    padding: '12px',
                                                    background: 'rgba(239, 68, 68, 0.1)',
                                                    color: '#ef4444',
                                                    border: '1px solid rgba(239, 68, 68, 0.2)',
                                                    borderRadius: '8px',
                                                    cursor: 'pointer',
                                                    fontWeight: 600
                                                }}
                                            >
                                                📊 Reset Sales Forecast
                                            </button>
                                            <button
                                                onClick={async () => {
                                                    if (!window.confirm("⚠️ Are you sure you want to HARD RESET Volume Forecasts?")) return;
                                                    if (!window.confirm("This will delete all volume models and cache. Use Pull from Cloud or Full Retrain to populate.")) return;
                                                    try {
                                                        const res = await endpoints.config.resetDb("volume_forecast");
                                                        setPopup({ type: 'success', message: res.data.message });
                                                    } catch (e: any) {
                                                        const errorMsg = e.response?.data?.detail || "Reset Failed";
                                                        setPopup({ type: 'error', message: errorMsg });
                                                    }
                                                }}
                                                style={{
                                                    padding: '12px',
                                                    background: 'rgba(239, 68, 68, 0.1)',
                                                    color: '#ef4444',
                                                    border: '1px solid rgba(239, 68, 68, 0.2)',
                                                    borderRadius: '8px',
                                                    cursor: 'pointer',
                                                    fontWeight: 600
                                                }}
                                            >
                                                📦 Reset Volume Forecast
                                            </button>
                                        </>
                                    )}
                                    {selectedDbSection !== 'Forecast' && (
                                        <button
                                            onClick={() => handleResetSection(selectedDbSection)}
                                            style={{
                                                padding: '12px',
                                                background: 'rgba(239, 68, 68, 0.1)',
                                                color: '#ef4444',
                                                border: '1px solid rgba(239, 68, 68, 0.2)',
                                                borderRadius: '8px',
                                                cursor: 'pointer',
                                                fontWeight: 600,
                                                transition: 'all 0.2s ease'
                                            }}
                                            onMouseEnter={e => {
                                                e.currentTarget.style.background = 'rgba(239, 68, 68, 0.2)';
                                            }}
                                            onMouseLeave={e => {
                                                e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)';
                                            }}
                                        >
                                            🗑️ Reset {selectedDbSection}
                                        </button>
                                    )}

                                    <button
                                        onClick={() => setSelectedDbSection(null)}
                                        style={{
                                            padding: '12px',
                                            background: 'var(--bg-color)',
                                            color: 'var(--text-color)',
                                            border: '1px solid var(--border-color)',
                                            borderRadius: '8px',
                                            cursor: 'pointer',
                                            fontWeight: 500
                                        }}
                                    >
                                        Close
                                    </button>
                                </div>
                            </div>
                        </div>
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
