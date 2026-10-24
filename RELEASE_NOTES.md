# PODA v0.9.2-public — Multi-account, local models, skill context and memory bridge

- Multiple connected Mail inboxes per scheduled prompt, independent account-scoped send/edit permissions and backward compatibility with single-account prompts.
- System compares measured unified RAM, dedicated VRAM if present and disk against local general/coding/vision Ollama model and quantization requirements.
- Encrypted disabled-by-default Personalize skill uploads; external harnesses never auto-install or execute on upload.
- Source-code AST/structured declaration tool within existing folder grants, grounded test-receipt lessons and edit verification.
- Optional Ollama-backed local Hindsight server, node-ID synchronization with canonical PODA memory graph, explicit memory feedback and recall exclusion.
- Evidence-first meeting extraction demotes unsupported or timezone-less events.
- macOS/Ollama/Notion/IMAP and Hindsight live service must still be tested on actual hardware.

See `docs/LOCAL_AGENT_INTEGRATIONS.md` for upstream research and limitations.

---

# PODA v0.9.1-public — Daily Automation reliability patch

- Fixed the verifier's false encryption failure: the throwaway smoke-test database intentionally uses plaintext SQLite, while normal production remains SQLCipher-required.
- Run pytest once and preserve the failure traceback rather than masking failures.
- Permit a stale PODA server from the same checkout to stop cleanly during relaunch; never stop unrelated applications or other PODA installations.
- Isolated macOS-sandbox-independent process-runner regression testing and made imported topic-cloud assignment assertions robust to overlapping valid clouds.
- Removed the obsolete three-event calendar-per-run UI and legacy permission field; calendar actions remain individually permission checked without an arbitrary count limit.
- Added connected Mail account dropdowns to Daily Automation, including account selection for mail reads and account-scoped writes. No re-entry of existing credentials or account IDs.
- Email-plus-calendar prompts now use grounded mail search followed by continued tool planning; a missing calendar stage is marked incomplete instead of being reported as accomplished.
- Reduced Notion duplicate scans to the relevant date, prevented overlapping runs of the same daily automation, and moved macOS notification invocation off the event loop.
- Automated CI does not replace a live macOS Notion/Apple Mail end-to-end test.

---

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
