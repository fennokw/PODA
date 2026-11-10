'use strict';

const { app, BrowserWindow, dialog, ipcMain, session, shell } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes, createHash } = require('node:crypto');
const { existsSync, readFileSync, mkdirSync, createWriteStream } = require('node:fs');
const { homedir } = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { setTimeout: sleep } = require('node:timers/promises');

const source = app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.join(__dirname, '.backend');
const data = path.resolve(process.env.PODA_DATA_DIR || path.join(homedir(), 'Library', 'Application Support', 'PODA-Public'));
const header = 'X-PODA-Desktop';
const token = randomBytes(32).toString('hex');
let backend;
let window;
let origin;
let closing = false;

function runtimePath() {
  const version = createHash('sha256').update(readFileSync(path.join(source, 'requirements.txt'))).digest('hex').slice(0, 16);
  const runtime = path.join(data, 'desktop-runtime', version);
  return existsSync(path.join(runtime, '.ready')) ? path.join(runtime, 'bin', 'python') : null;
}

async function startBackend(python) {
  const logs = path.join(data, 'logs');
  mkdirSync(logs, { recursive: true, mode: 0o700 });
  const log = createWriteStream(path.join(logs, 'desktop-server.log'), { flags: 'a', mode: 0o600 });
  log.on('error', console.error);
  const processEnv = { ...process.env, PODA_DATA_DIR: data, PODA_DESKTOP_TOKEN: token,
    PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
    PATH: ['/opt/homebrew/bin', '/usr/local/bin', process.env.PATH || '/usr/bin:/bin'].join(path.delimiter) };
  // Packaged builds always use the existing production encryption policy.
  if (app.isPackaged) processEnv.PODA_DB_ENCRYPTION = 'required';
  backend = spawn(python, ['-u', path.join(source, 'desktop_backend.py')], {
    cwd: source, env: processEnv, stdio: ['pipe', 'pipe', 'pipe'],
  });
  backend.stdin.on('error', () => {});
  backend.stderr.pipe(log, { end: false });
  backend.once('close', () => log.end());
  const lines = readline.createInterface({ input: backend.stdout });
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('The local backend did not report a port.')), 45000);
    const done = fn => value => { clearTimeout(timer); fn(value); };
    backend.once('error', done(reject));
    backend.once('close', done(code => reject(new Error(`Backend exited during startup (code ${code}).`))));
    lines.on('line', line => {
      if (line.startsWith('PODA_DESKTOP_ERROR ')) {
        log.write(line + '\n');
        try { done(reject)(new Error(JSON.parse(line.slice('PODA_DESKTOP_ERROR '.length)).message)); }
        catch (error) { done(reject)(error); }
        return;
      }
      if (!line.startsWith('PODA_DESKTOP ')) { log.write(line + '\n'); return; }
      try {
        const candidate = JSON.parse(line.slice('PODA_DESKTOP '.length)).port;
        if (!Number.isInteger(candidate) || candidate < 1 || candidate > 65535) throw new Error('Invalid backend port.');
        done(resolve)(candidate);
      } catch (error) { done(reject)(error); }
    });
  });
  const url = `http://127.0.0.1:${port}`;
  for (let attempt = 0; attempt < 160; attempt += 1) {
    if (backend.exitCode !== null || backend.signalCode) {
      throw new Error('The local backend stopped. Another PODA instance may be using the data directory.');
    }
    try {
      const response = await fetch(url + '/system/build', {
        headers: { [header]: token }, redirect: 'error', signal: AbortSignal.timeout(1000),
      });
      if (response.ok && (await response.json()).build_id) return url;
    } catch { /* The socket is bound while the database and integrations initialize. */ }
    await sleep(250);
  }
  throw new Error('The local backend did not become ready. Check the desktop server log.');
}

function openWindow() {
  const localSession = session.fromPartition('poda-desktop');
  window = new BrowserWindow({
    width: 1440, height: 930, minWidth: 840, minHeight: 620, title: 'PODA', backgroundColor: '#111018',
    webPreferences: { session: localSession, sandbox: true, contextIsolation: true,
      nodeIntegration: false, webviewTag: false, preload: path.join(__dirname, "preload.cjs") },
  });
  const isLocal = url => URL.canParse(url) && new URL(url).origin === origin;
  for (const eventName of ['will-navigate', 'will-redirect', 'will-frame-navigate']) {
    window.webContents.on(eventName, (event, url) => {
      if (!isLocal(typeof url === 'string' ? url : url?.url ?? event.url)) event.preventDefault();
    });
  }
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.setWindowOpenHandler(({ url }) => {
    const target = new URL(url);
    if (['http:', 'https:'].includes(target.protocol) && !target.username && !target.password && !isLocal(url)) {
      shell.openExternal(url).catch(console.error);
    }
    return { action: 'deny' };
  });
  window.on('closed', () => { window = null; });
  return window.loadURL(origin);
}

ipcMain.handle('poda:choose-grant-path', async (event, kind) => {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
      !origin || !URL.canParse(event.senderFrame.url) || new URL(event.senderFrame.url).origin !== origin ||
      !['file', 'directory'].includes(kind)) return null;
  const result = await dialog.showOpenDialog(window, {
    title: kind === 'file' ? 'Choose a file for PODA' : 'Choose a folder for PODA',
    defaultPath: app.getPath('desktop'),
    properties: [kind === 'file' ? 'openFile' : 'openDirectory'],
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

async function start() {
  const python = runtimePath();
  if (!python || !existsSync(python)) {
    const result = await dialog.showMessageBox({
      type: 'info', buttons: ['Set Up Python Runtime', 'Quit'], cancelId: 1, defaultId: 0,
      title: 'Set up PODA Desktop', message: 'PODA needs its local Python runtime.',
      detail: 'This opens the setup script in Terminal. When setup finishes, reopen PODA. Your data and downloaded GGUF models stay in their existing locations.',
    });
    if (result.response === 0) {
      const script = path.join(source, 'SETUP_DESKTOP.command');
      const error = await shell.openPath(script);
      if (error) throw new Error(error);
    }
    app.quit();
    return;
  }
  origin = await startBackend(python);
  const localSession = session.fromPartition('poda-desktop');
  await localSession.setProxy({ mode: 'direct' });
  localSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  localSession.setPermissionCheckHandler(() => false);
  localSession.webRequest.onBeforeRequest((details, callback) => {
    const url = new URL(details.url);
    callback({ cancel: ['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) && url.origin !== origin });
  });
  localSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    if (new URL(details.url).origin === origin) headers[header] = token;
    callback({ requestHeaders: headers });
  });
  backend.once('exit', () => {
    if (closing) return;
    dialog.showErrorBox('PODA stopped', `The local server exited. See ${path.join(data, 'logs', 'desktop-server.log')}.`);
    app.quit();
  });
  await openWindow();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (window?.isMinimized()) window.restore();
    if (window) window.focus();
    else if (origin && !closing) openWindow().catch(console.error);
  });
  app.on('activate', () => { if (!window && origin && !closing) openWindow().catch(console.error); });
  // On macOS the app remains alive after closing its window for Daily Automation.
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
  app.on('before-quit', event => {
    if (closing || !backend?.pid || backend.exitCode !== null || backend.signalCode) return;
    event.preventDefault();
    closing = true;
    const deadline = setTimeout(() => backend.kill('SIGKILL'), 12000);
    backend.once('exit', () => { clearTimeout(deadline); app.quit(); });
    backend.stdin.end();
  });
  app.whenReady().then(start).catch(error => {
    dialog.showErrorBox('PODA could not start', `${error.message}\n\nLog: ${path.join(data, 'logs', 'desktop-server.log')}`);
    app.quit();
  });
}
