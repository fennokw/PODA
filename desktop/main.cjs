'use strict';

const { app, BrowserWindow, dialog, ipcMain, session, shell, powerSaveBlocker, powerMonitor } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes, createHash } = require('node:crypto');
const { existsSync, readFileSync, mkdirSync, createWriteStream } = require('node:fs');
const { homedir } = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { createRecovery } = require('./recovery.cjs');
const { setTimeout: sleep } = require('node:timers/promises');

const source = app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.join(__dirname, '.backend');
const data = path.resolve(process.env.PODA_DATA_DIR || path.join(homedir(), 'Library', 'Application Support', 'PODA-Public'));
const header = 'X-PODA-Desktop';
const token = randomBytes(32).toString('hex');
let backend;
let window;
let origin;
let closing = false;
let wakeLock;
let opening = null;
let pythonRuntime;
let lastPath = '/';
let recoveryDialog = false;
const expectedExits = new WeakSet();

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
  const processEnv = { ...process.env, PODA_DATA_DIR: data, PODA_DESKTOP_TOKEN: token, PODA_DESKTOP_PARENT_PID: String(process.pid),
    PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
    PATH: ['/opt/homebrew/bin', '/usr/local/bin', process.env.PATH || '/usr/bin:/bin'].join(path.delimiter) };
  // Packaged builds always use the existing production encryption policy.
  if (app.isPackaged) processEnv.PODA_DB_ENCRYPTION = 'required';
  const child = spawn(python, ['-u', path.join(source, 'desktop_backend.py')], {
    cwd: source, env: processEnv, stdio: ['pipe', 'pipe', 'pipe'],
  });
  backend = child;
  child.stdin.on('error', () => {});
  child.stderr.pipe(log, { end: false });
  child.once('close', () => log.end());
  const lines = readline.createInterface({ input: child.stdout });
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('The local backend did not report a port.')), 45000);
    const done = fn => value => { clearTimeout(timer); fn(value); };
    child.once('error', done(reject));
    child.once('close', done(code => reject(new Error(`Backend exited during startup (code ${code}).`))));
    lines.on('line', line => {
      if (line.startsWith('PODA_DESKTOP_ERROR ')) {
        log.write(line + '\n');
        try {
          const problem = JSON.parse(line.slice('PODA_DESKTOP_ERROR '.length));
          done(reject)(Object.assign(new Error(problem.message), { code: problem.code }));
        }
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
    if (child.exitCode !== null || child.signalCode) {
      throw new Error('The local backend stopped. Another PODA instance may be using the data directory.');
    }
    try {
      const response = await fetch(url + '/system/build', {
        headers: { [header]: token }, redirect: 'error', signal: AbortSignal.timeout(1000),
      });
      if (response.ok && (await response.json()).build_id) {
        child.once('exit', () => { if (!closing && !expectedExits.has(child)) recoverWindow().catch(reportRecovery); });
        return url;
      }
    } catch { /* The socket is bound while the database and integrations initialize. */ }
    await sleep(250);
  }
  throw new Error('The local backend did not become ready. Check the desktop server log.');
}

function safePath(value) {
  try { const url = new URL(value); return url.origin === origin ? url.pathname + url.search + url.hash : '/'; }
  catch { return '/'; }
}

async function stopBackend() {
  const child = backend;
  if (!child || child.exitCode !== null || child.signalCode) return;
  expectedExits.add(child);
  await new Promise(resolve => {
    const timer = setTimeout(() => child.kill('SIGKILL'), 12000);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
    child.stdin.end();
  });
}

const ensureBackend = createRecovery({
  isClosing: () => closing,
  delay: ms => sleep(ms),
  probe: async () => {
    if (!origin || !backend || backend.exitCode !== null || backend.signalCode) return false;
    try {
      const r = await fetch(origin + '/system/build', { headers: { [header]: token }, redirect: 'error', signal: AbortSignal.timeout(2000) });
      return r.ok && !!(await r.json()).build_id;
    } catch { return false; }
  },
  stop: stopBackend,
  start: () => startBackend(pythonRuntime),
  onReady: async next => {
    origin = next;
    if (window && !window.isDestroyed()) await window.loadURL(origin + lastPath);
  },
});

function reportRecovery(error) {
  if (closing || recoveryDialog) return;
  recoveryDialog = true;
  dialog.showMessageBox({type:'error',title:'PODA needs to reconnect',message:error.message,
    detail:'Your saved data is intact. Interrupted actions are not automatically repeated. Check the server log if Retry fails.',
    buttons:['Retry','Close'],defaultId:0,cancelId:1}).then(r => {
      recoveryDialog = false;
      if (r.response === 0) recoverWindow().catch(reportRecovery);
    });
}

async function recoverWindow() {
  if (closing || !pythonRuntime) return;
  await ensureBackend();
}

function openWindow() {
  if (closing) return Promise.resolve();
  if (opening) return opening;
  opening = (async () => {
    await ensureBackend();
    if (closing) return;
    if (window && !window.isDestroyed()) {
      if (window.isMinimized()) window.restore();
      window.show(); window.focus(); return;
    }
    const localSession = session.fromPartition('poda-desktop');
    const win = new BrowserWindow({
      width:1440,height:930,minWidth:840,minHeight:620,title:'PODA',backgroundColor:'#111018',
      webPreferences:{session:localSession,sandbox:true,contextIsolation:true,nodeIntegration:false,
        webviewTag:false,preload:path.join(__dirname,'preload.cjs')},
    });
    window = win;
    const isLocal = url => URL.canParse(url) && new URL(url).origin === origin;
    for (const eventName of ['will-navigate','will-redirect','will-frame-navigate']) {
      win.webContents.on(eventName,(event,url) => {
        if (!isLocal(typeof url === 'string' ? url : url?.url ?? event.url)) event.preventDefault();
      });
    }
    win.webContents.on('did-navigate',(_e,url) => { if (isLocal(url)) lastPath = safePath(url); });
    win.webContents.on('did-navigate-in-page',(_e,url,isMainFrame) => { if (isMainFrame && isLocal(url)) lastPath = safePath(url); });
    win.webContents.on('will-attach-webview',event => event.preventDefault());
    win.webContents.setWindowOpenHandler(({url}) => {
      const target = new URL(url);
      if (['http:','https:'].includes(target.protocol) && !target.username && !target.password && !isLocal(url)) shell.openExternal(url).catch(console.error);
      return {action:'deny'};
    });
    let rendererRetries = 0;
    let loadRetries = 0;
    win.webContents.on('render-process-gone',async () => {
      if (closing || win.isDestroyed()) return;
      if (++rendererRetries > 2) { reportRecovery(new Error('The window renderer repeatedly stopped. Close and reopen the window.')); return; }
      try { await ensureBackend(); if (!win.isDestroyed()) await win.loadURL(origin + '/#/ask'); }
      catch(error) { reportRecovery(error); }
    });
    win.webContents.on('did-fail-load',(_e,code,_description,_url,isMainFrame) => {
      if (!isMainFrame || code === -3 || closing || win.isDestroyed()) return;
      if (++loadRetries > 2) { reportRecovery(new Error('The window could not load. Close and reopen the window.')); return; }
      // A healthy server can still leave a failed navigation blank after waking.
      setTimeout(async () => {
        try {
          const restarted = await ensureBackend();
          if (!restarted && !closing && !win.isDestroyed()) await win.loadURL(origin + lastPath);
        } catch(error) { reportRecovery(error); }
      }, 500);
    });
    win.on('closed',() => { if (window === win) window = null; });
    await win.loadURL(origin + lastPath);
  })().finally(() => { opening = null; });
  return opening;
}

ipcMain.handle('poda:shutdown', async event => {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
      !origin || !URL.canParse(event.senderFrame.url) || new URL(event.senderFrame.url).origin !== origin) return false;
  app.quit();
  return true;
});

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
  pythonRuntime = python;
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
  while (!origin && !closing) {
    try { origin = await startBackend(python); }
    catch (error) {
      if (error.code !== 'profile_busy') throw error;
      const previous = backend;
      if (previous.exitCode === null && !previous.signalCode) {
        await new Promise(resolve => previous.once('exit', resolve));
      }
      const result = await dialog.showMessageBox({
        type: 'warning', title: 'PODA data folder is busy',
        message: 'Another PODA server is using this data folder.',
        detail: 'Quit the other PODA app and click Retry. If it was force-quit and Retry still shows this message, restart your Mac, then open PODA again.',
        buttons: ['Retry', 'Quit'], defaultId: 0, cancelId: 1,
      });
      if (result.response !== 0) { app.quit(); return; }
    }
  }
  if (closing) return;
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
  if (process.platform === 'darwin') wakeLock = powerSaveBlocker.start('prevent-app-suspension');
  powerMonitor.on('resume', () => recoverWindow().catch(reportRecovery));
  powerMonitor.on('unlock-screen', () => recoverWindow().catch(reportRecovery));
  await openWindow();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => { if (origin && !closing) openWindow().catch(reportRecovery); });
  app.on('activate', () => { if (origin && !closing) openWindow().catch(reportRecovery); });
  // On macOS the app remains alive after closing its window for Daily Automation.
  app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
  app.on('before-quit', event => {
    if (wakeLock !== undefined && powerSaveBlocker.isStarted(wakeLock)) powerSaveBlocker.stop(wakeLock);
    if (closing) return;
    closing = true;
    if (!backend?.pid || backend.exitCode !== null || backend.signalCode) return;
    event.preventDefault();
    stopBackend().finally(() => app.quit());
  });
  app.whenReady().then(start).catch(error => {
    dialog.showErrorBox('PODA could not start', `${error.message}\n\nLog: ${path.join(data, 'logs', 'desktop-server.log')}`);
    app.quit();
  });
}
