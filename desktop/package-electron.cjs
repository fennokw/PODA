'use strict';

const { packager: build } = require('@electron/packager');
const checksums = require('electron/checksums.json');
const { setTimeout: sleep } = require('node:timers/promises');

const releaseAPI = 'https://api.github.com/repos/electron/electron/releases';
const mirrorConfigured = [
  'ELECTRON_MIRROR', 'npm_config_electron_mirror', 'NPM_CONFIG_ELECTRON_MIRROR',
  'npm_package_config_electron_mirror',
].some(name => process.env[name]);

const networkErrors = new Set([
  'ECONNRESET', 'ETIMEDOUT', 'EAI_AGAIN', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_SOCKET',
]);

async function releaseDownload({ electronVersion, platform, arch }) {
  const tag = `v${electronVersion}`;
  const filename = `electron-${tag}-${platform}-${arch}.zip`;
  const response = await fetch(`${releaseAPI}/tags/${encodeURIComponent(tag)}`, {
    headers: { Accept: 'application/vnd.github+json' },
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error(`Electron release API returned HTTP ${response.status}.`);
  const { assets } = await response.json();
  const asset = assets.find(item => item.name === filename);
  if (!Number.isSafeInteger(asset?.id)) throw new Error(`Electron release asset missing: ${filename}`);
  return {
    checksums,
    mirrorOptions: { resolveAssetURL: () => `${releaseAPI}/assets/${asset.id}` },
    downloadOptions: { headers: { Accept: 'application/octet-stream' }, cache: 'no-store' },
  };
}

async function packager(options) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      const download = attempt === 3 && !mirrorConfigured
        ? await releaseDownload(options)
        : { checksums };
      return await build({ ...options, download });
    } catch (error) {
      const status = error.response?.status;
      const transient = status === 408 || status === 429 || status >= 500
        || networkErrors.has(error.cause?.code ?? error.code);
      if (!transient || attempt === 3) throw error;
      console.warn(`Electron download failed (${error.message}). Retrying (${attempt}/2).`);
      await sleep(2000 * attempt);
      if (attempt === 2 && !mirrorConfigured) console.warn('Trying the official Electron release API.');
    }
  }
}

module.exports = { packager };
