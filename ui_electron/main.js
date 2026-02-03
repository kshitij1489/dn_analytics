const { app, BrowserWindow, ipcMain } = require('electron');
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

function startPythonParams(dbPath) {
    const isDev = process.env.NODE_ENV === 'development';

    const env = { ...process.env, DB_URL: dbPath };
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

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1400,
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

app.whenReady().then(() => {
    const userDataPath = app.getPath('userData');
    // Ensure we have a persistent path for the database
    const dbPath = path.join(userDataPath, 'analytics.db');
    console.log(`Database path set to: ${dbPath}`);

    if (process.env.NODE_ENV !== 'development') {
        const backendLogPath = getBackendLogPath();
        appendBackendLog(`Backend log file: ${backendLogPath}`);
    }

    apiProcess = startPythonParams(dbPath);
    console.log(`Python API started with PID: ${apiProcess.pid}`);

    // Setup Auto Updater
    setupAutoUpdater();

    // Health Check Loop
    const checkServer = () => {
        axios.get(`http://127.0.0.1:${API_PORT}/api/health`)
            .then(() => {
                console.log('Backend is ready!');
                createWindow();
            })
            .catch(() => {
                console.log('Waiting for backend...');
                setTimeout(checkServer, 500);
            });
    };
    checkServer();
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
