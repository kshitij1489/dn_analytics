const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const axios = require('axios');

let mainWindow;
let apiProcess;
const API_PORT = 8000;

function startPythonParams() {
    const isDev = process.env.NODE_ENV === 'development';

    if (isDev) {
        // DEV: usage of python/uvicorn directly from the virtual environment
        // We assume the venv is in the project root: ../.venv/bin/python3
        const pythonPath = path.join(__dirname, '../.venv/bin/python3');
        return spawn(pythonPath, ['-m', 'uvicorn', 'src.api.main:app', '--host', '127.0.0.1', '--port', API_PORT.toString()], {
            cwd: path.join(__dirname, '..'), // Run from root
            stdio: 'inherit' // helpful for debugging
        });
    } else {
        // PROD: use bundled executable from resources path
        // process.resourcesPath is where Electron puts extraResources
        const executablePath = path.join(process.resourcesPath, 'analytics-backend');
        return spawn(executablePath, ['--host', '127.0.0.1', '--port', API_PORT.toString()]);
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
    apiProcess = startPythonParams();
    console.log(`Python API started with PID: ${apiProcess.pid}`);

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
    if (process.platform !== 'darwin') {
        app.quit();
    }
});
