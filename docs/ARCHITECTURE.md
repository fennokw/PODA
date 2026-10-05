# PODA v0.6.0-public — Architecture

The public source build is a **single-machine local-first application**. The browser frontend uses bundled HTML/CSS/JavaScript and WebGL2; the API is FastAPI/Uvicorn bound only to `127.0.0.1`. The local Ollama service handles chat, planning and embeddings. SQLCipher persists memory and audit data outside the source checkout; macOS Keychain keeps encryption keys and connector secrets. External access is opt-in for Notion and connected mail providers only.

```text
Browser (localhost:8787, no CDN/telemetry)
  ├── Ask and stream / tool proposal approval / recall explanations
  ├── Memory Web (local WebGL2, geometry from Open3D backend)
  ├── Plan / Notion / Mail / Files & Agent / Privacy / System
  │
  ▼
FastAPI application [poda_app/main.py]
  ├── chat/: model grounding, live capabilities, tool proposals, SSE streams
  ├── memory/: source chat pairs, durable facts, distilled surface meanings,
  │              cached embeddings, FTS5, spatial positions, transactional edits
  │              POSTCOMMIT embedding queue (no Ollama network call in DB transaction)
  ├── agent/: folder-granted file tools, action receipts, sandbox-aware Python execution,
  │             optional separately acknowledged third-party CLI tools
  ├── connectors/notion/: original-source discovery, schema mapper, agenda, staged writes
  ├── connectors/email/: Apple Mail automation; optional Gmail IMAP/SMTP
  ├── runtime/: Ollama, hardware-aware context budgets, SQLCipher, capability probes
  ├── planner/: explainable priority rankings
  └── security/: loopback sessions, Keychain, encrypt/backup, network audit
  │
  ├── Ollama HTTP (127.0.0.1:11434)
  ├── ~/Library/Application Support/PODA-Public/poda.db (SQLCipher, freshly created)
  ├── macOS Keychain (PODA.Public.* service names)
  ├── api.notion.com [only after integration is explicitly configured]
  └── Mail.app / Gmail servers [only after explicit connection]
```

## Chat, memory and tool request flow

1. The browser obtains a process-scoped session cookie from the home page. Protected API calls require the cookie and valid browser-origin metadata.
2. A new chat request obtains live runtime capabilities and a model profile using the measured physical memory registry and installed Ollama tags. The action gate blocks unsupported actions before the model can hallucinate execution.
3. User chat is persisted in encrypted SQLite and mirrored into a visible source memory node. The memory engine retrieves from FTS5/text embeddings/reference triggers and manually committed semantic positions, favoring relevant surface memories before optionally diving into full conversation pairs.
4. The tool orchestrator validates model-suggested arguments. Read-only approved tools can execute directly with receipts; mutations need an explicit confirmation proposal. External contents remain untrusted data.
5. The assistant's response is streamed into the UI; the pair of user request and assistant answer is a single visible conversation node; prior assistant assertions remain contextual rather than authoritative.
6. If memory text changed, **queue** its embedding within the SQLCipher transaction, **commit**, and only then contact Ollama. A disconnected embedding server leaves a durable pending job; lookup falls back to cached vectors and lexical search.

## 3D memory representation

Clouds group related points inside translucent spatial regions. Small points are individual memories, with always-on connections of variable brightness based on committed distance. Zoom changes point visibility, not membership. Drag/edit operations are client-side drafts until **COMMIT MEMORY CHANGES**. Coordinates and text edits are persisted atomically; embedding changes are processed after that transaction. Open3D generates geometry locally and browser WebGL2 handles smooth interaction.

## Files and external adapters

Filesystem grant checks restrict built-in read/edit tools to canonical approved folders. Python execution is a *different, higher-risk permission* and requires actual OS sandbox detection by default. The expert unsafe override or separately acknowledged external CLI tool execution may operate with the signed-in macOS user's authority. Notion's API calls are HTTPS allowlisted with redirects blocked. Apple's Mail.app itself can contact configured providers; direct Gmail uses an independent restricted SMTP/IMAP adapter. All supported side effects produce receipts.

## Runtime data and release code are separate

Source checkout: Python code, self-hosted frontend assets, shell scripts, synthetic tests and public docs only. Private runtime: `~/Library/Application Support/PODA-Public/` with a fresh encrypted DB, encrypted backups, logs and scratch folders. Build via `./PACKAGE_PODA.command`; release guard positively selects authored files. Start a new GitHub history from the clean source-only ZIP, never the previous development repository.

## Verification boundaries

Linux CI runs unit tests with isolated plaintext **synthetic temporary** databases for portability. It does **not** exercise native Mac Keychain/SQLCipher/Open3D, Apple Mail Automation, the availability of `sandbox-exec` on your macOS release, or live Notion write privileges. `./VERIFY_PODA.command` runs the Mac integration smoke test, which should be followed by explicit read/write verification on your own connected services when authorized.
