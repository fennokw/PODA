'use strict';

const { packager } = require('./package-electron.cjs');
const { copyFileSync, mkdirSync, mkdtempSync, rmSync } = require('node:fs');
const { execFileSync } = require('node:child_process');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { devDependencies, version } = require('./package.json');

async function build() {
  const root = path.resolve(__dirname, '..');
  const stage = mkdtempSync(path.join(tmpdir(), 'poda-desktop-'));
  try {
    const appDir = path.join(stage, 'app');
    const backendDir = path.join(stage, 'backend');
    mkdirSync(appDir);
    mkdirSync(backendDir);
    for (const file of ['main.cjs', 'preload.cjs', 'package.json']) {
      copyFileSync(path.join(__dirname, file), path.join(appDir, file));
    }
    execFileSync('python3', [path.join(__dirname, 'stage_backend.py'), backendDir], { stdio: 'inherit' });
    const output = await packager({
      dir: appDir, out: path.join(root, 'dist', 'desktop'), overwrite: true,
      name: 'PODA', platform: 'darwin', arch: 'arm64', electronVersion: devDependencies.electron,
      appBundleId: 'com.fennokw.poda', appVersion: version, asar: true, prune: false,
      extendInfo: {
        NSDesktopFolderUsageDescription: 'PODA can access Desktop files and folders you choose in Files & Agent.',
        NSDocumentsFolderUsageDescription: 'PODA can access Documents files and folders you choose in Files & Agent.',
        NSDownloadsFolderUsageDescription: 'PODA can access Downloads files and folders you choose in Files & Agent.',
        NSAppleEventsUsageDescription: 'PODA uses Apple Events to read or modify mail you authorize and show local notifications.',
      },
      extraResource: [backendDir],
    });
    for (const directory of output) {
      const bundle = path.join(directory, 'PODA.app');
      if (process.platform === 'darwin') {
        execFileSync('codesign', ['--force', '--deep', '--sign', '-', bundle], { stdio: 'inherit' });
        execFileSync('codesign', ['--verify', '--deep', '--strict', bundle], { stdio: 'inherit' });
      }
      console.log(bundle);
    }
  } finally {
    rmSync(stage, { recursive: true, force: true });
  }
}

build().catch(error => { console.error(error); process.exitCode = 1; });
