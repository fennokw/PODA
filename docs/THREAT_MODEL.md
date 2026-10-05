# PODA v0.5.1-public — Technical threat model

This document describes implemented boundaries, known limitations, and deliberate opt-ins. It is not a certification or third-party security review. All personal data starts **empty** in a fresh install of this public source archive; new data is created under `~/Library/Application Support/PODA-Public/` rather than in the repository.

## Assets and locations

- SQLCipher database: chat messages, memory/surface nodes, vector embeddings, search indices, projects, capability state, action proposals/receipts, connection metadata and egress logs.
- Keychain: independently named `PODA.Public.Database`, `PODA.Public.Notion`, `PODA.Public.Gmail` and `PODA.Public.BackupKey` service secrets. No real tokens are included in source.
- External states only if the user opts in: Notion data sources and Apple Mail/Google mail. Notion and mail providers can retain copies of API-transmitted data.
- Application logs under private app-support `logs/`; they are plain access/error logs even though the database is encrypted. Review logs and diagnostic outputs before sharing.

## Boundaries

| Boundary | Implementation | Residual risk |
|---|---|---|
| Local HTTP | Uvicorn loopback bind `127.0.0.1`, Host validation and per-process same-origin session cookie | Another process logged in as the same macOS user can generally open the UI and obtain a session |
| Browser | SameSite session, origin checks, CSP, no external scripts/fonts/telemetry | A malicious extension or compromised browser can observe visible data |
| Storage | `PODA_DB_ENCRYPTION=required` defaults to SQLCipher; database/backup keys in Keychain; app-support directories `0700`; no silent plaintext migration in this release | An unlocked user account, privileged process, Keychain access or live database process memory can reveal content |
| Backups | AES-256-GCM encrypted `.podabkp` stored outside checkout, key in Keychain | Losing the key can make backups unrecoverable; manual plaintext exports and historical system backups are outside PODA's control |
| External HTTP | Exact HTTPS `api.notion.com` allowlist, offline toggle, egress metadata audit, `allow_redirects=False` and 3XX rejection | OS, other apps and explicit mail adapters have their own network channels; allowlists are per connector, not a system-wide firewall |
| Mail | Apple Mail uses macOS Automation; optional Gmail IMAP/SMTP restricted to Gmail hosts, stored app password in Keychain; per-account read/modify/send flags | Mail.app and Gmail have their own permission/transport models; direct Gmail adapter must not claim that all network is blocked by the Notion allowlist |
| Legacy IMAP | `/email/imap/preview` returns 410 and makes no connection | Older clients need to migrate to the scoped Mail screen |
| Files | Canonicalized explicit folder grants; no general delete tool; hash-verified writes and receipts | A malicious local process or elevated macOS app may still read granted paths; symlink race windows depend on filesystem operations |
| Code | Requires explicit execute grant **and** macOS `sandbox-exec`; absent sandbox = blocked by default. Expert override must use exact `PODA_UNSAFE_EXECUTE` acknowledgement | `sandbox-exec` is deprecated and not a formally validated escape-proof security boundary; use a restricted user/VM for untrusted programs |
| External CLI | Disabled by default; opt-in exact `PODA_ALLOW_EXTERNAL_SOFTWARE` acknowledgement and per-harness permissions | If enabled, harnesses may execute with the full logged-in user's OS permissions and may contact the internet |
| LLM | Local Ollama; capability ledger injected on requests; retrieved email, Notion, files and prior assistant replies marked as untrusted | Language models still make mistakes. Never rely solely on model output as a proof of side effects; check receipts and underlying source |
| Memory commit | Changes committed atomically; embeddings queued and refreshed **after** the write transaction; cached/lexical fallback offline | Without Ollama, newly edited text may not have updated vector relations until a retry succeeds |

## What "no other program can see" does not mean

PODA does not transmit personal chat/memory to hosted LLMs and does not expose the server on the LAN by default. However, software running as the same macOS user can generally inspect local files you can read, access the browser's current page and potentially obtain Keychain permissions; administrator privileges are more powerful still. PODA cannot promise OS-level invisibility to every other local program. Strong separation requires a dedicated macOS user account or appropriately sandboxed native application and, for untrusted code, a VM/container or other operating-system containment.

Never claim that data sent to Notion or an email provider remains physically confined to the Mac. Notion data-source access is controlled by its workspace permissions and the user's integration token; Apple Mail's remote sync is managed by the email provider.

## Public-source release invariants

- No personal `data/` folder, plaintext/encrypted `.db`, application logs, credentials, personal screenshots, memory exports, backup keys or private `.git` history in any archive or initial Git commit.
- Strict source-only positive allowlist at `tools/release_guard.py`; CI checks its output and tests synthetic fixtures. This minimizes known leaks but cannot prove a repository history has never held secrets.
- On first startup, the public build uses a different app-support directory and distinct Keychain service names from private legacy PODA versions. No migration/import is automatic.
- If SQLCipher or Keychain is unavailable, normal startup fails. `PODA_DB_ENCRYPTION=off` is only for deliberately isolated tests or an informed recovery operation with synthetic data.
- Users control outbound connector access, offline mode, account scopes and approval flows; successful external writes must have a verifiable receipt.

## Incident procedure

Revoke any exposed Notion or email tokens through their provider and PODA's connection UI, rotate credentials, preserve safe diagnostic details, and review local backup and Git-history exposure. For disclosure guidance, see `SECURITY.md`.
