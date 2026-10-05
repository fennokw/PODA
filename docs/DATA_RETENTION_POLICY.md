# PODA v0.6.0-public — Data retention

The public GitHub repository and downloadable source ZIP include **no** personal content. The first run creates its own private database; a user explicitly decides when to connect external services. The runtime has no automatic cloud transcript/memory sync.

| Data | Storage | Default and controls |
|---|---|---|
| Conversation pairs and durable user memories | SQLCipher database in private macOS Application Support (`PODA-Public`) | Kept until deleted; visible in the Memory editor, searchable and source-linked; Privacy → Retention or a confirmed memory deletion |
| Surface memories, summaries, embeddings, search index and versions | Same encrypted database | Kept until deleted or recalculated; staged edits only persist after commit; embeddings can be queued offline |
| Capability state, receipts and action proposals | Same encrypted database | Receipts provide proof of attempted/successful tools; retention controls apply |
| Egress log | Same encrypted database | Latest 2000 metadata records by default; body/credential content is not intentionally logged; use Privacy to clear |
| Notion and email account metadata | Encrypted database + service-specific Keychain secrets | Only after explicit connection; revoke in the respective UI and/or provider |
| Email/Notion source content | Loaded through explicit connectors | Not shipped; not deliberately auto-ingested into lasting personal memory without user-approved extraction; actual providers manage their own retention |
| Database logs | `~/Library/Application Support/PODA-Public/logs` | Plaintext diagnostic metadata, `0700` parent; do not share logs without review |
| Encrypted backups | Private app-support `backups/` | User-created `.podabkp` encrypted with Keychain AES-GCM key; delete old backups deliberately, retain a secure key recovery method |
| Manual plaintext export | Wherever the user explicitly exports it | **Outside encryption boundary**; handle and securely remove under your own threat model |

`./reset_poda_memory.command` requires confirmation and removes the current memory/database information while retaining the installed framework. It should never be run against a different PODA install by accident. Uninstalling the source folder does **not** remove your app-support data, Keychain items, stored Ollama models, macOS Mail accounts or remote Notion pages. Review these separately when permanently decommissioning PODA.
