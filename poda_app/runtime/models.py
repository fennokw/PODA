"""Model registry and router. Selects only from models actually installed; context policy is derived from measured memory."""
from __future__ import annotations

import json
import re
import uuid
import time
import os
from typing import Any

from . import config, ollama
from . import hardware
from .db import connect, now_iso, rows

# Qwen3.6 tags from ollama.com/library/qwen3.6/tags (verified 2026-10-01) with on-disk sizes.
QWEN36_CANDIDATES = [
    {"tag": "qwen3.6:27b-coding", "disk_gb": 18.5, "kind": "coding", "arch": "dense 27B"},
    {"tag": "qwen3.6:27b-q4_K_M", "disk_gb": 17.0, "kind": "general", "arch": "dense 27B"},
    {"tag": "qwen3.6:27b", "disk_gb": 18.5, "kind": "general", "arch": "dense 27B"},
    {"tag": "qwen3.6:35b-a3b-coding", "disk_gb": 23.5, "kind": "coding", "arch": "MoE 35B / 3B active"},
    {"tag": "qwen3.6:35b-a3b", "disk_gb": 23.5, "kind": "general", "arch": "MoE 35B / 3B active"},
]


def context_budget(unified_memory_gb: float) -> dict[str, int]:
    """Measured policy: Conservative initial profile, subject to real installed-model benchmarks on this Mac."""
    if unified_memory_gb >= 64:
        return {"fast": 32768, "balanced": 65536, "deep": 131072}
    if unified_memory_gb >= 36:
        return {"fast": 16384, "balanced": 32768, "deep": 65536}
    return {"fast": 8192, "balanced": 16384, "deep": 32768}


def qwen36_assessment(unified_memory_gb: float, installed: list[str]) -> list[dict[str, Any]]:
    out = []
    # Reserve ~6 GB for macOS + PODA + browser + Ollama runtime, plus KV cache headroom.
    headroom_gb = 6.0
    for cand in QWEN36_CANDIDATES:
        usable = unified_memory_gb - headroom_gb
        fits_8k = cand["disk_gb"] + 1.2 <= usable
        fits_32k = cand["disk_gb"] + 4.5 <= usable
        out.append({**cand, "installed": any(m.startswith(cand["tag"]) for m in installed),
                    "memory_safe_8k": fits_8k, "memory_safe_32k": fits_32k,
                    "verdict": ("memory-safe" if fits_32k else ("tight: 8K context only, expect swapping under load" if fits_8k else "not memory-safe on this Mac"))})
    return out


def recorded_benchmarks() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return rows(conn, "SELECT * FROM model_benchmarks ORDER BY created_at DESC LIMIT 200")
    finally:
        conn.close()


def record_benchmark(result: dict[str, Any], hardware: str) -> None:
    conn = connect()
    try:
        conn.execute("INSERT INTO model_benchmarks (id, model, num_ctx, load_s, first_token_s, total_s, gen_tok_s, prompt_tok_s, eval_tokens, memory_bytes, hardware, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (str(uuid.uuid4()), result["model"], result["num_ctx"], result.get("load_s"), result.get("first_token_s"), result.get("total_s"), result.get("gen_tok_s"),
                      result.get("prompt_tok_s"), result.get("eval_tokens"), result.get("memory_bytes"), hardware, now_iso()))
        conn.commit()
    finally:
        conn.close()


_MEMORY_CACHE: tuple[float, float] | None = None


def measured_memory_gb() -> float:
    """One runtime source for UI, normal chat and tool planner. Never pretend 24 GB is measured."""
    global _MEMORY_CACHE
    now = time.monotonic()
    if _MEMORY_CACHE and now - _MEMORY_CACHE[0] < 300:
        return _MEMORY_CACHE[1]
    raw = hardware._sysctl("hw.memsize")
    try:
        gb = int(raw or 0) / 1024**3
    except ValueError:
        gb = 0.0
    if gb <= 0:
        # On non-macOS test machines, avoid promising performance from an invented hardware size.
        gb = 0.0
    _MEMORY_CACHE = (now, gb)
    return gb


def registry(installed: list[str] | None = None, unified_memory_gb: float | None = None) -> dict[str, Any]:
    installed = installed if installed is not None else (ollama.status().get("models") or [])
    mem = measured_memory_gb() if unified_memory_gb is None else float(unified_memory_gb)
    budget = context_budget(mem)
    # Safe fallback when hardware detection is unavailable: small context only.
    if mem <= 0:
        budget = {"fast": 4096, "balanced": 8192, "deep": 8192}

    def pick(preferred: str, fallbacks: list[str]) -> str | None:
        for candidate in [preferred, *fallbacks]:
            if any(m == candidate or m.split(":")[0] == candidate.split(":")[0] and m.startswith(candidate) for m in installed):
                return candidate
        return None

    fast = pick(config.FAST_MODEL, ["llama3.2:3b", "qwen3:4b", "qwen3:8b"])
    balanced = pick(config.ADVANCED_MODEL, ["qwen3:14b", "qwen3:8b", "llama3.2:3b"])
    deep = pick(config.OPTIONAL_DEEP_MODEL, ["qwen3.6:27b-coding", "qwen3:14b"])
    return {
        "installed": installed,
        "profiles": {
            "fast": {"model": fast, "num_ctx": budget["fast"], "num_predict": 900, "temperature": 0.08, "think": False},
            "balanced": {"model": balanced, "num_ctx": budget["balanced"], "num_predict": 1600, "temperature": 0.09, "think": True},
            "deep": {"model": deep, "num_ctx": budget["deep"], "num_predict": 3000, "temperature": 0.10, "think": True},
        },
        "embedding": config.EMBED_MODEL,
        "embedding_installed": any(m.startswith(config.EMBED_MODEL) for m in installed),
        "qwen36": qwen36_assessment(mem, installed),
        "unified_memory_gb": mem,
        "memory_measurement": "sysctl hw.memsize" if mem > 0 else "unavailable; conservative context limits",
    }


def complexity_score(message: str) -> int:
    text = message.lower()
    score = 0
    for word in ["analyze", "prioritize", "plan", "debug", "complex", "strategy", "calendar", "email", "project", "rank", "optimize", "explain", "compare", "schedule", "refactor", "implement", "test", "patch", "schema"]:
        if word in text:
            score += 2
    score += min(6, len(message) // 350)
    if "```" in message:
        score += 3
    return score


_DATA_ACCESS = re.compile(r"\b(calendar|schedule|agenda|events?|class(es)?|lecture|recitation|assignment|homework|due|deadline|exam|quiz|notion|email|e-mail|inbox|mail|message from|unread|today|tonight|tomorrow|this week|next week|remind|plan my|prioriti)\b", re.I)


def needs_grounded_model(message: str) -> bool:
    """Questions that must be answered from live data (calendar, mail, Notion, deadlines) need the stronger tool-following model."""
    return bool(_DATA_ACCESS.search(message or ""))


def choose_profile(message: str, mode: str, thinking_level: str) -> str:
    if mode == "fast":
        return "fast"
    if mode == "advanced":
        return "balanced"
    if mode == "deep":
        return "deep"
    score = complexity_score(message)
    if thinking_level == "deep" and score >= 2:
        return "deep" if score >= 6 else "balanced"
    if score >= 4 or needs_grounded_model(message):
        return "balanced"
    return "fast"


def resolve(message: str, mode: str, thinking_level: str, reg: dict[str, Any] | None = None) -> dict[str, Any]:
    reg = reg or registry()
    profile_name = choose_profile(message, mode, thinking_level)
    profile = dict(reg["profiles"][profile_name])
    # Thinking level adjusts the context/prediction budget without changing which models are available.
    if thinking_level == "fast":
        profile["num_ctx"] = min(profile["num_ctx"], reg["profiles"]["fast"]["num_ctx"])
        profile["num_predict"] = min(profile["num_predict"], 1200)
    elif thinking_level == "deep":
        profile["num_predict"] = max(profile["num_predict"], 2400)
    if not profile.get("model"):
        for fallback in ("balanced", "fast", "deep"):
            if reg["profiles"][fallback].get("model"):
                profile = dict(reg["profiles"][fallback])
                profile_name = fallback
                break
    profile["profile"] = profile_name
    profile["options"] = {"temperature": profile["temperature"], "num_ctx": profile["num_ctx"], "num_predict": profile["num_predict"]}
    return profile
