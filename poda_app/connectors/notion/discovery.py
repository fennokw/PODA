"""Token validation and data-source discovery via the current Notion search API."""
from __future__ import annotations

from typing import Any

from ...runtime import config
from ...security import keychain
from .client import NotionClient, NotionError, identity_summary, rich_plain


def validate_token(token: str, store: bool = True) -> dict[str, Any]:
    """Validate with GET /users/me. The token is written to Keychain only after Notion accepts it."""
    token = (token or "").strip()
    if not token:
        raise NotionError("validation", "Enter a Notion internal integration token (starts with ntn_ or secret_).")
    client = NotionClient(token)
    me = client.me()
    if me.get("type") != "bot":
        raise NotionError("validation", "That token is not an integration (bot) token.")
    summary = identity_summary(me)
    if store:
        keychain.keychain_set(config.NOTION_KEYCHAIN_SERVICE, config.NOTION_KEYCHAIN_ACCOUNT, token)
    return {"valid": True, **summary}


def _summarize_data_source(item: dict[str, Any], database_titles: dict[str, str], client: NotionClient | None) -> dict[str, Any]:
    parent = item.get("parent") or {}
    database_id = parent.get("database_id") if parent.get("type") == "database_id" else None
    database_title = None
    if database_id:
        if database_id not in database_titles and client is not None:
            try:
                database_titles[database_id] = rich_plain(client.database(database_id).get("title")) or "Untitled database"
            except NotionError:
                database_titles[database_id] = "(database not readable)"
        database_title = database_titles.get(database_id)
    return {
        "id": item.get("id"),
        "name": rich_plain(item.get("title")) or item.get("name") or "Untitled data source",
        "database_id": database_id,
        "database_title": database_title,
        "parent": parent,
        "url": item.get("url"),
        "last_edited_time": item.get("last_edited_time"),
        "in_trash": bool(item.get("in_trash") or item.get("archived")),
    }


def list_data_sources(query: str | None = None, cursor: str | None = None, page_size: int = 50, client: NotionClient | None = None,
                      resolve_database_titles: bool = True) -> dict[str, Any]:
    client = client or NotionClient()
    payload: dict[str, Any] = {"filter": {"property": "object", "value": "data_source"}, "page_size": max(1, min(page_size, 100)),
                               "sort": {"direction": "descending", "timestamp": "last_edited_time"}}
    if query:
        payload["query"] = query
    if cursor:
        payload["start_cursor"] = cursor
    data = client.search(payload)
    titles: dict[str, str] = {}
    items = [_summarize_data_source(item, titles, client if resolve_database_titles else None)
             for item in data.get("results") or [] if isinstance(item, dict) and item.get("object") == "data_source"]
    status = data.get("request_status") or {}
    return {"data_sources": items, "has_more": bool(data.get("has_more")), "next_cursor": data.get("next_cursor"),
            "request_status": status, "incomplete": status.get("type") == "incomplete",
            "note": ("Search only lists data sources already shared with the PODA connection. If the one you want is missing, share its ORIGINAL database with PODA first."
                     + (" Notion reported this search as incomplete; retry or narrow the query." if status.get("type") == "incomplete" else ""))}


def all_data_sources(client: NotionClient | None = None, max_pages: int = 10) -> list[dict[str, Any]]:
    client = client or NotionClient()
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(max_pages):
        page = list_data_sources(cursor=cursor, page_size=100, client=client, resolve_database_titles=False)
        out.extend(page["data_sources"])
        if not page["has_more"] or not page["next_cursor"]:
            break
        cursor = page["next_cursor"]
    return out
