'use strict';

const { packager: build } = require('@electron/packager');
const checksums = require('electron/checksums.json');
const { setTimeout: sleep } = require('node:timers/promises');

const networkErrors = new Set([
  'ECONNRESET', 'ETIMEDOUT', 'EAI_AGAIN', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_SOCKET',
]);

async function packager(options) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await build({ ...options, download: { checksums } });
    } catch (error) {
      const status = error.response?.status;
      const transient = status === 408 || status === 429 || status >= 500
        || networkErrors.has(error.cause?.code ?? error.code);
      if (!transient || attempt === 3) throw error;
      console.warn(`Electron download failed (${error.message}). Retrying (${attempt}/2).`);
      await sleep(2000 * attempt);
    }
  }
}

module.exports = { packager };
