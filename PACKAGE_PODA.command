#!/bin/bash
# Clean source-only release. Never archives the checkout recursively.
set -euo pipefail
cd "$(dirname "$0")"
python3 tools/release_guard.py --package
