const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electron', {
    // Auto-Update API
    checkForUpdate: () => ipcRenderer.send('check-for-update'),
    downloadUpdate: () => ipcRenderer.send('download-update'),
    quitAndInstall: () => ipcRenderer.send('quit-and-install'),
    onUpdateStatus: (callback) => ipcRenderer.on('update-status', (event, data) => callback(data)),
    getAppVersion: () => ipcRenderer.invoke('get-app-version'),
});
