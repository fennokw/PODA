"""Model registry and router. Selects only from models actually installed; context policy is derived from measured memory."""
from __future__ import annotations

import json
import re
import uuid
import time
from typing import Any

from . import config, inference
from . import hardware
from . import model_catalog
from .db import connect, now_iso, rows, get_setting, set_setting

PROFILE_NAMES = ("fast", "balanced", "deep", "vision", "fast_coding", "deep_coding")


def context_budget(unified_memory_gb: float) -> dict[str, int]:
    """Measured policy: Conservative initial profile, subject to real installed-model benchmarks on this Mac."""
    if unified_memory_gb >= 64:
        return {"fast": 32768, "balanced": 65536, "deep": 131072}
    if unified_memory_gb >= 36:
        return {"fast": 16384, "balanced": 32768, "deep": 65536}
    return {"fast": 8192, "balanced": 16384, "deep": 32768}


def selected_profiles() -> dict[str, str]:
    return {profile: get_setting("model_profile_" + profile) for profile in PROFILE_NAMES}


def set_profile(profile: str, model: str, installed: list[str]) -> None:
    if profile not in PROFILE_NAMES:
        raise ValueError("Unknown model profile.")
    if model:
        item = model_catalog.installed(model)
        if model not in installed or not item or item["role"] == "embedding":
            raise ValueError("Choose an installed GGUF chat model.")
        if profile == "vision" and not item.get("vision"):
            raise ValueError("The visual role requires a GGUF with its matching projector.")
    set_setting("model_profile_" + profile, model)


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
    if config.WINDOWS:
        from .windows_hardware import ram_bytes
        gb = ram_bytes() / 1024**3
        _MEMORY_CACHE = (now, gb)
        return gb
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
    installed = installed if installed is not None else inference.installed_models()
    inventory = {item["id"]: item for item in model_catalog.inventory()}
    mem = measured_memory_gb() if unified_memory_gb is None else float(unified_memory_gb)
    budget = context_budget(mem) if mem > 0 else {"fast": 4096, "balanced": 8192, "deep": 8192}
    limit = inference.settings()["context_limit"]
    assignments = selected_profiles()
    language_models = [name for name in installed if name != config.EMBED_MODEL
                       and inventory.get(name, {}).get("role") not in {"embedding", "vision"}]
    coding = sorted((name for name in language_models if inventory.get(name, {}).get("role") == "coding"),
                    key=lambda name: inventory[name]["size"])
    vision = [name for name in installed if inventory.get(name, {}).get("vision")]

    def pick(profile: str, preferred: str, candidates: list[str]) -> str | None:
        return next((name for name in [assignments[profile], preferred, *candidates] if name in candidates), None)

    defaults = {"fast": config.FAST_MODEL, "balanced": config.ADVANCED_MODEL, "deep": config.OPTIONAL_DEEP_MODEL}
    general = sorted(language_models, key=lambda name: inventory.get(name, {}).get("size", 0))
    selected = {profile: pick(profile, default, (list(reversed(general)) if profile == "deep" else general) or vision)
                for profile, default in defaults.items()}
    for role in defaults:
        if assignments[role] in vision:
            selected[role] = assignments[role]
    selected["vision"] = pick("vision", "", vision)
    selected["fast_coding"] = pick("fast_coding", "", coding) or (
        assignments["fast_coding"] if assignments["fast_coding"] in language_models else selected["fast"])
    selected["deep_coding"] = pick("deep_coding", "", list(reversed(coding))) or (
        assignments["deep_coding"] if assignments["deep_coding"] in language_models else selected["deep"])
    for role in ("fast_coding", "deep_coding"):
        if assignments[role] in language_models or assignments[role] in vision:
            selected[role] = assignments[role]
    levels = {"fast": "fast", "balanced": "balanced", "deep": "deep", "vision": "balanced",
              "fast_coding": "balanced", "deep_coding": "deep"}
    prediction = {"fast": 900, "balanced": 2200, "deep": 5200}
    return {
        "installed": installed,
        "profiles": {role: {
            "model": name, "num_ctx": min(limit, budget[levels[role]]), "num_predict": prediction[levels[role]],
            "temperature": .1, "think": bool(inventory.get(name, {}).get("thinking") and levels[role] != "fast"),
        } for role, name in selected.items()},
        "embedding": config.EMBED_MODEL,
        "embedding_installed": config.EMBED_MODEL in installed,
        "unified_memory_gb": None if config.WINDOWS else mem,
        "memory_measurement": "Measured hardware" if mem else "Conservative context limits",
    }


def complexity_score(message: str) -> int:
    words = set(re.findall(r"[a-z]+", message.lower()))
    score = 0
    for word in ["analyze", "prioritize", "plan", "debug", "complex", "strategy", "calendar", "email", "project", "rank", "optimize", "explain", "compare", "schedule", "refactor", "implement", "test", "patch", "schema"]:
        if word in words:
            score += 2
    score += min(6, len(message) // 350)
    if "```" in message:
        score += 3
    return score


_DATA_ACCESS = re.compile(r"\b(calendar|schedule|agenda|events?|class(es)?|lecture|recitation|assignment|homework|due|deadline|exam|quiz|notion|email|e-mail|inbox|mail|message from|unread|today|tonight|tomorrow|this week|next week|remind|plan my|prioriti)\b", re.I)


def needs_grounded_model(message: str) -> bool:
    """Questions that must be answered from live data (calendar, mail, Notion, deadlines) need the stronger tool-following model."""
    return bool(_DATA_ACCESS.search(message or ""))


_CODE = re.compile(r"\b(?:code|coding|debug|refactor|repository|script|software|python|javascript|typescript|html|css|rust|patch|unit tests?)\b", re.I)
_REASON = re.compile(r"\b(?:debug|diagnose|proof|prove|derive|tradeoffs?|refactor|optimi[sz]e|algorithm|architect(?:ure)?|root cause)\b", re.I)


def choose_profile(message: str, mode: str, thinking_level: str) -> str:
    score = complexity_score(message)
    live_account = re.search(r"\b(?:my|our|connected) (?:calendar|notion|inbox|e-?mails?|workload|projects?)\b", message, re.I)
    if _CODE.search(message) and not live_account:
        complex_code = bool(_REASON.search(message)) or score >= 6
        return "deep_coding" if mode == "deep" or (mode != "fast" and (thinking_level == "deep" or complex_code)) else "fast_coding"
    if mode == "fast":
        return "fast"
    if mode == "advanced":
        return "balanced"
    if mode == "deep":
        return "deep"
    if thinking_level == "deep":
        return "deep" if score >= 6 or _REASON.search(message) else "balanced"
    if score >= 4 or needs_grounded_model(message):
        return "balanced"
    return "fast"


def resolve(message: str, mode: str, thinking_level: str, reg: dict[str, Any] | None = None) -> dict[str, Any]:
    reg = reg or registry()
    profiles = reg["profiles"]
    profile_name = choose_profile(message, mode, thinking_level)
    if not profiles.get(profile_name, {}).get("model"):
        profile_name = next((role for role in ("balanced", "fast", "deep") if profiles[role].get("model")), "fast")
    profile = dict(profiles[profile_name])
    needs_reasoning = (thinking_level == "deep" or profile_name in {"deep", "deep_coding"}
                       or bool(_REASON.search(message)) or complexity_score(message) >= 6)
    profile["think"] = profile.get("think", False) and thinking_level != "fast" and needs_reasoning
    if thinking_level == "fast":
        profile["num_ctx"] = min(profile["num_ctx"], profiles["fast"]["num_ctx"])
        profile["num_predict"] = min(profile["num_predict"], 1200)
    elif profile["think"]:
        profile["num_predict"] = max(profile["num_predict"], 3200)
    profile["profile"] = profile_name
    profile["options"] = {key: profile[key] for key in ("temperature", "num_ctx", "num_predict")}
    if profile["think"]:
        deep = thinking_level == "deep" or profile_name in {"deep", "deep_coding"}
        profile["options"]["reasoning_budget"] = 768 if deep else 256
    return profile
