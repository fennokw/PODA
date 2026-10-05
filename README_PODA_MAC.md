# PODA for macOS

The complete introduction, security notes, installation instructions, Notion and email guides, and developer setup are maintained in [README.md](README.md).

Quick start on a supported Apple Silicon Mac after installing Python 3.11+, Ollama and native dependencies:

```bash
chmod +x *.command
./INSTALL_PODA.command
./start_poda.command
```

PODA v0.5.1-public starts with an empty encrypted database **outside the checkout** in `~/Library/Application Support/PODA-Public/`. Do not copy your private PODA database into this public installation.
