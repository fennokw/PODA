"""Notion HTTP client: egress-guarded, Keychain-backed token, typed error taxonomy, bounded retries."""
from __future__ import annotations

import time
from typing import Any

from ...runtime import config
from ...security import egress, keychain

_sleep = time.sleep  # patched in tests
MAX_ATTEMPTS = 3


class NotionError(RuntimeError):
    """kind ∈ invalid_token | restricted | not_found | rate_limited | validation | server | network | offline | no_token"""

    def __init__(self, kind: str, message: str, status: int | None = None, code: str | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.status = status
        self.code = code
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "message": self.message, "status": self.status, "code": self.code}


def stored_token() -> str | None:
    return keychain.keychain_get(config.NOTION_KEYCHAIN_SERVICE, config.NOTION_KEYCHAIN_ACCOUNT)


def _classify(status: int, code: str | None, message: str, retry_after: float | None) -> NotionError:
    if status == 401:
        return NotionError("invalid_token", "Notion rejected the token (401 unauthorized). It may be revoked, mistyped, or belong to a deleted integration.", status, code)
    if status == 403:
        return NotionError("restricted", f"Notion refused access (403 {code or 'restricted_resource'}): {message}", status, code)
    if status == 404:
        return NotionError("not_found", message or "Notion returned 404 object_not_found.", status, code or "object_not_found")
    if status == 429:
        return NotionError("rate_limited", "Notion rate limit reached (429).", status, code, retry_after)
    if status == 400:
        return NotionError("validation", f"Notion validation error: {message}", status, code or "validation_error")
    if status >= 500:
        return NotionError("server", f"Notion server error {status}: {message}", status, code)
    return NotionError("server", f"Notion HTTP {status}: {message}", status, code)


class NotionClient:
    def __init__(self, token: str | None = None):
        self._token = token or stored_token()
        if not self._token:
            raise NotionError("no_token", "No Notion token is stored in macOS Keychain. Validate a token first.")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Notion-Version": config.NOTION_VERSION, "Content-Type": "application/json"}

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, *, purpose: str | None = None, timeout: float = 25) -> dict[str, Any]:
        url = f"{config.NOTION_API_BASE}{path}"
        last: NotionError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = egress.guarded_request("notion", method, url, headers=self._headers(), json_body=payload, timeout=timeout, purpose=purpose or path)
            except egress.EgressBlocked as exc:
                raise NotionError("offline", str(exc)) from exc
            except Exception as exc:  # requests.RequestException and friends
                last = NotionError("network", f"Could not reach api.notion.com: {exc.__class__.__name__}")
                if attempt < MAX_ATTEMPTS:
                    _sleep(min(2.0, 0.5 * attempt))
                    continue
                raise last from exc
            try:
                data = response.json()
            except Exception:
                data = {"message": (getattr(response, "text", "") or "")[:1000]}
            status = int(getattr(response, "status_code", 0) or 0)
            if 200 <= status < 300:
                return data if isinstance(data, dict) else {"result": data}
            retry_after = None
            try:
                retry_after = float((getattr(response, "headers", {}) or {}).get("Retry-After", 0)) or None
            except Exception:
                retry_after = None
            err = _classify(status, data.get("code") if isinstance(data, dict) else None, (data.get("message") if isinstance(data, dict) else "") or "", retry_after)
            if err.kind in {"rate_limited", "server"} and attempt < MAX_ATTEMPTS:
                _sleep(min(10.0, retry_after or (0.8 * attempt)))
                last = err
                continue
            raise err
        raise last or NotionError("network", "Notion request failed")

    # Convenience wrappers -------------------------------------------------------------
    def me(self) -> dict[str, Any]:
        return self.request("GET", "/users/me", purpose="identity")

    def database(self, database_id: str) -> dict[str, Any]:
        return self.request("GET", f"/databases/{database_id}", purpose="database")

    def data_source(self, data_source_id: str) -> dict[str, Any]:
        return self.request("GET", f"/data_sources/{data_source_id}", purpose="data_source")

    def page(self, page_id: str) -> dict[str, Any]:
        return self.request("GET", f"/pages/{page_id}", purpose="page")

    def query(self, data_source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/data_sources/{data_source_id}/query", payload, purpose="query")

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/search", payload, purpose="search")

    def create_page(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/pages", payload, purpose="create_page")

    def update_page(self, page_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("PATCH", f"/pages/{page_id}", payload, purpose="update_page")


def rich_plain(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join(str(part.get("plain_text") or (part.get("text") or {}).get("content") or "") for part in value if isinstance(part, dict)).strip()


def identity_summary(me: dict[str, Any]) -> dict[str, Any]:
    bot = me.get("bot") or {}
    owner = bot.get("owner") or {}
    return {"bot_id": me.get("id"), "bot_name": me.get("name"), "workspace_id": bot.get("workspace_id"),
            "workspace_name": bot.get("workspace_name"), "owner_type": owner.get("type")}
