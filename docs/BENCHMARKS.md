# PODA local model benchmarking

**There are no benchmark results or hardware measurements bundled in the public repository.** The developer's private machine-specific benchmarks were deliberately excluded: public users must measure their *own* Apple Silicon Mac and their *own* installed Ollama models.

PODA chooses among installed Fast/Balanced/Deep profiles using `poda_app/runtime/models.py`. It measures `hw.memsize` (cached for a few minutes) and derives an initial context budget; if hardware discovery is unavailable it conservatively limits the profile to small contexts instead of inventing 24 GB. The model actually loaded and available context also depend on quantization, KV cache, GPU offloading, other apps, system memory pressure, and Ollama implementation.

## How to benchmark

1. Install Ollama with at least `llama3.2:3b`, `qwen3:14b` and `nomic-embed-text`. Confirm `ollama list` and verify memory on the System screen.
2. Run one model at a time. Warm it up, then compare multiple runs for prompt tokens per second, generation tokens per second, first-token latency, CPU/GPU memory pressure and actual context from Ollama's `/api/ps` endpoint.
3. Compare representative coding and organization tasks with each model, not just a synthetic arithmetic prompt. Use short/medium/long contexts and include retrieval-backed cases from the second brain.
4. Start with an 8K context. Raise it cautiously while watching swap use and interactive latency; a model advertising 256K is **not** evidence a small Mac can operate at 256K locally.
5. Confirm exact Qwen3.6 tags and model sizes from the locally installed Ollama catalog before requesting downloads. PODA should not silently install models or claim a tag exists merely because it appears in a configuration list.

Use System → Models → Benchmark (or `POST /system/benchmark` with an installed model and context value) to persist **your** local results. The database storing benchmarks is in the private app-support directory, not version control.

## Long-context architecture

PODA's primary long-horizon context strategy is *selective retrieval*, not infinitely growing raw message windows. Distilled surface memories, source-linked conversation pairs, lexical matching, text cosine embeddings, reference triggers and committed spatial relationships rank relevant context for each prompt. `memory_dive` retrieves complete source exchanges only when warranted. Pending embeddings are nonblocking if Ollama is offline; lexical and cached memory remain available until vectors refresh.
