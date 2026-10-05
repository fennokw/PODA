# PODA v0.5.1-public — Source-only public release

**Purpose:** provide a clean GitHub-ready copy of the audited v0.5.0 architecture without its private development snapshot, and address the five highest-priority security/reliability findings. This is a new independent profile, not an in-place upgrade of any private PODA installation.

## Security and reliability changes

1. **Private state completely excluded from the public tree.** Fresh profile `~/Library/Application Support/PODA-Public/`, independently named macOS Keychain services, SQLCipher-required default and no automatic copying or plaintext migration from the original private profile. `tools/release_guard.py` positively selects authored source, synthetic tests and public docs. Excludes `.git`, `.venv`, any old live database, plaintext/encrypted backups, logs, snapshots, personal screenshots and machine-specific benchmark files.
2. **Code execution no longer assumes that a folder grant is an OS sandbox.** Execute permission still requires a specific folder grant; if macOS `sandbox-exec` is absent, Python/pytest execution is blocked unless a user intentionally sets an explicit unsafe override. `sandbox-exec` is best-effort/deprecated, not an unbreakable containment mechanism. Third-party CLI harnesses require a separate explicit expert opt-in and can execute with user privileges.
3. **Legacy outbound gaps closed.** The arbitrary-host one-off IMAP preview endpoint returns HTTP 410 even for incomplete old requests. Notion HTTP requests use an exact HTTPS host allowlist, no automatic redirects and offline-mode checks. Apple Mail and the optional fixed-host Gmail adapter use their own distinct permissions.
4. **Memory updates are reliable without Ollama.** Database commits persist text/position changes and enqueue embedding work atomically; embeddings are refreshed after the write transaction ends and retry later if the model server is unavailable. Offline retrieval continues using previously cached and lexical signals.
5. **Model memory reporting is hardware-derived.** The chat router and System screen share measured unified-memory state; unknown hardware defaults to conservative context budgets rather than inventing a 24 GB Mac.

Additional hardening: bounded filesystem reads/hashing, neutralized personal user-name/university defaults in system prompts/mail-account hints, release guard, isolated test-only data stores and GitHub Actions source audit + Python regression tests.

## Public release privacy statement

No chat messages, personal memory nodes, embeddings, conversation summaries, personal projects, connector credentials, connected-account metadata, filesystem grants or historical receipts are shipped. On first run the database is created fresh with generic topic scaffolding (Email, Calendar, Priorities and Personal Memory). Chat conversations then populate the full two-tier memory framework normally. Tests contain clearly synthetic examples only; no actual account needs to be connected to run them.

## Test scope

- Linux source regression run: **147 passing tests, 13 skipped**; macOS-only integration and Ollama-dependent embedding scenarios are skipped when the required native service is absent.
- Python module compilation and bundled JavaScript syntax checks passed.
- Positive-allowlist archive scan and known private-marker/high-confidence-secret checks passed.
- This release has **not** been independently installed on the recipient's Mac, and live Notion/Apple Mail/SQLCipher/Open3D execution requires verification there. `./VERIFY_PODA.command` is the local smoke test.

Full functionality, threat model, developer workflow and setup: [README.md](README.md), [SECURITY.md](SECURITY.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Publish safely

Create a new empty GitHub repository, extract this ZIP, run `python3 tools/release_guard.py --check`, then `git init`, add only vetted files, commit and push. Never reuse the original private repository's `.git` history. Review the included MIT license before publishing or replace it with your preferred license and attribution if you own the relevant code.
