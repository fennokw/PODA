# Security policy and responsible disclosure

PODA is a local-first personal assistant, not a formal security boundary against other processes running under the same macOS account. This public source release is intended for individual experimentation and careful local deployment.

## Supported configuration

- Apple Silicon macOS; listen **only** on `127.0.0.1`; no router forwarding, reverse proxy or exposed public web service.
- Default SQLCipher encryption **required** with its key in this release's isolated macOS Keychain namespace. Fresh user state stored in `~/Library/Application Support/PODA-Public/`, never in the repository.
- Local Ollama; Notion API and explicitly connected Gmail service are optional external destinations. Never paste secrets in chat, issues, logs, or public config.
- External CLI harness execution **off** by default. Python execution blocked if a real macOS sandbox is not detected. The developer-only unsafe override runs with your account's privileges and defeats that protection.
- macOS sandbox-exec is deprecated and may fail on newer macOS. Treat it as a best-effort guard, not a guarantee; use a separate low-privilege OS account or VM for genuinely untrusted programs.

## Which threats are covered

| Threat | Mitigation |
|---|---|
| Accidental public ZIP of private history | Strict source-only packager, excluded backups/DB/logs/.git, clean release history |
| Casual non-loopback web access | Loopback socket binding, host/origin checks, session cookie and CSP |
| Stolen inactive database file | SQLCipher key kept in Keychain; database is unreadable without the key |
| Unexpected Notion credential forwarding | HTTPS exact-host allowlist; **no redirect following** |
| Hidden arbitrary-host IMAP egress | Legacy route disabled; Gmail connector restricts provider hosts |
| Unsupported action hallucinations | Capability ledger, structured tools, explicit approvals and receipts |
| Lost Ollama while editing memory | Offline commit and durable embedding retry queue |

## Not protected

An administrator, malicious app running as your logged-in user, unrestricted AppleScript/Automation, browser extension, screen recorder, unlocked process memory, trusted third-party CLI harness, or compromised operating system may read or alter user-visible information. macOS Keychain can prompt for authorization, but do not assume its entries are inaccessible to same-user programs. Notion and mail providers retain the data you send to their APIs. Plaintext content you deliberately export lies outside the encrypted DB. An AES-encrypted backup is not recoverable if the associated Keychain key is lost.

## Disclosing a security issue

Contact the repository maintainer privately using the GitHub repository's security advisory mechanism if enabled. Otherwise open a **minimal public issue without exploit payloads, personal data, tokens, unredacted logs or actual inbox/database contents** and ask for a private channel. There is currently no promised response SLA or bug-bounty program. Rotate any accidentally exposed credential immediately and purge it from hosting history when possible; deleting the working-tree file alone does not remove it from Git history.

## Before making the repository public

Run `python3 tools/release_guard.py --check`, review a fresh `git status --ignored`, inspect the actual generated ZIP, check `git log --all --stat` for historic private content, and start from this source-only ZIP with a **new initial Git commit**. Never publish the original private v0.5.0 repository or its historical backups. See `docs/THREAT_MODEL.md` for an extended technical discussion.

## LLM history imports

The Personalize importer treats uploaded provider ZIPs as hostile input. It validates paths and archive expansion before parsing, ignores binary attachments, stages the ZIP inside PODA's private Application Support directory with restrictive permissions and deletes the temporary archive after the import finishes or fails. Imported assistant text is stored as contextual provenance, not automatically promoted to user fact. The resulting normalized memories are part of the user's encrypted PODA database and therefore persist until explicitly removed.
