'use strict';

const { packager } = require('@electron/packager');
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
    for (const file of ['main.cjs', 'package.json']) {
      copyFileSync(path.join(__dirname, file), path.join(appDir, file));
    }
    const stageBackend = String.raw`
import shutil
import sys
from pathlib import Path
from tools.release_guard import ROOT, audit, shipping_paths

target = Path(sys.argv[1])
files = shipping_paths()
errors = audit(files)
if errors:
    raise SystemExit("\n".join(errors))
for file in files:
    relative = file.relative_to(ROOT)
    if relative.as_posix() == "desktop/SETUP_DESKTOP.command":
        relative = Path(relative.name)
    elif relative.parts[0] != "poda_app" and relative.as_posix() not in {
        "setup_poda.command", "requirements.txt", "PODA_VERSION.txt", "LICENSE"
    }:
        continue
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(file, destination)
`;
    execFileSync('python3', ['-c', stageBackend, backendDir], { cwd: root, stdio: 'inherit' });
    const output = await packager({
      dir: appDir, out: path.join(root, 'dist', 'desktop'), overwrite: true,
      name: 'PODA', platform: 'darwin', arch: 'arm64', electronVersion: devDependencies.electron,
      appBundleId: 'com.fennokw.poda', appVersion: version, asar: true, prune: false,
      extendInfo: {
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
