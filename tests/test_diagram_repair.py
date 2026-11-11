"""Regression coverage for PODA's isolated Mermaid render retry."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_real_rhino_chart_is_retried_without_changing_original_source():
    js = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const chart = fs.readFileSync(process.argv[2], 'utf8');
let listener, attempts = [], response;
const parent = { postMessage(data) { response = data; } };
const context = {
  URL, document: { baseURI: 'http://127.0.0.1:1234/' },
  window: { parent, addEventListener(_name, fn) { listener = fn; },
    mermaid: { initialize() {}, async render(_id, text) {
      attempts.push(text);
      if (text.includes('G1[Create Base Profile (2D Curve)]')) throw Error('Mermaid parse error');
      return { svg: '<svg/>' };
    } } }
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  await listener({ source: parent, origin: 'http://127.0.0.1:1234', data: { id: 1, diagram: chart } });
  assert.equal(attempts.length, 2);
  assert(attempts[1].includes('G1["Create Base Profile (2D Curve)"]'));
  assert(attempts[1].includes('I2["Import Rhino File (.3dm)"]'));
  assert(attempts[1].includes('fill:#ff9,stroke:#333,stroke-width:2px,color:#111827'));
  assert(chart.includes('G1[Create Base Profile (2D Curve)]'));
  assert.equal(response.svg, '<svg/>');
})().catch(e => { console.error(e); process.exit(1); });
'''
    subprocess.run(['node', '-e', js, str(ROOT / 'poda_app/static/chat-diagram-frame.js'),
                    str(ROOT / 'tests/fixtures/rhino_flowchart.txt')], check=True, capture_output=True, text=True)
