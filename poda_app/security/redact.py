"""Redaction helpers so tokens and secrets never reach logs, receipts, or the model."""
from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"\b(ntn_[A-Za-z0-9]{10,})"),
    re.compile(r"\b(secret_[A-Za-z0-9]{10,})"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"\b(sk-[A-Za-z0-9]{10,})"),
    re.compile(r"\b(x'[0-9a-fA-F]{64}')"),
]
_SECRET_KEYS = {"token", "password", "app_password", "secret", "key", "authorization", "api_key"}


def redact_text(text: str) -> str:
    out = text or ""
    for pattern in _PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) if m.lastindex and m.group(1).lower().startswith("bearer") else "") + "[REDACTED]", out)
    return out


def redact_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if str(k).lower() in _SECRET_KEYS else redact_obj(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
