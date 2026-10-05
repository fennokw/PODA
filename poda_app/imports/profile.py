"""Derive a bounded, user-authored personalization profile from imported history."""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from ..runtime import config, ollama

STOP = {"the","and","that","this","with","for","you","your","have","from","but","not","are","was","were","they","them","then","than","just","what","when","how","can","could","would","should","will","into","about","like","want","need","make","more","some","all","also","very","please","thanks","thank","there","here","been","being","because","really","much","which","while","does","did","doing","our","out","get","got","use","using"}


def _sample_user_text(conversations, max_chars: int = 120_000) -> str:
    chunks: list[str] = []
    used = 0
    # Bias recent conversations slightly by walking in provided order but cap each conversation.
    for conv in conversations:
        user = "\n".join(m.text for m in conv.messages if m.role == "user")[:5000]
        if not user:
            continue
        block = f"CONVERSATION: {conv.title}\n{user}\n"
        if used + len(block) > max_chars:
            break
        chunks.append(block); used += len(block)
    return "\n".join(chunks)


def _fallback(conversations) -> dict[str, Any]:
    text = _sample_user_text(conversations, 300_000)
    words = re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", text.lower())
    top = [w for w, _ in Counter(w for w in words if w not in STOP).most_common(30)]
    return {
        "summary": "Imported conversation history is available as local memory. No local model profile synthesis was available during import.",
        "communication_preferences": [],
        "workflow_preferences": [],
        "recurring_topics": top[:18],
        "response_preferences": [],
        "source": "deterministic-fallback",
    }


def derive(conversations) -> dict[str, Any]:
    sample = _sample_user_text(conversations)
    if not sample:
        return _fallback(conversations)
    prompt = """Analyze ONLY the USER-authored text below to create a personalization profile for a private local assistant.\nReturn ONLY valid JSON with keys: summary (<=5 sentences), communication_preferences (array), workflow_preferences (array), recurring_topics (array), response_preferences (array), confidence_notes (array).\nRules: do not infer sensitive attributes, protected traits, health conditions, politics, religion, sexuality, or diagnoses. Do not treat assistant-authored claims as user facts. Prefer explicit repeated preferences and work habits over speculation. State uncertainty in confidence_notes. This profile guides response style and workflow organization; it must never override PODA's truth/safety contract.\n\nUSER HISTORY:\n""" + sample
    try:
        models = ollama.installed_models(timeout=2.5)
        preferred = next((m for m in models if m.startswith(config.ADVANCED_MODEL)), None) or next((m for m in models if "qwen" in m.lower()), None) or (models[0] if models else None)
        if not preferred:
            return _fallback(conversations)
        resp = ollama.chat([{"role":"user","content":prompt}], preferred, options={"temperature":0.05,"num_ctx":32768,"num_predict":900}, think=False if preferred.startswith("qwen") else None, timeout=300)
        raw = (resp.get("message",{}).get("content") or "").strip()
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        if not isinstance(data, dict):
            raise ValueError("profile is not an object")
        data["source"] = f"local-model:{preferred}"
        return data
    except Exception:
        return _fallback(conversations)
