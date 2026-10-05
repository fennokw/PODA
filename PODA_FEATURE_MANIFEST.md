# PODA v0.5.1-public — audited feature manifest

**Source-only release.** It includes no user data, chat history, personal memories, tokens, linked accounts, prior app-support databases, backups or earlier private Git history. The memory *framework* remains intact and starts collecting new chat/memory records once installed.

| Function | Modules / verification |
|---|---|
| Single-Mac local web frontend, chat, thought stream, build identity | `poda_app/static/`, `chat/`, `runtime/router.py`, `tests/test_core_api.py` |
| Ollama fast/balanced/deep routing; measured-memory budgets, conservative unknown-hardware fallback | `runtime/models.py`, `runtime/hardware.py` |
| Live capability ledger, unsupported-action gate, action receipts | `runtime/capability.py`, `agent/gate.py`, `agent/receipts.py` |
| Conversation pair nodes, durable personal facts, surface memory distillation, FTS5/cosine/spatial search | `memory/store.py`, `memory/surface.py`, `memory/retrieval.py` |
| Persistent post-commit embedding queue; no Ollama call within the atomic memory commit | `memory/embedding_jobs.py`, `memory/router.py`, `tests/` |
| Open3D 3D geometry, local WebGL rendering, interactive translucent clouds and points, search and transactional commit | `memory/layout.py`, `static/memory.*`, `static/memory-worker.js` |
| Explainable project priorities | `planner/priority.py` |
| Notion original database/data-source discovery, permissions diagnostics, schema mapping, calendar and staged writes | `connectors/notion/` |
| Apple Mail and optionally Gmail IMAP/SMTP with account-level permissions and action receipts | `connectors/email/` |
| Arbitrary-host legacy IMAP preview **disabled** (HTTP 410) | `connectors/email/router.py` |
| Exact-host Notion HTTPS egress, offline mode, outbound audit, denied redirects | `security/egress.py` |
| Scoped folders and hash-verified file edits; code execution blocked without OS sandbox or deliberate unsafe expert override | `agent/grants.py`, `agent/fs_tools.py`, `agent/exec_tools.py`, `agent/sandbox.py` |
| External CLI harnesses disabled until deliberate expert opt-in, additionally subject to per-harness permissions | `agent/software.py`, `agent/software_router.py` |
| SQLCipher **required** for normal startup, separate `PODA-Public` Keychain and data directories, AES-GCM encrypted backups | `runtime/db.py`, `security/keychain.py`, `security/crypto.py` |
| Positive-allowlist archive and GitHub CI security/test gate | `tools/release_guard.py`, `.github/workflows/ci.yml` |

## Not promised

- No cloud-hosted inference or memory sync is included. Notion and connected mail services still require their own external network connections.
- No initial account grants, credentials, remembered chat or personal projects are included.
- Script execution is **not** unconditionally available: OS sandbox must exist or a consciously unsafe override must be set. Third-party software harnesses run with account authority if opt-in is enabled.
- No security tool can guarantee secrecy from a compromised Mac, administrator, malicious same-user process, untrusted browser extension, or external service you explicitly connect.
- Linux CI source/tests cannot verify macOS Keychain, Apple Mail Automation, Open3D native loading, live Notion, or execution on your exact Mac. Run `./VERIFY_PODA.command` and live connector tests locally.
