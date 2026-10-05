#!/bin/bash
set -euo pipefail
echo "PODA Ollama model installer"
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed. Opening https://ollama.com/download/mac"; open "https://ollama.com/download/mac" || true; exit 1
fi
curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || { echo "Starting Ollama..."; (ollama serve >/tmp/poda-ollama.log 2>&1 &); sleep 3; }
MEM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "Detected unified memory: ${MEM_GB} GB"
for m in llama3.2:3b qwen3:14b nomic-embed-text; do echo "Pulling $m"; ollama pull "$m"; done
echo
echo "Qwen3.6 (measured guidance for a ${MEM_GB} GB Mac):"
if [ "$MEM_GB" -ge 48 ]; then
  echo "  qwen3.6:27b-coding (~18.5 GB) and qwen3.6:35b-a3b-coding (~23.5 GB) are memory-safe here."
  read -r -p "Pull qwen3.6:27b-coding now? [y/N]: " A; case "${A:-N}" in y|Y) ollama pull qwen3.6:27b-coding;; esac
elif [ "$MEM_GB" -ge 36 ]; then
  echo "  qwen3.6:27b-coding (~18.5 GB) fits with a 16K-32K context. 35B-A3B is not recommended."
  read -r -p "Pull qwen3.6:27b-coding now? [y/N]: " A; case "${A:-N}" in y|Y) ollama pull qwen3.6:27b-coding;; esac
else
  echo "  NOT memory-safe on ${MEM_GB} GB: qwen3.6:27b-* (17-19 GB weights + KV cache) and qwen3.6:35b-a3b-* (23-24 GB)."
  echo "  PODA defaults to qwen3:14b for advanced requests. Benchmark actual context size and speed on your own Mac."
  echo "  You can still pull one manually (ollama pull qwen3.6:27b-q4_K_M) and PODA will offer it in System → Models with a memory warning."
fi
echo "Done."
