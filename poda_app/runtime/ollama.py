"""Local Ollama client. Loopback only; no cloud fallback exists anywhere in PODA."""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Iterable

import httpx
import requests

from . import config


class OllamaError(RuntimeError):
    pass


def tags(timeout: float = 4.0) -> dict[str, Any]:
    try:
        r = requests.get(config.OLLAMA_TAGS_URL, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        raise OllamaError(str(exc)) from exc


def installed_models(timeout: float = 2.5) -> list[str]:
    data = tags(timeout)
    names = []
    for item in data.get("models", []):
        name = item.get("name") or item.get("model")
        if name:
            names.append(name)
    return names


def status() -> dict[str, Any]:
    try:
        version = None
        try:
            version = requests.get(f"{config.OLLAMA_BASE}/api/version", timeout=2).json().get("version")
        except Exception:
            pass
        return {"reachable": True, "models": installed_models(), "version": version}
    except Exception as exc:
        return {"reachable": False, "models": [], "error": str(exc)}


def ps() -> list[dict[str, Any]]:
    try:
        return requests.get(config.OLLAMA_PS_URL, timeout=3).json().get("models", [])
    except Exception:
        return []


def show(model: str) -> dict[str, Any]:
    try:
        r = requests.post(config.OLLAMA_SHOW_URL, json={"model": model}, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        raise OllamaError(str(exc)) from exc


def model_supports_tools(model: str) -> bool:
    try:
        info = show(model)
    except OllamaError:
        return False
    caps = info.get("capabilities") or []
    if caps:
        return "tools" in caps
    template = (info.get("template") or "").lower()
    return "tool" in template


def embed(texts: list[str], model: str | None = None, timeout: float = 180) -> list[list[float]]:
    if not texts:
        return []
    response = requests.post(
        config.OLLAMA_EMBED_URL,
        json={"model": model or config.EMBED_MODEL, "input": texts, "truncate": True, "keep_alive": "30m"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    vectors = data.get("embeddings")
    if vectors is None and data.get("embedding") is not None:
        vectors = [data["embedding"]]
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise OllamaError(f"Unexpected Ollama embedding response for {len(texts)} inputs")
    return [[float(v) for v in vec] for vec in vectors]


def chat(messages: list[dict[str, Any]], model: str, options: dict[str, Any] | None = None,
         tools: list[dict[str, Any]] | None = None, timeout: float = 600, think: bool | None = None) -> dict[str, Any]:
    """Non-streaming chat; used for tool-selection and classification turns."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "keep_alive": "30m", "options": options or {}}
    if tools:
        payload["tools"] = tools
    if think is not None:
        payload["think"] = think
    r = requests.post(config.OLLAMA_CHAT_URL, json=payload, timeout=timeout)
    if not r.ok:
        raise OllamaError(f"Ollama {r.status_code}: {r.text[:400]}")
    return r.json()


async def chat_stream(messages: list[dict[str, Any]], model: str, options: dict[str, Any] | None = None,
                      tools: list[dict[str, Any]] | None = None, think: bool | None = None,
                      timeout: float = 900) -> AsyncIterator[dict[str, Any]]:
    """Async streaming chat yielding raw Ollama chunks. Cancelling the iterator closes the socket."""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "keep_alive": "30m", "options": options or {}}
    if tools:
        payload["tools"] = tools
    if think is not None:
        payload["think"] = think
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        async with client.stream("POST", config.OLLAMA_CHAT_URL, json=payload) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise OllamaError(f"Ollama {response.status_code}: {body[:400].decode('utf-8', 'replace')}")
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def benchmark(model: str, num_ctx: int, prompt: str | None = None, num_predict: int = 200) -> dict[str, Any]:
    """Measure real load time, first-token latency, tokens/sec, and resident memory on this Mac."""
    prompt = prompt or "In exactly three short bullet points, explain how a hash map handles collisions."
    t0 = time.perf_counter()
    first = None
    final: dict[str, Any] = {}
    with requests.post(config.OLLAMA_CHAT_URL, json={
        "model": model, "messages": [{"role": "user", "content": prompt}], "stream": True, "keep_alive": "2m",
        "options": {"num_ctx": num_ctx, "num_predict": num_predict, "temperature": 0.1},
    }, stream=True, timeout=900) as r:
        if not r.ok:
            raise OllamaError(f"Ollama {r.status_code}: {r.text[:300]}")
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            if first is None and (chunk.get("message", {}).get("content") or chunk.get("message", {}).get("thinking")):
                first = time.perf_counter() - t0
            if chunk.get("done"):
                final = chunk
    total = time.perf_counter() - t0
    eval_count = final.get("eval_count") or 0
    eval_dur = final.get("eval_duration") or 0
    prompt_count = final.get("prompt_eval_count") or 0
    prompt_dur = final.get("prompt_eval_duration") or 0
    resident = next((m for m in ps() if (m.get("name") == model or m.get("model") == model)), {})
    return {
        "model": model, "num_ctx": num_ctx,
        "load_s": round((final.get("load_duration") or 0) / 1e9, 2),
        "first_token_s": round(first or 0.0, 2), "total_s": round(total, 2),
        "gen_tok_s": round(eval_count / (eval_dur / 1e9), 1) if eval_count and eval_dur else None,
        "prompt_tok_s": round(prompt_count / (prompt_dur / 1e9), 1) if prompt_count and prompt_dur else None,
        "eval_tokens": eval_count, "memory_bytes": resident.get("size_vram") or resident.get("size"),
        "context_reported": resident.get("context_length"),
    }
