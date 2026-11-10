# PODA for Mac

PODA is a local-first assistant for chat, memory, mail, projects, and automations. This repository contains the full Apple Silicon desktop application.

## Install from source

1. Clone this repository or download and extract its source ZIP. Install Node.js 24, Python 3.11 or newer, and Ollama for local models. If Open3D requires libusb, install it with Homebrew using `brew install libusb`.
2. Run `./Install-PODA.command` from the source folder. If the ZIP extractor stripped the executable bit, run `bash Install-PODA.command`.
3. The command builds `PODA.app` from this folder, sets up the Python runtime, creates a PODA shortcut on your Desktop, and opens the app. Keep this folder in place so the shortcut can find the app. Rerun the command after pulling updates or editing the code.

Start Ollama and run `ollama pull llama3.2:3b` and `ollama pull nomic-embed-text` for starter chat and embedding models. PODA stores desktop data in `~/Library/Application Support/PODA-Public` and credentials in Keychain.

## Source layout

- `poda_app/` contains the application API, UI, agent, memory, and integrations.
- `desktop/` contains the Electron launcher, runtime setup, and packager.
- `tests/` contains the application regression tests.

A prebuilt Apple Silicon package is available in the [desktop workflow artifacts](https://github.com/fennokw/PODA/actions/workflows/desktop.yml). The app uses an ad hoc macOS signature. The separate browser edition is available in [PODA Web](https://github.com/fennokw/PODA-Web).

Daily Automation resolves email event dates against the original send time, then verifies authorized Notion additions. To use the same read-only scheduling tools from an MCP client, run `python -m poda_app.scheduling.mcp` over stdio from the source folder with the PODA Python environment.
