# PODA local harness / memory integration

The enhancements borrow *design patterns* from the cited projects; they do not
automatically install cloud-hosted services, third-party credentials, or unknown
executables.

## Project provenance / license review (October 2026)

| Upstream | Compatibility decision |
|---|---|
| [schedule-agent](https://github.com/addabjork/schedule-agent) (MIT) | Use evidence-first event extraction and confirmation flow. Do **not** transplant Claude, Twilio, Google Calendar, Google Maps, SendGrid or Railway services into a local-only product. |
| [open-schedule-agent](https://github.com/anthroos/open-schedule-agent) (MIT) | Adopt timezone-aware scheduling checks and an Ollama-compatible workflow. Never use its fail-open calendar outage fallback. |
| [meeting-mistro](https://github.com/Meeting-Mistro/meeting-mistro) (license not identified) | Follow general meeting-from-email design; no proprietary code is copied. |
| [ECC](https://github.com/affaan-m/ecc) (MIT) | User-selectable skills, routing and research-first programming. Use only permissioned, non-executable markdown as context. |
| [Cortex Suite](https://github.com/Artistsyn/cortex_suite) (license not identified) | Native static symbol/import mapping (`code_context`), hash-bound edits, tool receipts, testing and corrective feedback, **not** its complete Rust MCP binaries. |
| [Hindsight](https://github.com/vectorize-io/hindsight) (MIT) | Optional local Hindsight REST adapter, materialized index keyed to PODA node IDs, bidirectional delete/update reconciliation through PODA, reranked canonical recall and feedback-based exclusion. Full engine still requires optional local Hindsight installation and storage. |

## Hindsight architecture

Canonical: PODA encrypted `memory_nodes`, `memory_links`, and visible
user-editable memory tree. Optional: Hindsight local server on
`127.0.0.1:8888` using Ollama LLM/embedding endpoints. A stable
`document_id=poda-node-<node ID>` links derived Hindsight facts to their
source node. Hindsight cannot create or delete authoritative PODA nodes.
Background sync publishes only committed nodes and propagates removed PODA
nodes to the derived index. **The derived Hindsight service maintains its own
local storage**, so this is an optional local index, not one physically
combined SQLCipher database. Do not send sensitive memories until you
verify its local server's on-disk encryption and backup policy.

For truly local inference, configure the Hindsight engine with its
`ollama` provider and locally installed models. Follow the upstream
installation guide. Do not configure `openai`, Hindsight Cloud, or remote
URLs. The PODA adapter deliberately refuses non-loopback destinations.
PODA does not silently install packages, Docker images or background
processes. The Hindsight toggle is disabled until a local instance is
reachable. The sync runs incrementally and can also be triggered from
Personalize. If the local instance goes down, PODA degrades to its
existing FTS5, embedding cosine and geometry memory search.

Feedback can mark specific existing visible PODA memories `incorrect` or
`outdated`. They remain in the user's memory graph for transparent review
but are excluded from contextual recall and the derived Hindsight index;
`restore` re-enables them.

## Skills and harness upload security

Personalize accepts only Markdown/TXT or a ZIP containing exactly one
`SKILL.md` <= 24 KB. Contents are stored in the current encrypted PODA
database and inactive by default. Enabling a skill allows bounded,
low-priority reference text into prompts; it does **not** allow the
skill to override security, add tools, execute code, access a file system
or install untrusted packages. Trusted executable third-party harness
installation remains separately controlled by Files & Agent's existing
opt-in and per-folder permissions.

## Real model compatibility

The model catalog measures total Apple Silicon unified RAM with
`sysctl hw.memsize`; on Apple Silicon, GPU shared memory is not added
as extra VRAM. On machines with dedicated VRAM, VRAM and system RAM are
shown separately. Disk measurements use the actual location of PODA's
data directory when present. Estimated model weights, runtime buffer and
minimum disk headroom determine tentative compatibility; model tokens/s,
GPU-offload performance and thermal behavior require Mac-side testing.
All catalog entries are local Ollama families; no automatic downloads,
remote inference or license assumptions.

## Limitations

- GitHub Actions runs synthetic local tests; it cannot verify a user's
  actual Mac/Ollama performance, connected IMAP, Notion, Hindsight daemon
  or Rust toolchain.
- Hindsight cannot be claimed *fully active* until installed and
  configured locally; Cortex full MCP servers are not installed.
- The schedule/email extraction pipeline refuses unsupported, ambiguous
  or timezone-less events rather than falsely claiming they were booked.
