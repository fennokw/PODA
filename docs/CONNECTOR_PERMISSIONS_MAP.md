# PODA v0.6.0-public — Connector and execution permissions

**Fresh public build:** no tokens, connected accounts, folder grants, external harnesses or memories. The following describe possible capabilities only after the user enables and verifies them.

| Connector | Credential / authority | Storage | Access / safety | External host |
|---|---|---|---|---|
| Ollama | local daemon, no account | separate local Ollama service | chat, coding, embeddings and tool planning; installed models discovered at runtime | `127.0.0.1:11434` |
| Notion | user-created internal integration token | Keychain `PODA.Public.Notion` | original database/data-source read after explicit sharing; write only after schema mapper, verification and confirmation | exact HTTPS `api.notion.com`, redirects denied |
| Apple Mail | macOS Automation permission for already-configured Mail.app | Mail.app holds account credentials; PODA stores scoped metadata | read first; modification and sending require separate per-account scopes; send/trash ask for confirmation | Mail.app itself connects to its configured providers |
| Gmail IMAP/SMTP | account-specific app password | Keychain `PODA.Public.Gmail` | Gmail hosts only, per-account modify/send grants and receipts | `imap.gmail.com`, `smtp.gmail.com` |
| Legacy IMAP preview | retired | none | HTTP 410; makes no network request | none |
| Filesystem | per-folder grant, optionally `allow_execute` | encrypted `fs_grants` table | canonical path read/edit; real code execution blocked without available macOS sandbox or conscious unsafe override | none for built-in read/edit tools |
| External CLI software | opt-in acknowledgement and per-harness grant | encrypted software-grant records | disabled until `PODA_ALLOW_EXTERNAL_SOFTWARE=I_TRUST_MY_INSTALLED_HARNESSES` and explicit enable; **not OS-sandboxed** | depends on installed harness |
| Database | randomly generated key on first run | Keychain `PODA.Public.Database` | required SQLCipher at rest; refuse to start in normal mode without encryption | none |
| Backup | independent AES-GCM key | Keychain `PODA.Public.BackupKey` | encrypted `.podabkp` and verified recovery | none |

Not implemented: Google Calendar, hosted inference, multi-user cloud access, unrestricted remote shell or file deletion. Outbound allowlisting in `security/egress.py` protects the HTTP connector that uses it; it is **not** a system-wide network firewall. Mail.app and deliberately enabled CLI harnesses have distinct permissions and network behavior.
