const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const axios = require('axios');
const { autoUpdater } = require('electron-updater');

let mainWindow;
let apiProcess;
const API_PORT = 8000;

// Backend log stream
let backendLogStream = null;

function getBackendLogPath() {
    return path.join(app.getPath('userData'), 'backend.log');
}

function getBackendLogStream() {
    if (!backendLogStream) {
        const logPath = getBackendLogPath();
        // Create write stream in append mode
        backendLogStream = fs.createWriteStream(logPath, { flags: 'a' });
        backendLogStream.on('error', (e) => {
            console.error('Backend log stream error:', e);
        });
    }
    return backendLogStream;
}

function appendBackendLog(line, stream = '') {
    const prefix = stream ? `[${stream}] ` : '';
    const msg = `${new Date().toISOString()} ${prefix}${line}\n`;

    // Write to backend log stream (non-blocking). Stream is only created in prod.
    try {
        const s = getBackendLogStream();
        if (s && s.writable) {
            s.write(msg);
        }
    } catch (e) {
        console.error('Failed to write to backend log stream:', e);
    }
}

// =========== Auto Updater Setup ===========
function setupAutoUpdater() {
    // Disable auto-download, let user control
    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.allowPrerelease = true;

    autoUpdater.on('checking-for-update', () => {
        sendUpdateStatus('checking');
    });

    autoUpdater.on('update-available', (info) => {
        sendUpdateStatus('available', info.version);
    });

    autoUpdater.on('update-not-available', () => {
        sendUpdateStatus('up-to-date');
    });

    autoUpdater.on('download-progress', (progress) => {
        sendUpdateStatus('downloading', null, progress.percent);
    });

    autoUpdater.on('update-downloaded', () => {
        sendUpdateStatus('ready');
    });

    autoUpdater.on('error', (err) => {
        sendUpdateStatus('error', err.message);
    });
}

function sendUpdateStatus(status, version = null, progress = null) {
    if (mainWindow) {
        mainWindow.webContents.send('update-status', { status, version, progress });
    }
}

// IPC Handlers for Renderer
ipcMain.on('check-for-update', async () => {
    try {
        // Fetch GitHub token from backend config for private repo auth
        const configRes = await axios.get(`http://127.0.0.1:${API_PORT}/api/config`);
        const gitToken = configRes.data?.git_pat;

        if (gitToken) {
            autoUpdater.requestHeaders = {
                'Authorization': `token ${gitToken}`
            };
            console.log('GitHub token found, using authenticated requests.');
        } else {
            console.log('No GitHub token found, using unauthenticated requests.');
        }

        autoUpdater.checkForUpdates();
    } catch (err) {
        console.error('Failed to fetch config for update check:', err);
        sendUpdateStatus('error', 'Could not connect to backend to fetch credentials.');
    }
});

ipcMain.on('download-update', () => {
    autoUpdater.downloadUpdate();
});

ipcMain.on('quit-and-install', () => {
    autoUpdater.quitAndInstall();
});
// =========================================

ipcMain.handle('get-app-version', () => {
    return app.getVersion();
});

function startPythonParams(dbPath, appDataRoot) {
    const isDev = process.env.NODE_ENV === 'development';

    const env = {
        ...process.env,
        DB_URL: dbPath,
        ANALYTICS_DB_PATH: dbPath,
        ANALYTICS_APP_DATA_ROOT: appDataRoot,
        ANALYTICS_CONTROL_DB_PATH: path.join(appDataRoot, 'analytics-control.db'),
    };
    // Prod cwd is read-only; use userData for error logs.
    if (!isDev) {
        const userDataPath = path.dirname(dbPath);
        env.ERROR_LOG_DIR = path.join(userDataPath, 'logs');
    }

    if (isDev) {
        console.log('Starting Python in DEV mode...');
        // DEV: usage of python/uvicorn directly from the virtual environment
        const pythonPath = path.join(__dirname, '../.venv/bin/python3');
        return spawn(pythonPath, ['-m', 'uvicorn', 'src.api.main:app', '--host', '127.0.0.1', '--port', API_PORT.toString()], {
            cwd: path.join(__dirname, '..'), // Run from root
            env: env,
            stdio: 'inherit'
        });
    } else {
        console.log('Starting Python in PROD mode...');
        const executablePath = path.join(process.resourcesPath, 'analytics-backend', 'analytics-backend');
        console.log(`Executable path: ${executablePath}`);
        appendBackendLog(`Spawning: ${executablePath} --host 127.0.0.1 --port ${API_PORT}`);

        const child = spawn(executablePath, ['--host', '127.0.0.1', '--port', API_PORT.toString()], {
            env: env,
        });

        child.stdout.on('data', (data) => appendBackendLog(data.toString().trim(), 'stdout'));
        child.stderr.on('data', (data) => appendBackendLog(data.toString().trim(), 'stderr'));
        child.on('error', (err) => appendBackendLog(`Spawn error: ${err.message}`, 'error'));
        child.on('exit', (code, signal) => appendBackendLog(`Exit code=${code} signal=${signal}`, 'exit'));

        return child;
    }
}

const HEALTH_URL = `http://127.0.0.1:${API_PORT}/api/health`;
const BACKEND_READY_TIMEOUT_MS = 60000;

async function probeBackend() {
    try {
        const response = await axios.get(HEALTH_URL, { timeout: 2000 });
        return response.data && typeof response.data === 'object' ? response.data : {};
    } catch (err) {
        return null;
    }
}

async function waitForBackend(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
        const health = await probeBackend();
        if (health) return health;
        if (Date.now() >= deadline) return null;
        console.log('Waiting for backend...');
        await new Promise((resolve) => setTimeout(resolve, 500));
    }
}

function assertBackendAddressesThisInstall(health, userDataPath) {
    // Adopting whatever answers on port 8000 is how a window ends up showing a
    // different installation's databases. The backend reports the roots it
    // resolved; anything we cannot match is refused rather than guessed at.
    const reportedRoot = health && health.app_data_root;
    if (!reportedRoot) {
        throw new Error(
            `A backend is already running on port ${API_PORT}, but it did not report ` +
            'which data directory it is using, so it cannot be confirmed as this ' +
            "installation's backend. Stop that process and start the app again."
        );
    }
    if (path.resolve(reportedRoot) !== path.resolve(userDataPath)) {
        throw new Error(
            `The backend already running on port ${API_PORT} is using a different ` +
            `data directory.\n\nIt is using:\n  ${reportedRoot}\n\nThis app expects:\n  ` +
            `${userDataPath}\n\nStop that process and start the app again.`
        );
    }
}

async function ensureBackend(dbPath, userDataPath) {
    // Returns the spawned child process, or null when an externally launched
    // backend is being reused (so `will-quit` never kills a process we do not own).
    const skipSpawn = String(process.env.ANALYTICS_SKIP_BACKEND_SPAWN || '').trim() === '1';

    let existing = await probeBackend();
    if (!existing && skipSpawn) {
        console.log('Backend is started externally; waiting for it...');
        existing = await waitForBackend(BACKEND_READY_TIMEOUT_MS);
        if (!existing) {
            throw new Error(
                `The externally started backend never became ready on port ${API_PORT}.`
            );
        }
    }

    if (existing) {
        assertBackendAddressesThisInstall(existing, userDataPath);
        console.log(`Reusing the backend already running on port ${API_PORT}.`);
        return null;
    }

    const child = startPythonParams(dbPath, userDataPath);
    console.log(`Python API started with PID: ${child.pid}`);
    try {
        const health = await waitForBackend(BACKEND_READY_TIMEOUT_MS);
        if (!health) {
            throw new Error(`The backend did not become ready on port ${API_PORT}.`);
        }
        assertBackendAddressesThisInstall(health, userDataPath);
        return child;
    } catch (err) {
        // apiProcess is assigned only after this function returns. Until then,
        // ensureBackend owns the child and must stop it on every failure path;
        // the will-quit handler cannot see it yet.
        if (child.exitCode === null && child.signalCode === null) {
            console.log(`Killing unready Python process with PID: ${child.pid}`);
            try {
                child.kill();
            } catch (killErr) {
                console.error(`Failed to kill Python process ${child.pid}:`, killErr);
            }
        }
        throw err;
    }
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1500,
        height: 900,
        backgroundColor: '#1a1a1a',
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js')
        }
    });

    if (process.env.NODE_ENV === 'development') {
        // Wait for Vite to be ready usually, but we assume npm run dev is parallel
        mainWindow.loadURL('http://localhost:5173');
        // mainWindow.webContents.openDevTools();
    } else {
        mainWindow.loadFile(path.join(__dirname, 'dist/index.html'));
    }
}

app.whenReady().then(async () => {
    const userDataPath = app.getPath('userData');
    // Ensure we have a persistent path for the database
    const dbPath = path.join(userDataPath, 'analytics.db');
    console.log(`Database path set to: ${dbPath}`);

    if (process.env.NODE_ENV !== 'development') {
        const backendLogPath = getBackendLogPath();
        appendBackendLog(`Backend log file: ${backendLogPath}`);
    }

    try {
        apiProcess = await ensureBackend(dbPath, userDataPath);
    } catch (err) {
        const message = err && err.message ? err.message : String(err);
        console.error(message);
        appendBackendLog(message, 'error');
        dialog.showErrorBox('Analytics backend unavailable', message);
        app.quit();
        return;
    }

    console.log('Backend is ready!');

    // Setup Auto Updater
    setupAutoUpdater();

    createWindow();
});

app.on('will-quit', () => {
    if (apiProcess) {
        console.log('Killing Python process...');
        apiProcess.kill();
    }
    if (backendLogStream) {
        console.log('Closing backend log stream...');
        backendLogStream.end();
    }
});

app.on('window-all-closed', () => {
    app.quit();
});
