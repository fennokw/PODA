# PODA v0.6.0-public — LLM-history personalization release

v0.6.0 builds on the hardened v0.5.1 public baseline and adds an opt-in, local-only way to initialize PODA from an existing ChatGPT or Claude data export.

## New: Personalize from an LLM data export

- New **Personalize** screen accepts the official provider ZIP directly in the local PODA UI.
- Secure ZIP handling rejects traversal paths, symbolic links, suspicious compression ratios, excessive file counts and oversized archives.
- ChatGPT `conversations.json` mapping exports and Claude conversation/chat-message exports are normalized into one internal representation.
- Every imported exchange becomes a visible, searchable second-brain memory with import provenance; imported assistant replies remain contextual rather than authoritative.
- Conversations are clustered into high-level semantic **topic clouds** with local Ollama embeddings. An offline lexical fallback still imports and organizes the archive when embeddings are unavailable.
- Existing surface memories continue to form inside those topic clouds, producing a manageable two-level hierarchy for large histories.
- Optional personalization derives workflow/communication/response preferences from **user-authored messages only**. The profile is stored locally, visible in the memory graph, injected into future system prompts as fallible preference context and never allowed to override the capability/truth contract.
- Future chats can route into matching imported topic clouds using their labels/tags.
- Archive fingerprints prevent accidental duplicate imports.
- The raw upload is deleted after parsing; PODA keeps only the normalized local memories/profile and non-secret import metadata.

## Public baseline retained

The v0.5.1 hardening remains: SQLCipher-required normal startup, isolated Keychain namespace, source-only packaging, no shipped memories, fail-closed execution sandbox policy, retired arbitrary-host IMAP preview, denied Notion redirects, offline-safe memory commits and hardware-derived model context budgets.

## Verification

The public regression suite now includes import parser, archive-safety, duplicate-import, visible-memory, personalization-prompt and frontend tests in addition to the existing connector, memory, agent and privacy tests. Run:

```bash
./VERIFY_PODA.command
python3 tools/release_guard.py --check
```
