"""Email connector state exposed to the Capability Ledger.

Contract: capability(integration_row) -> dict with at least:
  persistent_connection, providers:[...], can_read, can_search, can_modify_mailbox, can_send, can_draft,
  accounts:[{id, provider, account_name, address_masked, enabled, allow_modify, allow_send, last_success_at, last_error}],
  apple_mail:{available, authorized, running, error}, detail, last_success_at
"""
from __future__ import annotations

from typing import Any

from ...runtime.db import connect, now_iso, rows, one

_APPLE_STATUS_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


def ensure_tables(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS email_accounts (
        id TEXT PRIMARY KEY, provider TEXT NOT NULL, account_name TEXT NOT NULL, address_masked TEXT, address_hash TEXT,
        address TEXT, provider_type TEXT, enabled INTEGER DEFAULT 1, allow_modify INTEGER DEFAULT 0, allow_send INTEGER DEFAULT 0,
        created_at TEXT NOT NULL, last_success_at TEXT, last_error TEXT)""")


def list_accounts() -> list[dict[str, Any]]:
    conn = connect()
    try:
        ensure_tables(conn)
        return rows(conn, "SELECT * FROM email_accounts WHERE enabled=1 ORDER BY created_at ASC")
    finally:
        conn.close()


def public_account(a: dict[str, Any]) -> dict[str, Any]:
    return {"id": a["id"], "provider": a["provider"], "account_name": a["account_name"], "address_masked": a.get("address_masked") or "",
            "provider_type": a.get("provider_type"), "enabled": bool(a.get("enabled", 1)), "allow_modify": bool(a.get("allow_modify")),
            "allow_send": bool(a.get("allow_send")), "last_success_at": a.get("last_success_at"), "last_error": a.get("last_error")}


def apple_status_cached(max_age: float = 20.0) -> dict[str, Any]:
    import time
    if _APPLE_STATUS_CACHE["value"] is not None and time.time() - _APPLE_STATUS_CACHE["at"] < max_age:
        return _APPLE_STATUS_CACHE["value"]
    try:
        from . import apple_mail
        value = apple_mail.status(auto_launch=False)
    except Exception as exc:  # never let the ledger fail because of Mail
        value = {"available": False, "authorized": None, "running": False, "accounts_detected": [], "error": str(exc)}
    _APPLE_STATUS_CACHE.update({"at": time.time(), "value": value})
    return value


def invalidate_cache() -> None:
    _APPLE_STATUS_CACHE["value"] = None


def capability(integration: dict[str, Any] | None = None) -> dict[str, Any]:
    integration = integration or {}
    accounts = [public_account(a) for a in list_accounts()]
    providers = sorted({a["provider"] for a in accounts})
    apple = apple_status_cached() if any(a["provider"] == "apple_mail" for a in accounts) else {"available": True, "authorized": None, "running": None, "error": None}
    apple_ok = not any(a["provider"] == "apple_mail" for a in accounts) or (apple.get("authorized") is not False)
    readable = [a for a in accounts if a["provider"] != "apple_mail" or apple_ok]
    can_read = bool(readable)
    can_modify = any(a["allow_modify"] for a in readable)
    can_send = any(a["allow_send"] for a in readable)
    if accounts:
        parts = [f"{a['account_name']} ({a['provider'].replace('_', ' ')}; {'read+modify' if a['allow_modify'] else 'read-only'}{'+send' if a['allow_send'] else ''})" for a in accounts]
        detail = "Mail connected: " + "; ".join(parts) + ". Bodies are treated as untrusted data; nothing is stored in memory."
        if not apple_ok:
            detail += " Apple Mail automation is currently DENIED, so those accounts are unreachable until permission is restored."
    else:
        detail = "No mail account is connected. Connect Apple Mail or Gmail in the Mail screen; the IMAP preview tool still works with request-scoped credentials."
    last_success = max([a["last_success_at"] or "" for a in accounts] + [integration.get("last_success_at") or ""]) or None
    return {
        "persistent_connection": bool(accounts),
        "providers": providers,
        "accounts": accounts,
        "connected_account": accounts[0]["address_masked"] if accounts else None,
        "last_successful_preview_account": integration.get("account_identifier"),
        "last_success_at": last_success,
        "imap_preview_tool": True,
        "credential_storage": "macOS Keychain (Gmail app password)" if "gmail_imap" in providers else False,
        "can_read": can_read,
        "can_search": can_read,
        "can_read_without_credentials_in_request": can_read,
        "can_draft": can_modify,
        "can_send": can_send,
        "can_modify_mailbox": can_modify,
        "apple_mail": {k: apple.get(k) for k in ("available", "authorized", "running", "error")},
        "detail": detail,
    }
