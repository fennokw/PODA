"""Structured Notion reference parsing.

A copied Notion URL can carry several IDs at once: the database/page ID in the path, a view ID in
`?v=`, and a block ID in the `#fragment`. The v0.3.11 parser used a regex and took the *last* match,
which silently selects the view ID on URLs like `/ws/<dbid>?v=<viewid>` and yields a Notion 404.
This parser keeps every ID as a labelled candidate so nothing is assumed valid.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

_HEX32 = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")
_UUID = re.compile(r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b")


def to_dashed(raw: str) -> str:
    compact = raw.replace("-", "").lower()
    if len(compact) != 32:
        raise ValueError(f"Not a 32-hex Notion ID: {raw!r}")
    return f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-{compact[16:20]}-{compact[20:]}"


def _ids_in(text: str) -> list[str]:
    found: list[str] = []
    for m in _UUID.finditer(text):
        found.append(to_dashed(m.group(1)))
    stripped = _UUID.sub(" ", text)
    for m in _HEX32.finditer(stripped):
        found.append(to_dashed(m.group(1)))
    out: list[str] = []
    for f in found:
        if f not in out:
            out.append(f)
    return out


def parse_notion_reference(value: str) -> dict[str, Any]:
    text = (value or "").strip()
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not text:
        return {"candidates": [], "warnings": ["Empty value."], "input": text}

    looks_like_url = "://" in text or text.lower().startswith(("notion.so", "www.notion.so", "notion.site"))
    if looks_like_url:
        parsed = urlparse(text if "://" in text else "https://" + text)
        path_ids = _ids_in(parsed.path)
        for i, pid in enumerate(path_ids):
            is_last = i == len(path_ids) - 1
            candidates.append({"id": pid, "kind": "path_id" if is_last else "path_parent_id", "position": "path",
                               "note": ("Database or page ID from the URL path. This is the usual connection candidate." if is_last
                                        else "Earlier path segment; probably a parent page, not the target.")})
        query = parse_qs(parsed.query)
        for v in query.get("v", []):
            for vid in _ids_in(v):
                candidates.append({"id": vid, "kind": "view_id", "position": "query:v",
                                   "note": "View ID from `?v=`. A view is not a database; the Notion API returns 404 for it."})
        for key in ("pvs", "p"):
            for v in query.get(key, []):
                for pid in _ids_in(v):
                    candidates.append({"id": pid, "kind": "query_page_id", "position": f"query:{key}", "note": f"ID found in `?{key}=`; usually a peeked page."})
        if parsed.fragment:
            for bid in _ids_in(parsed.fragment):
                candidates.append({"id": bid, "kind": "block_fragment", "position": "fragment",
                                   "note": "Block ID from the `#fragment`. Blocks are not databases."})
        if not path_ids:
            warnings.append("The URL path contains no 32-hex Notion ID.")
        if query.get("v") and path_ids:
            warnings.append("This URL contains both a path ID and a `?v=` view ID. Only the path ID can be a database/page; the view ID will 404.")
        host = (parsed.hostname or "").lower()
        if host and "notion" not in host:
            warnings.append(f"Host '{host}' is not a notion.so / notion.site domain.")
    else:
        for rid in _ids_in(text):
            kind = "raw_id"
            note = "Bare ID. Could be a database, data source, page, view, or block — type is unknown until probed."
            if text.lower().startswith(("ds:", "data_source:", "datasource:")):
                kind, note = "data_source_id", "Explicit data source ID."
            candidates.append({"id": rid, "kind": kind, "position": "text", "note": note})
        if not candidates:
            warnings.append("No 32-hex Notion ID found in that value.")
    seen: set[str] = set()
    unique = []
    for c in candidates:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        unique.append(c)
    return {"candidates": unique, "warnings": warnings, "input": text}


def legacy_last_match_id(value: str) -> str | None:
    """The v0.3.11 behaviour, kept only for tests/diagnostics to demonstrate the old failure mode."""
    matches = re.findall(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value or "")
    return to_dashed(matches[-1]) if matches else None


def primary_candidates(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Order candidates by how likely they are to be the connectable resource."""
    order = {"data_source_id": 0, "path_id": 1, "raw_id": 2, "query_page_id": 3, "path_parent_id": 4, "view_id": 5, "block_fragment": 6}
    return sorted(parsed.get("candidates", []), key=lambda c: order.get(c["kind"], 9))
