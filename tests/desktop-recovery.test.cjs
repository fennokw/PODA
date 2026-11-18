'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createRecovery}=require('../desktop/recovery.cjs');
function fixture(overrides={}){
  const calls=[];
  const ensure=createRecovery({probe:async()=>{calls.push('probe');return false;},stop:async()=>calls.push('stop'),start:async()=>{calls.push('start');return 'http://127.0.0.1:1234';},onReady:async origin=>calls.push(origin),delay:async()=>{},isClosing:()=>false,...overrides});
  return {calls,ensure};
}
test('concurrent reopen/wake events share one restart',async()=>{
  const {calls,ensure}=fixture();await Promise.all([ensure(),ensure(),ensure()]);
  assert.deepEqual(calls,['probe','probe','probe','stop','start','http://127.0.0.1:1234']);
});
test('healthy or briefly unavailable backend is retained',async()=>{
  let attempts=0;const {calls,ensure}=fixture({probe:async()=>++attempts===2});
  assert.equal(await ensure(),false);assert.deepEqual(calls,[]);
});
test('quit during recovery never starts a replacement',async()=>{
  let closing=false;const {calls,ensure}=fixture({isClosing:()=>closing,stop:async()=>{closing=true;}});
  assert.equal(await ensure(),false);assert.equal(calls.includes('start'),false);
});
test('failure clears singleflight and repeated crashes stop retrying',async()=>{
  const {ensure}=fixture({start:async()=>{throw new Error('failed');}});
  for(let i=0;i<3;i++)await assert.rejects(ensure(),/failed/);
  await assert.rejects(ensure(),/repeatedly stopped/);
});
test('launcher entry point and local recovery modules are packaged',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const desktop=path.join(__dirname,'../desktop');
  const main=fs.readFileSync(path.join(desktop,'main.cjs'),'utf8');
  const entry=main.match(/spawn\(python, \['-u', path.join\(source, '([^']+)'\)/)[1];
  assert.ok(fs.existsSync(path.join(desktop,entry)),`Missing backend entry point: ${entry}`);
  assert.ok(fs.readFileSync(path.join(desktop,'stage_backend.py'),'utf8').includes(`"${entry}"`));
  const packaging=fs.readFileSync(path.join(desktop,'package-mac.cjs'),'utf8');
  for(const [,name] of main.matchAll(/require\('\.\/([^']+)'\)/g))assert.ok(packaging.includes(`'${name}'`),`Module omitted from package: ${name}`);
});

test('HTTP failure never replaces a still-running backend during window recovery', async () => {
  const {calls, ensure} = fixture({isBackendAlive: () => true});
  await assert.rejects(ensure(), /server is still running/);
  assert.deepEqual(calls, ['probe','probe','probe']);
});

test('closing or retrying a macOS window keeps the backend, and quitting waits for it', async () => {
  const {EventEmitter} = require('node:events');
  const {PassThrough, Writable} = require('node:stream');
  const {createRequire} = require('node:module');
  const fs = require('node:fs');
  const vm = require('node:vm');
  const filename = require('node:path').resolve(__dirname, '../desktop/main.cjs');
  const realRequire = createRequire(filename);
  const until = async condition => {
    for (let i = 0; i < 50 && !condition(); i++) await new Promise(resolve => setImmediate(resolve));
    assert.ok(condition(), 'expected the launcher to finish this step');
  };
  const app = new EventEmitter();
  app.requestSingleInstanceLock = () => true;
  app.whenReady = async () => {};
  let exited = 0;
  app.quit = () => {
    let prevented = false;
    app.emit('before-quit', {preventDefault() { prevented = true; }});
    if (!prevented) exited++;
  };
  const observations = {starts:0, probes:0, closedPipes:0, dialogs:0};
  const windows = [];
  let failNextLoad = false;
  class BrowserWindow extends EventEmitter {
    constructor() {
      super();
      this.webContents = Object.assign(new EventEmitter(), {
        setWindowOpenHandler() {}, isCrashed: () => false, stop() {},
      });
      windows.push(this);
    }
    isDestroyed() { return this.destroyed; }
    isMinimized() { return false; }
    show() {}
    focus() {}
    async loadURL(url) {
      if (failNextLoad) {
        failNextLoad = false;
        throw new Error('Navigation failed');
      }
      this.url = url;
    }
    destroy() {
      this.destroyed = true;
      this.emit('closed');
      app.emit('window-all-closed');
    }
  }
  const child = Object.assign(new EventEmitter(), {
    pid:1234, exitCode:null, signalCode:null,
    stdin:Object.assign(new EventEmitter(), {end() { observations.closedPipes++; }}),
    stdout:new PassThrough(), stderr:new PassThrough(),
    kill() { throw new Error('normal shutdown should finish without a forced kill'); },
  });
  const localSession = {
    async setProxy() {}, setPermissionRequestHandler() {}, setPermissionCheckHandler() {},
    webRequest:{onBeforeRequest() {}, onBeforeSendHeaders() {}},
  };
  const mocks = {
    electron:{app, BrowserWindow,
      dialog:{showMessageBox:async()=>{observations.dialogs++;return {response:1};}},
      ipcMain:{handle() {}}, session:{fromPartition:()=>localSession}, shell:{},
      powerMonitor:new EventEmitter(),
      powerSaveBlocker:{start:()=>1,isStarted:()=>true,stop() {}},
    },
    'node:child_process':{spawn() {
      observations.starts++;
      setImmediate(() => child.stdout.write('PODA_DESKTOP {"port":12500}\n'));
      return child;
    }},
    'node:fs':{existsSync:()=>true,readFileSync:()=>Buffer.from('runtime'), mkdirSync() {},
      createWriteStream:()=>new Writable({write(_chunk,_encoding,done) { done(); }}),
    },
  };
  vm.runInNewContext(fs.readFileSync(filename,'utf8'), {
    require:name=>mocks[name] || realRequire(name), __dirname:require('node:path').dirname(filename),
    process:{env:{},pid:123,platform:'darwin'}, console, URL, AbortSignal, setTimeout, clearTimeout,
    fetch:async()=>{observations.probes++;return {ok:true,json:async()=>({build_id:'test'})};},
  }, {filename});

  await until(() => windows[0]?.url);
  assert.equal(observations.starts,1);
  windows[0].destroy();
  assert.equal(observations.closedPipes,0);
  assert.equal(exited,0);

  failNextLoad = true;
  app.emit('activate');
  await until(() => observations.dialogs===1);
  await new Promise(resolve => setImmediate(resolve));
  app.emit('activate');
  await until(() => windows[2]?.url);
  assert.equal(observations.starts,1);
  assert.equal(observations.probes,1);
  assert.equal(observations.closedPipes,0);

  app.quit();
  app.quit();
  assert.equal(observations.closedPipes,1);
  assert.equal(exited,0);
  child.exitCode=0;
  child.emit('exit',0);
  await until(() => exited===1);
  assert.equal(observations.starts,1);
});
