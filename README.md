# PODA Desktop for Mac

PODA is a local-first assistant. This repository builds the Apple Silicon desktop app. For the browser-hosted version and shared application source, use [PODA Web](https://github.com/fennokw/PODA-Web).

## Install

1. Download `PODA-macOS-arm64` from a successful [desktop workflow](https://github.com/fennokw/PODA/actions/workflows/desktop.yml). Extract both ZIP layers and move `PODA.app` to Applications.
2. Install Python 3.11 or newer and Ollama. Open PODA and select **Set Up Python Runtime** on first launch. Reopen the app when setup finishes.
3. Start Ollama. Use `ollama pull llama3.2:3b` and `ollama pull nomic-embed-text` to install starter chat and embedding models.

The build uses an ad hoc macOS signature. Existing desktop data stays in `~/Library/Application Support/PODA-Public` and its credentials stay in Keychain. Keep PODA running and the Mac awake for Daily Automation.

## Build

Install Node.js 24, Python, and Git. Run the following from the checkout.

```sh
cd desktop
npm ci --ignore-scripts
npm run package:mac
```

The output is `dist/desktop/PODA-darwin-arm64/PODA.app`. The build embeds the pinned PODA Web runtime behind a private backend launcher. The installed app needs no PODA Web checkout or separate server.
