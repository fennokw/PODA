# PODA for Mac

PODA is a local-first assistant for chat, memory, mail, projects, and automations. This repository contains the full Apple Silicon desktop application.

## Install from source

1. Clone this repository or download and extract its source ZIP. Install Node.js 24 and Python 3.11 or newer. If Open3D requires libusb, install it with Homebrew using `brew install libusb`.
2. Run `./Install-PODA.command` from the source folder. If the ZIP extractor stripped the executable bit, run `bash Install-PODA.command`.
3. The command builds `PODA.app` from this folder, sets up the Python runtime, creates a PODA shortcut on your Desktop, and opens the app. Keep this folder in place so the shortcut can find the app. Rerun the command after pulling updates or editing the code.

PODA stores desktop data in `~/Library/Application Support/PODA-Public` and credentials in Keychain.

## Source layout

- `poda_app/` contains the application API, UI, agent, memory, and integrations.
- `desktop/` contains the Electron launcher, runtime setup, and packager.
- `tests/` contains the application regression tests.

A prebuilt Apple Silicon package is available in the [desktop workflow artifacts](https://github.com/fennokw/PODA/actions/workflows/desktop.yml). The app uses an ad hoc macOS signature. The separate browser edition is available in [PODA Web](https://github.com/fennokw/PODA-Web).

Daily Automation resolves email event dates against the original send time, then verifies authorized Notion additions. To use the same read-only scheduling tools from an MCP client, run `python -m poda_app.scheduling.mcp` over stdio from the source folder with the PODA Python environment.

Use **Personalize → Memory search with Hindsight** to install and enable the local search engine. Setup uses an installed GGUF chat model and the Nomic GGUF embedding model. Hindsight keeps a derived index in the app's data folder. **Add-ons** contains the downloadable program catalog and saved skills. Program tools require a separate trust opt-in and per-tool enable switch. **Developer mode** in the toolbar reveals technical diagnostics.

In Chat, choose **Attach files**, drop files into the chat, or paste a screenshot. Files are shared with that message only. PODA can inspect source ZIPs and text-based PDFs and return edited copies. A revised PDF uses PODA’s document layout. To read images, download Gemma 3 Vision or import a GGUF with its matching projector from **System → Model installer and profile selection**.

PODA packages its own llama.cpp engine. In **System**, download or import a GGUF and assign it to a model role. Install the Nomic embedding model for semantic recall and Hindsight. Advanced controls include speculative decoding, K/V cache precision, GPU offload, and Flash Attention. The first engine or model download needs an internet connection. Inference and OCR run locally afterward.

Uploaded PDFs use native text when available and local PP-OCR for scanned pages. Chat also reads DOCX and XLSX content and recognizes screenshot text before asking a visual model to interpret image details.

## Choose your calendar

Open **Calendar → Calendar setup** and select **one** active provider. Chat, remote
text commands, reminders, email event proposals and daily automation use it.
Switching never moves or deletes events. Reauthorize unattended calendar writes
in Messages and Daily Automation after changing provider or destination. Pending
proposals for a previous destination must be prepared again.

- **Notion** remains the default. Connect the original database, verify access,
  and save its field mapping. **New event defaults** lets you select existing Type,
  Commitment, Area, Status and Import Source options. PODA fills valid temporary
  categories and asks whether you want changes. Timed events without an end get a
  provisional one-hour duration. Edit details in chat, an authorized text prompt,
  or the Calendar event inspector.
- **PODA local:** click **Use PODA local calendar**. No account is needed. Events
  remain in PODA's local database with its configured encryption and work offline.
  This is the built-in PODA calendar, not Apple Calendar synchronization.
- **Google Calendar:** enable Calendar API in a Google Cloud project, configure
  OAuth consent (add your account as a test user while in Testing), create a
  **Desktop app** OAuth client and download its JSON. Upload that file in setup,
  click **Sign in with Google → Continue in Google**, then **Refresh calendars
  after sign-in**. Select one calendar, choose whether edits are allowed, and
  activate it. Sign-in uses the system browser, PKCE, and a five-minute loopback
  callback. Tokens stay in macOS Keychain or Windows Credential Manager, not
  prompts or SQLite. Google receives calendar API data; inference remains local.
  Google consent settings, especially Testing mode, may require reauthorization.
  Disconnect deletes local tokens; revoke access in your Google account too if
  desired.

### Notion Calendar visibility and dashboard setup

See **[BestNDS — Kaden's Notion dashboard setup](https://github.com/fennokw/BestNDS)**
for the dashboard integrating PODA and Kaden's other public repositories.

PODA and Notion Calendar must use the **same original database and date property**.
Add that database/view to Notion Calendar and compare its filters with PODA's
new-event defaults. PODA verifies saved properties; a successful API write cannot
prove that a filtered external calendar view displays a page.

For an older entry, select it in PODA and choose **Edit event details → Fill missing
calendar fields**. This fills empty supported category properties without replacing
existing values. Relation, formula and rollup values are not fabricated: choose
any required related page in Notion. Defaults never create new schema options and
remain scoped to the connected source.

### Desktop reopening and Memory performance

Closing the Mac window keeps PODA's background tasks alive. Reopening and waking
check the local server, recover a stopped child process, and reconnect the window.
Recovery is bounded and does not automatically repeat interrupted actions. Use
**Shut down PODA** or Quit to stop the app and its background work. A closed lid or
system sleep can suspend execution despite the app's idle-sleep prevention;
remote commands need the Mac powered on, awake, and connected.

Memory opens its saved geometry without invoking embeddings or semantic reflow.
The viewer pauses while hidden, restores graphics after context loss, and releases
workers/listeners/GPU resources when leaving. Large graphs use neighbor edges to
keep navigation responsive; explicit semantic reflow remains available.

Developer regression checks for the calendar and window lifecycle:

```sh
python -m pytest -q tests/test_calendar_providers.py tests/test_notion_connector.py tests/test_memory_loading.py
node --test tests/desktop-recovery.test.cjs
# DOM regression tests (jsdom is a test dependency, not shipped with PODA):
npm install --prefix /tmp/poda-ui-test jsdom
NODE_PATH=/tmp/poda-ui-test/node_modules node --test tests/frontend-calendar.test.cjs
```

### BestNDS mapping

For [Kaden’s BestNDS](https://github.com/fennokw/BestNDS), connect Schedule & Tasks and choose **Use Kaden's BestNDS** in the schema mapper. Calendar placement uses Date, all tasks support Completed; events do not, and event defaults are Committed / General. Custom property mapping and portable JSON import/export remain available. Apply the preset again after the dashboard migration to refresh the saved schema; remote permissions still apply.

### BestNDS task and event kinds

Type determines whether a Notion item is an event or task. Completed applies only to tasks, regardless of Task kind. Homework is only a label. Task kind applies only to tasks; Event kind and Commitment apply only to events. PODA ignores incompatible stored fields with a setup warning, offers only the matching kind in its editor, validates changes against the live schema, and clears the old type's mapped fields on an explicit mapped Type change. Canvas provenance never overrides an explicit Event.

After upgrading an older BestNDS database with Event kind, refresh the Notion schema and reapply **Use Kaden's BestNDS** before remote writes. The portable JSON and full option list are in [BestNDS](https://github.com/fennokw/BestNDS). Other schemas can map their kind properties separately. Notion's manual editor permits incompatible selections; BestNDS's Setup check formula flags them without hiding the calendar item.
