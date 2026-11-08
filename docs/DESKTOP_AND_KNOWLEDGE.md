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

In **Memory Viewer**, select a saved memory point and open **Knowledge facts**. Draft from the committed user text with Ollama, or add a fact manually. Review the subject, direction, condition and exact quote before selecting **Commit knowledge facts**. Search **Knowledge relationships** in the viewer or ask in Chat with memory enabled. Results open the source note. Old notes remain available as evidence for review. Graph assertions start empty on upgrade because a spatial or topical link does not establish a factual relationship.

Editing the source title or text invalidates its saved assertions. Deleting a source deletes its assertions. Marking a source incorrect or outdated excludes its assertions from recall and exploration until restored. Different sources can disagree and remain separately inspectable. Only the user can commit extracted facts. User-authored chat text can be a source after it becomes a visible memory, while assistant replies, generated summaries and automation run output are excluded.

## Agent workflow changes

Chat exposes a collapsible **Workflow activity** panel for model routing, recall, verified tool results and approval state. These entries describe actions the application observed. The planner keeps a bounded window of complete tool rounds. After a proposal runs, **Continue original workflow** starts from its original request and receipts. Exact previously attempted actions are suppressed during continuation. Further writes appear as a new proposal. A failed test gives you a receipt and a continuation path for diagnosis and repair.

The design draws on the creator's published JARVIS episode descriptions for staged routing, prompt-scoped traces and context limits. Full transcripts for the supplied series were unavailable during implementation, so the workflow scope reflects the published descriptions and source code.
