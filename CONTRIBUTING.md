# Contributing to PODA

PODA's core design constraints are local inference, an inspectable memory graph, capability-grounded answers, and explicit approval/receipts for side effects. Please preserve those invariants when making changes.

1. Create a branch from this **source-only** repository. Do not import private data, logs, screenshots, backups, account metadata, Keychain dumps, or the original development Git history.
2. Add or update tests for every security-sensitive change. Network-facing tests must mock requests; no CI test may require a personal account, credentials or live Ollama. macOS integration tests should be marked/skipped outside compatible environments.
3. Run `python -m pytest -q tests`, `python tools/release_guard.py --check`, and on a Mac `./VERIFY_PODA.command`. Use a throwaway data directory for tests, never your real `~/Library/Application Support/PODA-Public/`.
4. Document any new outbound host, executable harness, file permission or sensitive data type in `SECURITY.md` and `docs/THREAT_MODEL.md`. Do not expand external access silently.
5. Do not say a file, email or calendar action succeeded unless an actual tool receipt verifies it. Keep Ollama/embedding calls outside SQLite write transactions.
6. Keep HTML/CSS/JS self-hosted. Avoid CDN scripts and telemetry. Any new binary/wheel or image assets require explicit release-packager approval.

For public pull requests, use synthetic fixture data (`example.test` addresses and synthetic UUIDs) and scrub stack traces. Source ZIP releases are built by `./PACKAGE_PODA.command`, which fails on unexpected files and selected secret patterns.
