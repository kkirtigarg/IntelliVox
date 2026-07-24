const { app, BrowserWindow, session, shell } = require('electron');
const path = require('path');

const VITE_DEV_PORT = 5173;
const isDev = !app.isPackaged;

let mainWindow = null;

async function requestMicAccess() {
  if (process.platform !== 'darwin') return;
  try {
    const { systemPreferences } = require('electron');
    const status = systemPreferences.getMediaAccessStatus('microphone');
    if (status !== 'granted') {
      await systemPreferences.askForMediaAccess('microphone');
    }
  } catch (err) {
    console.warn('[IntelliVox] Microphone permission request failed:', err.message);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 780,
    minWidth: 800,
    minHeight: 600,
    title: 'IntelliVox',
    backgroundColor: '#0a0a0f',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
    show: false,
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());

  if (isDev) {
    mainWindow.loadURL(`http://127.0.0.1:${VITE_DEV_PORT}`);
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === 'media' || permission === 'microphone');
  });

  session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
    return permission === 'media' || permission === 'microphone';
  });

  await requestMicAccess();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
