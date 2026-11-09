# PODA Desktop and knowledge relationships

## Open PODA on an Apple Silicon Mac

1. Quit any running PODA server. If it was started from the source checkout, close its Terminal window or run that checkout's `STOP_PODA.command`.
2. Download the **PODA-macOS-arm64** artifact from the [desktop workflow](https://github.com/fennokw/PODA/actions/workflows/desktop.yml). Extract the inner ZIP and move **PODA.app** to Applications or the Desktop.
3. Open PODA. On first launch choose **Set Up Python Runtime**. The Terminal script needs Python 3.11 or newer and installs PODA's Python dependencies. Open PODA again after setup succeeds. Keep Ollama installed and running for chat, model-assisted drafts and embeddings.

The workflow builds an ad-hoc-signed development bundle. It does not have a Developer ID signature or Apple notarization. You can build the same app from a trusted checkout on your Mac:

```bash
cd desktop
npm ci --ignore-scripts
npm run package:mac
open ../dist/desktop/PODA-darwin-arm64/PODA.app
```

Building requires Node.js 24 and Python 3. The standalone app does not need Node.js installed separately. The source launcher is also available. Both entry points use the same encrypted `~/Library/Application Support/PODA-Public/` database and Keychain entries. The desktop runtime lives in its own directory underneath Application Support, so replacing the app does not remove state or reinstall dependencies when the requirements are unchanged.

Closing the window leaves PODA running in the Dock for Daily Automation. **Quit PODA** stops the server. The Mac must be awake for scheduled runs. A database-directory lease prevents the current desktop and source launchers from both running the scheduler against the same data. Desktop startup diagnostics are in `logs/desktop-server.log` under PODA's Application Support directory.

## How the relationship graph works

The model is based on [graph-memory-starter](https://github.com/Glitch-Cat-Club/graph-memory-starter) and its [knowledge-graph demonstration](https://www.youtube.com/watch?v=svvlT-nX6N8). PODA uses the existing memory notes as sources, with typed entities, alternate names, and directed, source-backed relationships stored in the same encrypted database. It resolves entity names with word boundaries and walks up to three links before the model answers. The source quote and any condition travel with each relationship. The walk works with Ollama offline. Normal lexical, semantic and optional Hindsight recall still cover the original notes and broader topics.

In **Memory Viewer**, select a saved memory point and open **Knowledge facts**, or open **Knowledge relationships → Find memories to review** to find active source notes that have no committed facts. Draft from the committed user text with Ollama, or add a fact manually. Review the subject, direction, condition and exact quote before selecting **Commit knowledge facts**. Search **Knowledge relationships** in the viewer or ask in Chat with memory enabled. Results open the source evidence even when the note is outside the rendered scene. The elapsed time beside the graph results measures the local SQL lookup. Old notes remain available as evidence for review. Graph assertions start empty on upgrade because a spatial or topical link does not establish a factual relationship.

Editing the source title or text invalidates its saved assertions. Deleting a source deletes its assertions. Marking a source incorrect or outdated excludes its assertions from recall and exploration until restored. Different sources can disagree and remain separately inspectable. Only the user can commit extracted facts. User-authored chat text can be a source after it becomes a visible memory, while assistant replies, generated summaries and automation run output are excluded.

## Agent workflow changes

Chat exposes a collapsible **Workflow activity** panel for model routing, recall, verified tool results and approval state. Finished replies keep a bounded activity history in the encrypted chat record. It contains operation names, counts, model and receipt IDs. After reopening PODA, choose a chat under **Recent chats** in the context sidebar to recover its proposals and receipts. A pending proposal remains available for review. Interrupted writes show the last recorded state for inspection.

After a proposal runs, **Continue original workflow** uses the original request and the receipts from its approval chain. It suppresses identical previously attempted writes and queues further writes for a separate approval. A failed test leaves a receipt and the option to continue with diagnosis. If Ollama rejects a planner model, PODA can retry that inference on another installed profile. The planner keeps a bounded window of complete tool rounds and uses coding error receipts from the active chat.

## Transcript review

The follow-up used all ten supplied transcripts and the graph-memory starter source. The table records the changes each episode informed.

| Source | Applied to PODA |
| --- | --- |
| [Day 1 · 1:10:11](https://www.youtube.com/watch?v=VrAT63NH9gQ&t=4211s) | Ordered activity grouped by prompt, available after a chat reopens. |
| [Day 2 · 3:01:10](https://www.youtube.com/watch?v=VntOH2k6DoE&t=10870s) | A fresh planning pass receives the original request, receipts and bounded recent context. |
| [Day 3 · 37:31](https://www.youtube.com/watch?v=aieSaeRVSe4&t=2251s) | Typed facts and graph conditions live separately from the memory viewer's spatial relationships. |
| [Day 4 · 28:34](https://www.youtube.com/watch?v=VwC5eLZk7U4&t=1714s) | Workflow stages retain their approval boundaries and continue from verified results. |
| [Day 5 · 52:43](https://www.youtube.com/watch?v=gujdNMn31Q8&t=3163s) | The trace records the planner route and any local model fallback. |
| [Day 6 · 1:17:10](https://www.youtube.com/watch?v=TVtjMypDeqE&t=4630s) | The application saves handoff state as it runs so later planning can reuse it. |
| [Day 7 · 48:46](https://www.youtube.com/watch?v=_90JXYlOY9Y&t=2926s) | Planning can switch installed Ollama models while tool permissions and receipts stay in PODA. |
| [Day 8 · 2:19:24](https://www.youtube.com/watch?v=YT6EKKbj2gk&t=8364s) | Regression checks cover repeated approvals, resumed writes and cross-session isolation. |
| [Day 9 · 12:41](https://www.youtube.com/watch?v=Gd7JFq8JsJc&t=761s) | Desktop builds use a locked install with lifecycle scripts disabled. The packaging lock uses patched versions of glob and minimatch. |
| [Knowledge graph demo · 1:47](https://www.youtube.com/watch?v=svvlT-nX6N8&t=107s) | Reproduced the refund policy → role → holder → March delegate chain in PODA's database with a founder decoy. SQL supplied Marcus Webb and the source quotes. |
