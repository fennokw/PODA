'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('podaDesktop', {
  chooseGrantPath: kind => ipcRenderer.invoke('poda:choose-grant-path', kind),
});
