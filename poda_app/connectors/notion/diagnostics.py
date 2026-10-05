"""Permission/resource diagnostics. A Notion 404 never uniquely identifies the cause; we enumerate what remains."""
from __future__ import annotations

from typing import Any

from ...security import egress
from . import state
from .client import NotionClient, NotionError, identity_summary, rich_plain
from .discovery import all_data_sources
from .service import inspect_relations
from .urls import parse_notion_reference, primary_candidates

SHARE_WALKTHROUGH = (
    "Open the ORIGINAL database (not a linked view and not Notion Calendar): in Notion, open the page that owns the table, "
    "or click the database title → 'Open in new page'. Then click ••• (top-right) → Connections → Add connections → choose 'PODA'. "
    "Repeat for every related database (e.g. Project Domain, Class). Linked views and Notion Calendar itself cannot be shared with an API connection."
)


def _diag(code: str, severity: str, message: str, remedy: str | None = None, **extra: Any) -> dict[str, Any]:
    d = {"code": code, "severity": severity, "message": message}
    if remedy:
        d["remedy"] = remedy
    d.update(extra)
    return d


def _probe(client: NotionClient, candidate: dict[str, Any]) -> dict[str, Any]:
    cid = candidate["id"]
    check: dict[str, Any] = {"id": cid, "kind": candidate["kind"], "data_source": None, "database": None, "page": None, "errors": {}}
    try:
        ds = client.data_source(cid)
        check["data_source"] = {"ok": True, "title": rich_plain(ds.get("title")) or "Untitled", "database_id": (ds.get("parent") or {}).get("database_id"),
                                "properties": ds.get("properties") or {}}
        return check
    except NotionError as exc:
        check["errors"]["data_source"] = exc.to_dict()
        if exc.kind == "invalid_token":
            raise
    try:
        db = client.database(cid)
        check["database"] = {"ok": True, "title": rich_plain(db.get("title")) or "Untitled", "data_sources": db.get("data_sources") or [], "in_trash": bool(db.get("in_trash"))}
        return check
    except NotionError as exc:
        check["errors"]["database"] = exc.to_dict()
    try:
        page = client.page(cid)
        props = page.get("properties") or {}
        title = next((rich_plain(p.get("title")) for p in props.values() if isinstance(p, dict) and p.get("type") == "title"), "") or "Untitled page"
        check["page"] = {"ok": True, "title": title, "parent": page.get("parent"), "in_trash": bool(page.get("in_trash"))}
    except NotionError as exc:
        check["errors"]["page"] = exc.to_dict()
    return check


def diagnose(value: str) -> dict[str, Any]:
    parsed = parse_notion_reference(value)
    result: dict[str, Any] = {"input": parsed["input"], "candidates": parsed["candidates"], "warnings": parsed["warnings"], "checks": [], "diagnosis": [], "resolved": None,
                              "accessible_data_sources": [], "share_walkthrough": SHARE_WALKTHROUGH}
    if not parsed["candidates"]:
        result["diagnosis"].append(_diag("NO_CANDIDATE", "error", "No Notion ID could be found in that value.", "Paste the URL of the original database (open it as a full page first) or its 32-character ID."))
        return result
    if egress.offline_mode():
        result["diagnosis"].append(_diag("OFFLINE_MODE", "error", "PODA is in offline mode; Notion cannot be contacted.", "Disable offline mode in Privacy & Permissions to run live diagnostics."))
        return result
    try:
        client = NotionClient()
    except NotionError as exc:
        result["diagnosis"].append(_diag("INVALID_TOKEN", "error", exc.message, "Paste a valid internal integration token first (Notion → Settings → Connections → Develop or manage integrations)."))
        return result
    try:
        me = identity_summary(client.me())
        result["identity"] = me
    except NotionError as exc:
        if exc.kind == "invalid_token":
            result["diagnosis"].append(_diag("INVALID_TOKEN", "error", exc.message, "Create or re-copy the integration token and validate it again. Tokens are workspace-specific."))
            return result
        result["diagnosis"].append(_diag("NETWORK", "error", exc.message))
        return result
    try:
        accessible = all_data_sources(client)
    except NotionError as exc:
        accessible = []
        result["diagnosis"].append(_diag("SEARCH_UNAVAILABLE", "warning", f"Could not list accessible data sources: {exc.message}"))
    result["accessible_data_sources"] = accessible
    accessible_ids = {str(d.get("id", "")).replace("-", "") for d in accessible} | {str(d.get("database_id", "")).replace("-", "") for d in accessible if d.get("database_id")}

    ordered = primary_candidates(parsed)
    view_ids = [c for c in ordered if c["kind"] == "view_id"]
    resolved = None
    for cand in ordered:
        try:
            check = _probe(client, cand)
        except NotionError as exc:
            result["diagnosis"].append(_diag("INVALID_TOKEN", "error", exc.message))
            return result
        result["checks"].append(check)
        if check["data_source"]:
            resolved = {"database_id": check["data_source"]["database_id"], "data_source_id": cand["id"], "title": check["data_source"]["title"], "via": cand["kind"]}
            result["diagnosis"].append(_diag("DATA_SOURCE_RESOLVED", "ok", f"'{check['data_source']['title']}' is a data source PODA can read.", data_source_id=cand["id"]))
            _relations(client, check["data_source"]["properties"], result)
            break
        if check["database"]:
            sources = check["database"]["data_sources"]
            if check["database"]["in_trash"]:
                result["diagnosis"].append(_diag("DATABASE_IN_TRASH", "error", f"Database '{check['database']['title']}' is in the trash."))
            if len(sources) == 1:
                resolved = {"database_id": cand["id"], "data_source_id": sources[0].get("id"), "title": check["database"]["title"], "via": cand["kind"]}
                result["diagnosis"].append(_diag("DATA_SOURCE_RESOLVED", "ok", f"Database '{check['database']['title']}' has one data source '{sources[0].get('name')}'; it is readable by PODA.", data_source_id=sources[0].get("id")))
                try:
                    ds = client.data_source(sources[0]["id"])
                    _relations(client, ds.get("properties") or {}, result)
                except NotionError as exc:
                    result["diagnosis"].append(_diag("DATA_SOURCE_UNREADABLE", "error", f"The database is visible but its data source is not: {exc.message}", SHARE_WALKTHROUGH))
            elif len(sources) > 1:
                resolved = {"database_id": cand["id"], "data_source_id": None, "title": check["database"]["title"], "via": cand["kind"], "data_sources": sources}
                result["diagnosis"].append(_diag("MULTIPLE_DATA_SOURCES", "action", f"Database '{check['database']['title']}' contains {len(sources)} data sources. Choose the one Notion Calendar displays.",
                                                 "Pick the data source by name below; PODA will connect to exactly that source.", data_sources=sources))
            else:
                result["diagnosis"].append(_diag("NO_DATA_SOURCES", "error", f"Database '{check['database']['title']}' exposed no data sources through the API."))
            break
        if check["page"]:
            result["diagnosis"].append(_diag("PAGE_NOT_DATABASE", "error", f"That ID is a page ('{check['page']['title']}'), not a database. If the table you see on it is a linked view, the original lives elsewhere.",
                                             "Open the table's original database (click its title → Open in new page) and copy that URL. " + SHARE_WALKTHROUGH))
            if view_ids:
                result["diagnosis"].append(_diag("LINKED_VIEW_SUSPECTED", "warning", "The URL has a `?v=` view parameter and the path ID is a page: this looks like a linked database view embedded in a page."))
            continue
        # Everything 404'd for this candidate.
        if cand["kind"] == "view_id":
            result["diagnosis"].append(_diag("VIEW_ID_NOT_DATABASE", "info", f"{cand['id']} is the view ID from `?v=`; Notion returns 404 for view IDs by design. It was not used."))
            continue
        if cand["kind"] == "block_fragment":
            result["diagnosis"].append(_diag("BLOCK_NOT_DATABASE", "info", f"{cand['id']} is a block fragment, not a database."))
            continue
        restricted = any(e.get("kind") == "restricted" for e in check["errors"].values())
        if restricted:
            result["diagnosis"].append(_diag("RESTRICTED_RESOURCE", "error", f"Notion returned 403 for {cand['id']}: the integration lacks permission for this resource.", SHARE_WALKTHROUGH))
            continue
        if accessible:
            result["diagnosis"].append(_diag("WRONG_WORKSPACE_OR_UNSHARED", "error",
                                             f"PODA's token works (workspace '{me.get('workspace_name') or me.get('workspace_id')}') and can see {len(accessible)} data source(s), but {cand['id']} is not one of them. "
                                             "Either this database is not shared with PODA, or it lives in a different workspace than the token.", SHARE_WALKTHROUGH, workspace=me.get("workspace_name")))
        remaining = ["the original database is not shared with the PODA connection", "the ID belongs to a different Notion workspace than this token",
                     "the ID is a linked database view rather than the original database", "the ID is a view, block, or page ID rather than a database/data source",
                     "the database was deleted or moved to trash"]
        if view_ids:
            remaining.insert(0, "the pasted URL points at a view; the view's parent may itself be a linked copy")
        result["diagnosis"].append(_diag("AMBIGUOUS_404", "error", f"Notion returned 404 for {cand['id']} as a data source, database, and page. Notion intentionally does not say which; still possible: " + "; ".join(remaining) + ".",
                                         SHARE_WALKTHROUGH, possibilities=remaining))
    if not resolved and accessible and not any(d["code"] == "WRONG_WORKSPACE_OR_UNSHARED" for d in result["diagnosis"]):
        result["diagnosis"].append(_diag("ACCESSIBLE_ALTERNATIVES", "info", f"PODA can currently see {len(accessible)} data source(s); pick one from the discovery list instead of guessing an ID."))
    if not accessible and not resolved:
        result["diagnosis"].append(_diag("NOTHING_SHARED", "warning", "The PODA connection has no databases shared with it at all in this workspace.", SHARE_WALKTHROUGH))
    result["resolved"] = resolved
    return result


def _relations(client: NotionClient, properties: dict[str, Any], result: dict[str, Any]) -> None:
    related = inspect_relations(client, properties)
    result["related"] = related
    for r in related:
        if not r["accessible"]:
            result["diagnosis"].append(_diag("RELATED_DATABASE_UNSHARED", "warning",
                                             f"Relation property '{r['property']}' points at a database PODA cannot read ({r.get('error')}). Reads will show raw IDs and writes cannot set this relation safely.",
                                             "Share the related original database (e.g. Project Domain, Class) with PODA: " + SHARE_WALKTHROUGH, property=r["property"]))
