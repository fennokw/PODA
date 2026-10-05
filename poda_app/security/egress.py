"""Per-connector egress allowlist, offline mode, and an auditable outbound-request log.

Only metadata is logged (service, method, host, path, status, sizes). Never bodies, never tokens.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import requests

from ..runtime import config
from ..runtime.db import connect, now_iso, rows


class EgressBlocked(RuntimeError):
    pass


def offline_mode() -> bool:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='offline_mode'").fetchone()
        return bool(row and str(row["value"]) in {"1", "true", "on"})
    finally:
        conn.close()


def _log(service: str, method: str, host: str, path: str, status: int | None, bytes_out: int, bytes_in: int,
         purpose: str | None, duration_ms: int, blocked: bool) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO egress_log (ts, service, method, host, path, status, bytes_out, bytes_in, purpose, duration_ms, blocked) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (now_iso(), service, method, host, path, status, bytes_out, bytes_in, purpose, duration_ms, 1 if blocked else 0),
        )
        conn.execute("DELETE FROM egress_log WHERE id NOT IN (SELECT id FROM egress_log ORDER BY id DESC LIMIT 2000)")
        conn.commit()
    finally:
        conn.close()


def guarded_request(service: str, method: str, url: str, *, headers: dict[str, str] | None = None,
                    json_body: Any = None, timeout: float = 25, purpose: str | None = None) -> requests.Response:
    """Perform an outbound HTTP request only if the host is allowlisted for `service` and offline mode is off."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    allowed = config.EGRESS_ALLOWLIST.get(service, set())
    # Never forward bearer tokens across a redirect or to a host not named in the per-service allowlist.
    # Deny non-TLS external URLs, userinfo, nonstandard ports and malformed destinations.
    valid = (parsed.scheme == ("http" if service == "ollama" else "https")
             and parsed.username is None and parsed.password is None
             and parsed.port in (None, 80 if service == "ollama" else 443)
             and not parsed.fragment and host in allowed)
    if not valid:
        _log(service, method, host, parsed.path, None, 0, 0, purpose, 0, True)
        raise EgressBlocked(f"Outbound host '{host}' is not allowlisted for connector '{service}'")
    if service != "ollama" and offline_mode():
        _log(service, method, host, parsed.path, None, 0, 0, purpose, 0, True)
        raise EgressBlocked("PODA is in offline mode; external connectors are disabled")
    started = time.perf_counter()
    bytes_out = len(requests.models.complexjson.dumps(json_body)) if json_body is not None else 0
    try:
        response = requests.request(method, url, headers=headers, json=json_body, timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        _log(service, method, host, parsed.path, None, bytes_out, 0, purpose, int((time.perf_counter() - started) * 1000), False)
        raise
    if 300 <= response.status_code < 400:
        # Do not retry or follow redirects: even a same-host redirect must be evaluated as a fresh request
        # by the caller so credentials never leak across unexpected locations.
        _log(service, method, host, parsed.path, response.status_code, bytes_out, 0, purpose,
             int((time.perf_counter() - started) * 1000), True)
        raise EgressBlocked(f"{service}: redirect denied (HTTP {response.status_code}); no credentials forwarded")
    _log(service, method, host, parsed.path, response.status_code, bytes_out, len(response.content or b""), purpose,
         int((time.perf_counter() - started) * 1000), False)
    return response


def recent_egress(limit: int = 100) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return rows(conn, "SELECT * FROM egress_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 2000)),))
    finally:
        conn.close()


def egress_summary() -> dict[str, Any]:
    conn = connect()
    try:
        by_host = rows(conn, "SELECT host, service, COUNT(*) AS requests, MAX(ts) AS last_ts, SUM(blocked) AS blocked FROM egress_log GROUP BY host, service ORDER BY requests DESC")
        return {"allowlist": {k: sorted(v) for k, v in config.EGRESS_ALLOWLIST.items()}, "offline_mode": offline_mode(), "hosts": by_host}
    finally:
        conn.close()
