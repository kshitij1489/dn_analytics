const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const axios = require('axios');
const { autoUpdater } = require('electron-updater');

let mainWindow;
let apiProcess;
const API_PORT = 8000;

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
        // PROD: use bundled executable from resources path
        // In the .dmg, the executable is in Contents/Resources/analytics-backend/analytics-backend
        // or just Contents/Resources/analytics-backend depending on how we copy it.
        // electron-builder "extraResources" usually puts files directly in Resources if "destionation" is root of extraResources.
        // We configured "to": "analytics-backend", so it should be a folder.

        // Let's assume the binary is inside 'analytics-backend' folder (dist directory content)
        // OR if we copy the file itself. The 'dist-backend/analytics-backend' is a directory (from PyInstaller usually) unless --onefile.
        // But the previous plan (Step 12 view) showed extraResources copying from "../dist-backend/analytics-backend".
        // PyInstaller --onedir (default) creates a directory.
        // PyInstaller --onefile creates a file.
        // My spec file has 'exclude_binaries=True' -> COLLECT, so it is ONEDIR (directory).

        const executablePath = path.join(process.resourcesPath, 'analytics-backend', 'analytics-backend');
        console.log(`Executable path: ${executablePath}`);

        return spawn(executablePath, ['--host', '127.0.0.1', '--port', API_PORT.toString()], {
            env: env,
            // stdio: 'inherit' // can cause issues in detached process, but good for debugging if logging to file
        });
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
});

app.on('window-all-closed', () => {
    app.quit();
});
