# PODA — Personal Organization Digital Assistant

**A private, local-first personal AI for people who want their assistant to remember useful context, tell the truth about its abilities, organize their work, and ask before taking consequential action.**

PODA is a self-hosted web application for **one Apple Silicon Mac**. Its chat models, embeddings, memory database, task planner and visual second brain run locally. Instead of relying on a hosted AI service to remember your life, PODA maintains an inspectable, editable local record of the context you choose to retain. It can also connect, with your permission, to **the Notion database behind Notion Calendar**, macOS Apple Mail, and optionally Gmail IMAP/SMTP. Those integrations necessarily contact external services; PODA's AI inference does not.

> **Public source release v0.6.0-public.** This repository intentionally contains **no personal memories, chat transcripts, credentials, connected accounts, historical databases, logs, screenshots or private Git history**. On first run it creates a brand-new, encrypted memory database in `~/Library/Application Support/PODA-Public/`. It does not import or modify another PODA installation. The downloadable ZIP is a source repository, **not a signed Mac app**. See [Security and limitations](#security-and-limitations).

## Why PODA exists

Personal assistants are most useful when they retain context across discussions. They are least trustworthy when they confuse what they *could* do with what they *actually did*, fabricate access to a disconnected service, or require private conversations to be stored on a remote AI platform.

PODA began with a practical goal: take the useful elements of a multi-device AI agent—tiered local models, memory, a capability ledger, permissioned tools, and a visual knowledge graph—and build them into a **single-device personal system**. The desired workflow is straightforward: ask PODA to review commitments, reconcile them with an existing Notion schedule, find an email containing an important decision, prioritize projects, or write code in a specifically approved folder. PODA should be blunt about what is connected, show the memories it used, offer a proposed action when a change is required, and provide a real receipt after execution. It should never pretend a file, calendar event or email exists just to agree with its user.

That design leads to five principles:

1. **Truth before agreement.** Current abilities come from live backend checks; external content and remembered assistant replies are not automatically facts.
2. **Local by default.** No hosted LLM calls or remote memory sync. Local chat and memory data are encrypted and kept outside the Git checkout.
3. **Inspectable memory.** Conversation pairs and curated durable facts have visible nodes; distilled surface memories help retrieve the relevant details without filling the context window with everything.
4. **Consent before side effects.** Separate read/edit/execute permissions, confirmation proposals, and independently verified action receipts.
5. **One understandable source of truth.** The Notion connector reaches the original Notion *data source* used by Notion Calendar; it does not require Google Calendar or duplicate events in a second cloud calendar.

PODA is open source so users can inspect the code that holds their personal data, change the behavior, run their own models, and contribute improvements. Open source does **not** guarantee security; the specific threat model and limitations are documented below.

## What you get

| Area | Implementation | Important qualification |
|---|---|---|
| Ask | FastAPI streaming chat, live Ollama status, Fast/Balanced/Deep model modes, separate thought stream, cancellation | Needs locally installed Ollama/models |
| Capability Ledger | Backend probes for model, file, Notion, mail and security state; injected into model context | A live check establishes access, not guaranteed correctness for arbitrary world knowledge |
| Second Brain | Encrypted SQLite/SQLCipher memory, source-tagged conversation pairs, durable notes, distilled surface memories, text embeddings, FTS5, cosine and spatial retrieval, recall explanations | Embeddings are queued when Ollama is offline; full semantic retrieval resumes when available |
| 3D memory | Open3D geometry with local WebGL viewer, translucent clouds, single-point memories, distance-weighted wires, search, zoom, smooth camera controls, idle rotation and explicit commit/discard | Your edits change semantic positions/relationships only after commit |
| Plan | Priorities ranked by deadlines, importance, effort, duration and available context with explainable scores | Does not silently modify your calendar |
| Notion | Original database/data-source discovery, diagnostics, actual property-schema mapping, page queries, agenda, calendar view and guarded write proposals | Requires your own Notion integration and explicit sharing; chat writes require verification and confirmation |
| Mail | Apple Mail adapter using Automation permission; optional Gmail-specific IMAP/SMTP using Keychain-stored app password; search, read, drafts, per-account modify/send scopes | No mail account connected on first run; arbitrary-host legacy IMAP preview removed |
| Files & code | Explicit folder grants, bounded reads/search, atomic writes with backups, real action receipts; controlled Python/pytest execution | **Execution is blocked** unless a supported macOS sandbox is present, or the user explicitly opts into unsafe execution |
| Software skills | CLI-Anything discovery, permissions, skill instructions and receipts | External CLI execution is off by default; explicitly enabled harnesses can have ordinary user privileges |
| Privacy | Loopback-only server, session/origin checks, outbound audit/offline mode, Keychain credentials, SQLCipher storage and encrypted backups | Same-user malicious processes, browser extensions and compromised macOS are outside PODA's isolation guarantee |

The UI provides Ask, Memory, **Personalize**, Plan, Notion, Mail, Files & Agent, Privacy and System. `⌘K` opens the command palette; number keys switch the main screens. `OPEN3D MEMORY` opens the dedicated `/memory-viewer`.

## Import an existing ChatGPT or Claude history

PODA can optionally bootstrap itself from an official **main/data export ZIP** from ChatGPT or Claude. Open **Personalize** in the left rail and upload the provider export directly in the local UI. The archive is staged under PODA's private Application Support directory with restrictive permissions, parsed locally, and deleted immediately after parsing; the raw ZIP is not retained.

The importer is designed to make a fresh PODA installation useful quickly without turning prior assistant output into unquestioned truth:

1. User/assistant turns are normalized into source-tagged **visible exchange memories** in the same second-brain graph used by live chat. Assistant text is preserved as contextual conversation, not authoritative user fact.
2. Conversations are grouped into **high-level semantic topic clouds** using local Ollama embeddings when available. If Ollama is offline, a deterministic lexical fallback still imports everything and builds manageable topic clouds.
3. Within those big clouds, PODA's existing surface-memory system continues to distill related exchanges into smaller semantic surfaces. The result is a hierarchy rather than one giant Conversation Memory bucket.
4. If the user enables personalization, PODA analyzes **user-authored text only** to derive workflow, communication and response-style preferences. It explicitly avoids inferring sensitive traits and stores the resulting profile as both local system personalization context and a visible second-brain memory.
5. Future chats can match the imported topic-cloud tags, so new related conversations naturally continue inside the relevant high-level area instead of rebuilding the user's organization from scratch.
6. Importing the exact same archive twice is idempotent: PODA fingerprints the ZIP and refuses to duplicate it.

The archive parser rejects path traversal, symbolic links, suspicious compression ratios and oversized archives. Attachments and binary content are ignored. The feature never uploads the export to a hosted inference service; any profile synthesis uses the local Ollama runtime.

## Requirements

- **macOS on Apple Silicon**. The source can be inspected elsewhere; this release's installation and native viewer target macOS.
- **Python 3.11 or newer** (a supported release with wheels for the pinned packages; Python 3.14 is supported by the currently pinned build). Download from [python.org](https://www.python.org/downloads/macos/) if necessary.
- **Ollama for macOS** from [ollama.com/download/mac](https://ollama.com/download/mac). PODA does **not** bundle Ollama or its models.
- **Homebrew** from [brew.sh](https://brew.sh/) if Open3D needs its native `libusb` dependency (`brew install libusb`). PODA asks before installing native dependencies and never silently installs Homebrew.
- Enough free SSD space for models and dependencies. More unified memory improves locally runnable model size and context; PODA must measure the hardware rather than promise a fixed high context on every Mac.
- Safari or another modern local browser that supports WebGL2; macOS Keychain and SQLCipher must be functional for normal startup.

**No Notion, Apple Mail or Gmail account is needed to start.** The application begins with zero connections and an empty memory database.

## Clean macOS setup

### 1. Get the source

If you cloned from GitHub:

```bash
git clone https://github.com/YOUR-USERNAME/PODA.git
cd PODA
```

Replace `YOUR-USERNAME/PODA` with the actual repository address after publishing. If you downloaded the ZIP, unzip it and navigate to its `PODA` folder, for example:

```bash
cd ~/Downloads/PODA
```

### 2. Install Python, Ollama and native prerequisites

Verify Python:

```bash
python3 --version
```

Install Ollama from its official Mac installer and open it once. To verify its local daemon after starting Ollama:

```bash
curl http://127.0.0.1:11434/api/tags
```

If a later Open3D import error mentions `libusb`, install Homebrew manually (if absent), then:

```bash
brew install libusb
```

### 3. Install PODA's Python dependencies

```bash
chmod +x *.command
./setup_poda.command
```

This creates a **local virtual environment** in `.venv/` (ignored by Git), installs the pinned requirements, checks Open3D and **refuses a normal installation if SQLCipher is unavailable**. The encrypted application database will be created separately under `~/Library/Application Support/PODA-Public/`; source checkout and application data are intentionally segregated.

### 4. Install local Ollama models

```bash
./install_ollama_models.command
```

The normal starter selection is `llama3.2:3b` (fast), `qwen3:14b` (advanced) and `nomic-embed-text` (private local embeddings). The script measures the Mac's unified memory and may offer an optional Qwen3.6 coding model where supported by that Ollama installation and available memory. These tags, sizes and runtimes evolve; confirm availability from `ollama list`, the System screen and Ollama's library. Do not blindly enlarge the context window: KV cache, OS memory pressure and model concurrency matter.

### 5. Install and verify

```bash
./INSTALL_PODA.command
./VERIFY_PODA.command
```

The full installer calls setup, model installation and verification. If you already ran steps 3–4, running it again should reuse those installations. Verification runs unit tests against disposable test databases, validates frontend/source structure, and tries a loopback test server with **an empty temporary database**; it does not copy your personal data into tests.

### 6. Start PODA

```bash
./start_poda.command
```

Then open **http://127.0.0.1:8787**. The first startup creates a new encrypted database and a fresh second brain. The Memory screen may show system/category scaffolding, but **no shipped conversation or personal memory**. Your first conversation becomes the first real chat memory. Stop PODA with `Ctrl+C` or use `./STOP_PODA.command` as instructed by the launcher.

If port 8787 belongs to another application or private PODA installation, the public launcher **does not kill it**. Stop that process yourself or run:

```bash
PODA_PORT=8788 ./start_poda.command
```

To keep an existing private version entirely separate, leave this public release's `PODA_DATA_DIR` and Keychain service defaults unchanged.

### First-run diagnostics

Use System to confirm installed Ollama models and hardware, Privacy to inspect the encryption status, and the Memory viewer to create and commit a fresh sample memory. If SQLCipher/Keychain initialization fails, PODA must stop with an explicit error rather than write private chats to a plaintext fallback database. The test-suite environment deliberately uses `PODA_DB_ENCRYPTION=off` on temporary synthetic data **only**; do not use that setting for personal operation.

## How the second brain works

1. **In-depth source memories:** A chat user turn and its assistant answer form a provenance-linked exchange node. Distinct durable facts/preferences have their own nodes. Prior assistant statements remain contextual, not verified user truths. All retrievable *historical conversation* is surfaced in the Memory Web rather than an invisible private chat-history feed.
2. **Surface memories:** Related exchanges are distilled into compact core meanings inside topic clouds. Surfaces have their own embeddings and visible children. They avoid dumping entire transcripts into every prompt; on-demand `memory_dive` can expand selected source exchanges when a question warrants deeper detail.
3. **Hybrid recall:** Keyword/full-text search, reference triggers, semantic vectors and committed spatial relationships contribute to relevance. The backend can explain why each memory was recalled. Your manual 3D arrangement reinforces relatedness, but it does not rewrite the underlying words or their original embedding cosine score.
4. **Transactional editing:** Drag points/clouds, edit labels/content/triggers or rearrange membership **as a draft**. `COMMIT MEMORY CHANGES` atomically persists edits, recomputes spatial links and queues embeddings that changed text. `DISCARD DRAFT` restores the committed state.
5. **Offline resilience:** A working Ollama server is **not required to save or move a memory**. Dirty embeddings enter a durable queue during the SQLite commit. After the commit, PODA attempts to refresh pending vectors without holding a database write transaction. It can retry when Ollama returns; lexical and previously cached retrieval remain available in the meantime.

The default ranking combines text-based similarity with committed spatial relationships; read the Memory inspector for the actual relevance factors rather than interpreting visual proximity as a perfect representation of hundreds of embedding dimensions.

## Using Notion without Google Calendar

PODA connects **directly to the original Notion database/data source** that supplies your Notion Calendar view. It does not need Google Calendar.

1. Create an internal integration in your own Notion workspace and enable the minimum content capabilities: **Read** first; add **Insert/Update** only when you want writes.
2. Open the original database page (not merely a linked view) and add your PODA integration to its Connections. Share any related data sources whose relation fields you also want PODA to read.
3. In PODA → **Notion**, enter the integration token; PODA stores it in **macOS Keychain** (separate namespace for this public build).
4. Use **Discover** or **Diagnose** to distinguish database IDs, data source IDs, view IDs, inaccessible databases and wrong-workspace links. Select the actual data source shown in your Notion Calendar.
5. Verify **read**, inspect the dynamically discovered schema, map date/title/status/relation properties, and preview the actual entries. Only after that should you verify insert/update permissions with the disposable write test.
6. Chat-initiated Notion changes produce **proposals requiring explicit approval**. PODA must not infer Notion property names, overwrite import bookkeeping fields or invent an event creation receipt.

A 404 may mean **unshared integration permission** as well as a wrong ID. The diagnostics distinguish what they can establish rather than claiming a missing database solely from a 404. A token is never placed into a chat prompt or committed to this repository.

## Connecting email

- **Apple Mail (recommended on macOS):** Connect via the Mail tab and grant macOS Automation access when the system prompts. PODA uses the accounts already configured in Mail.app; it does not copy those accounts' passwords. Enable read first. Add per-account modify/send permissions only when needed, and approve each consequential action.
- **Gmail IMAP/SMTP (optional):** Connect explicitly with your own provider-supported app password where available. PODA restricts the direct adapter to Gmail's documented hosts and stores credentials in Keychain. It does not offer arbitrary-host IMAP input; that older legacy preview route is retired.
- Search, read, draft, mark, move and send availability is reported by actual adapter state and per-account grants. Sending and destructive actions require confirmation and produce receipts. Untrusted email bodies are not authoritative instructions and are not silently turned into permanent personal facts.
- Proposed commitments can flow from email to **Notion** after date resolution, duplicate checks, the schema mapper and your confirmation. **No live connection is configured by this ZIP.**

Notion and email services necessarily receive the API requests you approve; "local AI" does not mean offline remote connectors.

## Scoped files, coding and external software

PODA begins with **no folder grants**. In Files & Agent, grant individual directories Read or Edit and optionally enable Execute. Read-only file tools list/search within the canonical granted path. Editing requires an authorized root and provides atomic writes with pre-edit backups and read-back receipts. A file edit requested in normal chat goes through the structured tool planner and, when mutating, the explicit action proposal flow.

**Important:** A folder grant alone is **not** a sandbox. Python/pytest can import modules, traverse the user's home directory, spawn child processes or send traffic unless separately confined. For that reason the public release blocks execution by default when it cannot find macOS `sandbox-exec`. On systems where `sandbox-exec` exists it attempts a restrictive seatbelt profile; this legacy tool is not guaranteed to work on every macOS release and is **not a proven defense against malicious programs**. For untrusted code, use a restricted macOS account or properly isolated virtual machine/container. The expert environment override `PODA_UNSAFE_EXECUTE=I_UNDERSTAND_THIS_RUNS_AS_ME` intentionally bypasses OS isolation and should **never** be enabled for untrusted scripts. Ordinary file read/edit approvals are independent of code execution.

External CLI-Anything harnesses are more powerful and may run with your full user account. They are disabled by default and can be unlocked only by deliberately setting `PODA_ALLOW_EXTERNAL_SOFTWARE=I_TRUST_MY_INSTALLED_HARNESSES`, then granting each specific harness the relevant action scope. Do not install arbitrary harnesses from the internet without reviewing their code and dependencies. PODA does not promise the OS-level isolation of these external CLI tools.

## Security and limitations

This release addresses five issues identified during a private-codebase audit:

| Prior gap | Public release mitigation |
|---|---|
| Plaintext historical SQLite backups in the source ZIP | Strict **source-only allowlist packaging**, clean Git history, no data/backup export, fresh separate app-support directory and namespaced Keychain secrets |
| Execute permissions were incorrectly treated as a sandbox | Actual macOS sandbox attempt with **fail-closed default**; explicit dangerous override and disabled third-party harnesses |
| Legacy IMAP/redirect egress bypass | Legacy arbitrary-host IMAP route returns 410; Notion outbound HTTPS is hostname-allowlisted and **redirects are denied** to prevent token forwarding |
| Embedding server was called during memory commit transaction | Persistent embedding jobs committed with the memory, post-transaction retries, cached/lexical fallback while offline |
| Model context assumed 24 GB when hardware was unknown | One measured-memory registry; conservative context windows and explicit unknown-hardware reporting |

The application binds to **127.0.0.1** and uses a same-origin session cookie; it is not a remotely authenticated network service. **Another process running as the same logged-in macOS user can generally access PODA's live UI, inspect the code or potentially request Keychain access.** SQLCipher protects the database at rest when closed and the key is unavailable, but does not isolate unlocked data from processes with sufficient local privileges. Browser extensions, local malware, screenshots, OS backups, an administrator and externally connected service operators remain potential exposures. If adversarial isolation from *other programs running as you* is essential, a separate restricted OS account or genuinely sandboxed app is required. See [SECURITY.md](SECURITY.md) and [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

PODA is an evolving personal tool, **not an independently audited security product**. Linux CI runs the Python tests and release scanner but cannot validate macOS Keychain, native SQLCipher/Open3D, Apple Mail Automation, or live Notion permissions. Run `./VERIFY_PODA.command` on the target Mac and validate connectors separately before entrusting it with consequential operations.

## Backups, reset and upgrades

By default, the private application data and backups live in `~/Library/Application Support/PODA-Public/` (not the Git repository). `./BACKUP_PODA.command` creates an AES-256-GCM encrypted `.podabkp` protected by its own Keychain key; `./RESTORE_PODA.command` restores one after explicit confirmation. A lost database Keychain key can make encrypted data unrecoverable—plan an offline key recovery strategy before relying on PODA for unique information.

`./reset_poda_memory.command` **deletes local memories and conversations** after an explicit confirmation; it does not remove program source, Ollama models, Notion/email accounts or the memory framework. Do not use it for normal upgrades. Do not commit exported plaintext backups or copy preexisting user databases into your public fork. Keep private development snapshots and release branches separate.

## Development and GitHub contributions

For a clean non-macOS source-level/test environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi==0.141.1 'uvicorn[standard]==0.53.0' httpx==0.28.1 requests==2.32.3 pydantic==2.13.5 python-dateutil==2.9.0.post0 cryptography==50.0.2 'numpy>=2,<3' pytest==9.1.1
python -m pytest -q tests
python tools/release_guard.py --check
```

The test fixture creates and deletes a **temporary synthetic database**. Some tests are skipped when macOS-only dependencies, Apple Mail or live Ollama are unavailable; a clean Linux run is **not** equivalent to full Mac integration verification. See [CONTRIBUTING.md](CONTRIBUTING.md) and `.github/workflows/ci.yml`.

Before every public push or ZIP export:

```bash
python3 tools/release_guard.py --check
./PACKAGE_PODA.command
```

The packager uses a *positive allowlist*, does not recursively archive the Git checkout and rejects binary database signatures and high-confidence generic secret patterns; use PODA_RELEASE_FORBIDDEN_MARKERS for private migration-specific checks. New authored file types must be explicitly reviewed. A release scanner cannot guarantee arbitrary secrets are absent; **review Git history too**. This ZIP intentionally has no `.git` history. When publishing, create a *new empty GitHub repo and an initial commit from these vetted source files*, never push the old private development repository/history.

**Support and responsible disclosure:** See [SECURITY.md](SECURITY.md). Open an issue for reproducible non-sensitive bugs, and never paste real inbox contents, personal database pages, full access tokens or unredacted logs in public issues.

## Code map

```text
poda_app/
  main.py                  app assembly, routes and lifecycle
  runtime/                 configuration, hardware, Ollama, model budgets, SQLCipher and live ledger
  memory/                  source and surface nodes, offline embedding queue, hybrid retrieval,
                           spatial layout, 3D scenes and memory transactions
  imports/                 secure ChatGPT/Claude export parsing, topic-cloud clustering and local personalization
  chat/                    streaming chat, scoped session context and grounded system prompt
  agent/                   file grants, code sandbox, tool orchestration, approval receipts,
                           CLI skill discovery and external-execution opt-in
  connectors/notion/       original source discovery, URL diagnostics, schema mapping, calendar view
  connectors/email/        Apple Mail + Gmail adapters, permissions, commitments and action receipts
  planner/                 explainable project priority ranking
  security/                loopback/browser session, Keychain, encryption, egress, redaction
  static/                  bundled CSS, HTML and JS; no remote fonts/CDNs

tests/                     synthetic unit/integration tests
tools/release_guard.py     fail-closed source release checker/packager
.github/workflows/ci.yml  CI source audit, Python tests and archive contents check
docs/                      architecture, threats, permissions, benchmarks and retention
```

**License:** MIT. See [LICENSE](LICENSE).
