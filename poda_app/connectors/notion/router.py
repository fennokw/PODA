"""Notion connector API. Every error is {detail: {code, message, remedy?}}; no response ever contains the token."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...runtime import capability
from . import calendar, diagnostics, discovery, mapping, service, state
from .client import NotionError
from .urls import parse_notion_reference

router = APIRouter()

_STATUS = {"invalid_token": 401, "no_token": 401, "restricted": 403, "not_found": 404, "rate_limited": 429, "validation": 400, "server": 502, "network": 502, "offline": 503}


def _http(exc: NotionError, remedy: str | None = None) -> HTTPException:
    detail: dict[str, Any] = {"code": exc.code or exc.kind, "kind": exc.kind, "message": exc.message}
    if remedy:
        detail["remedy"] = remedy
    elif exc.kind in {"not_found", "restricted"}:
        detail["remedy"] = diagnostics.SHARE_WALKTHROUGH
    elif exc.kind in {"invalid_token", "no_token"}:
        detail["remedy"] = "Validate a Notion internal integration token in the Notion screen first."
    return HTTPException(status_code=_STATUS.get(exc.kind, 400), detail=detail)


class TokenRequest(BaseModel):
    token: str


class ValueRequest(BaseModel):
    value: str


class ConnectRequest(BaseModel):
    data_source_id: str
    database_id: str | None = None


class QueryRequest(BaseModel):
    filter: dict[str, Any] | None = None
    sorts: list[dict[str, Any]] | None = None
    page_size: int = 100
    start_cursor: str | None = None


class CreatePageRequest(BaseModel):
    properties: dict[str, Any]
    children: list[dict[str, Any]] | None = None


class UpdatePageRequest(BaseModel):
    properties: dict[str, Any] = {}
    in_trash: bool | None = None


class MappingRequest(BaseModel):
    title: str
    date: str
    status: str | None = None
    type: str | None = None
    project_relation: str | None = None
    class_relation: str | None = None
    notes: str | None = None
    external_ids: dict[str, str] = {}
    status_done_values: list[str] = []
    default_status: str | None = None
    default_type: str | None = None


class EventProposal(BaseModel):
    title: str
    start: str
    end: str | None = None
    time_zone: str | None = None
    status: str | None = None
    type: str | None = None
    notes: str | None = None
    project_ids: list[str] | None = None
    class_ids: list[str] | None = None
    external_ids: dict[str, Any] | None = None
    page_id: str | None = None
    session_id: str | None = None


class CommitRequest(BaseModel):
    proposal_id: str
    confirm: bool = False
    session_id: str | None = None


class ConfirmRequest(BaseModel):
    confirm: bool = False
    session_id: str | None = None


class EventUpdate(BaseModel):
    changes: dict[str, Any]
    expected_last_edited_time: str | None = None
    session_id: str | None = None


@router.get("/notion/status")
def notion_status(verify: bool = False) -> dict[str, Any]:
    result = service.status(verify=verify)
    result["mapping"] = mapping.get_active_mapping()
    if verify:
        capability.sync_ledger()
    return result


@router.post("/notion/token")
def notion_token(req: TokenRequest) -> dict[str, Any]:
    try:
        result = discovery.validate_token(req.token, store=True)
    except NotionError as exc:
        raise _http(exc, "Create an internal integration at notion.so/profile/integrations, copy its token, and make sure it belongs to the workspace that owns your calendar database.")
    capability.sync_ledger()
    return {**result, "token_storage": "macOS Keychain", "next": "Discover or diagnose the database, then connect to its data source."}


@router.delete("/notion/token")
def notion_delete_token() -> dict[str, Any]:
    result = service.delete_token()
    capability.sync_ledger()
    return result


@router.post("/notion/parse-url")
def notion_parse_url(req: ValueRequest) -> dict[str, Any]:
    return parse_notion_reference(req.value)


@router.get("/notion/discover")
def notion_discover(query: str | None = None, cursor: str | None = None) -> dict[str, Any]:
    try:
        return discovery.list_data_sources(query=query, cursor=cursor)
    except NotionError as exc:
        raise _http(exc)


@router.post("/notion/diagnose")
def notion_diagnose(req: ValueRequest) -> dict[str, Any]:
    return diagnostics.diagnose(req.value)


@router.post("/notion/connect")
def notion_connect(req: ConnectRequest) -> dict[str, Any]:
    try:
        result = service.connect_data_source(req.data_source_id, req.database_id)
    except NotionError as exc:
        raise _http(exc)
    capability.sync_ledger()
    return result


@router.post("/notion/verify-write")
def notion_verify_write() -> dict[str, Any]:
    try:
        result = service.verify_write()
    except NotionError as exc:
        capability.sync_ledger()
        raise _http(exc, "Give the PODA connection 'Insert content' and 'Update content' capabilities in the integration settings, then re-share the database.")
    capability.sync_ledger()
    return result


@router.get("/notion/schema")
def notion_schema() -> dict[str, Any]:
    cfg = state.get_config()
    if not cfg:
        raise HTTPException(status_code=404, detail={"code": "not_connected", "message": "No Notion database is connected."})
    live = service.status(verify=True)
    capability.sync_ledger()
    return {"properties": live.get("properties") or service.schema_summary(cfg.get("schema") or {}), "fingerprint": live.get("schema_fingerprint") or cfg.get("schema_fingerprint"),
            "changed_since_connect": bool(live.get("schema_changed")), "status": live.get("status"), "related": live.get("related") or cfg.get("related"), "error": live.get("error")}


@router.get("/notion/mapping")
def notion_mapping() -> dict[str, Any]:
    cfg = state.get_config()
    return {"active": mapping.get_active_mapping(), "inferred": mapping.infer_mapping((cfg or {}).get("schema") or {}), "history": mapping.mapping_history(),
            "schema": service.schema_summary((cfg or {}).get("schema") or {}), "fields": {k: sorted(v) for k, v in mapping.MAPPING_FIELDS.items()}}


@router.get("/notion/agenda")
def notion_agenda(day: str = "today", include_cancelled: bool = False) -> dict[str, Any]:
    """Locally interpreted agenda (events/classes + deadlines) for a day word or YYYY-MM-DD; works with an inferred mapping."""
    try:
        start, end = mapping.resolve_day_word(day)
        data = mapping.items_between_effective(start, end, include_cancelled=include_cancelled)
    except NotionError as exc:
        raise _http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code": "bad_day", "message": str(exc)})
    data["events"] = [i for i in data["items"] if not i.get("is_deadline")]
    data["deadlines"] = [i for i in data["items"] if i.get("is_deadline")]
    return data


@router.post("/notion/mapping")
def notion_save_mapping(req: MappingRequest) -> dict[str, Any]:
    try:
        result = mapping.save_mapping(req.model_dump())
    except NotionError as exc:
        raise _http(exc)
    capability.sync_ledger()
    return result


@router.get("/notion/mapping/preview")
def notion_mapping_preview(n: int = 5) -> dict[str, Any]:
    try:
        return mapping.preview(n)
    except NotionError as exc:
        raise _http(exc)


@router.get("/notion/calendar")
def notion_calendar(start: str, end: str) -> dict[str, Any]:
    """What Notion Calendar shows for this database between two dates (inclusive), grouped client-side by day."""
    try:
        return calendar.events_between(start, end)
    except NotionError as exc:
        raise _http(exc)


@router.get("/notion/calendar/items")
def notion_calendar_items(limit: int = 100) -> dict[str, Any]:
    try:
        pages = service.query_pages(limit=max(1, min(limit, 500)))
    except NotionError as exc:
        raise _http(exc)
    return {"results": pages, "count": len(pages)}


@router.get("/notion/items")
def notion_items(start: str, end: str) -> dict[str, Any]:
    try:
        items = mapping.items_between(start, end)
    except NotionError as exc:
        raise _http(exc)
    return {"start": start, "end": end, "count": len(items), "items": items}


@router.post("/notion/query")
def notion_query(req: QueryRequest) -> dict[str, Any]:
    cfg = state.get_config()
    if not cfg or not cfg.get("data_source_id"):
        raise HTTPException(status_code=400, detail={"code": "not_connected", "message": "No Notion data source is connected."})
    try:
        from .client import NotionClient
        payload: dict[str, Any] = {"page_size": max(1, min(req.page_size, 100))}
        if req.filter:
            payload["filter"] = req.filter
        if req.sorts:
            payload["sorts"] = req.sorts
        if req.start_cursor:
            payload["start_cursor"] = req.start_cursor
        raw = NotionClient().query(cfg["data_source_id"], payload)
    except NotionError as exc:
        raise _http(exc)
    return {"results": [service.normalize_page(p) for p in raw.get("results", []) if isinstance(p, dict) and p.get("object") == "page"],
            "has_more": raw.get("has_more", False), "next_cursor": raw.get("next_cursor")}


@router.post("/notion/pages")
def notion_create_page(req: CreatePageRequest) -> dict[str, Any]:
    cfg = state.get_config()
    if not cfg or not cfg.get("data_source_id"):
        raise HTTPException(status_code=400, detail={"code": "not_connected", "message": "No Notion data source is connected."})
    if not cfg.get("insert_verified"):
        raise HTTPException(status_code=400, detail={"code": "write_unverified", "message": "Run VERIFY WRITE ACCESS before creating pages."})
    try:
        from .client import NotionClient
        payload: dict[str, Any] = {"parent": {"type": "data_source_id", "data_source_id": cfg["data_source_id"]}, "properties": req.properties}
        if req.children:
            payload["children"] = req.children
        page = NotionClient().create_page(payload)
    except NotionError as exc:
        raise _http(exc)
    return {"created": True, "page": service.normalize_page(page), "raw_id": page.get("id")}


@router.patch("/notion/pages/{page_id}")
def notion_update_page(page_id: str, req: UpdatePageRequest) -> dict[str, Any]:
    cfg = state.get_config()
    if not cfg or not cfg.get("update_verified"):
        raise HTTPException(status_code=400, detail={"code": "write_unverified", "message": "Run VERIFY WRITE ACCESS before updating pages."})
    try:
        from .client import NotionClient
        payload: dict[str, Any] = {"properties": req.properties}
        if req.in_trash is not None:
            payload["in_trash"] = req.in_trash
        page = NotionClient().update_page(page_id, payload)
    except NotionError as exc:
        raise _http(exc)
    return {"updated": True, "page": service.normalize_page(page), "raw_id": page.get("id")}


@router.post("/notion/events/propose")
def notion_propose(req: EventProposal) -> dict[str, Any]:
    try:
        return mapping.propose_event(req.model_dump(exclude_none=True), session_id=req.session_id)
    except NotionError as exc:
        raise _http(exc)


@router.post("/notion/events/commit")
def notion_commit(req: CommitRequest) -> dict[str, Any]:
    if not req.confirm:
        raise HTTPException(status_code=400, detail={"code": "confirmation_required", "message": "Review the proposal and re-send with confirm=true."})
    try:
        result = mapping.commit_event(req.proposal_id, session_id=req.session_id)
    except NotionError as exc:
        raise _http(exc)
    capability.sync_ledger()
    return result


@router.post("/notion/events/{page_id}/update")
def notion_update_event(page_id: str, req: EventUpdate) -> dict[str, Any]:
    try:
        return mapping.update_event(page_id, req.changes, req.expected_last_edited_time, session_id=req.session_id)
    except NotionError as exc:
        raise HTTPException(status_code=409 if exc.code == "conflict" else _STATUS.get(exc.kind, 400), detail={"code": exc.code or exc.kind, "message": exc.message})


@router.post("/notion/events/{page_id}/archive")
def notion_archive(page_id: str, req: ConfirmRequest) -> dict[str, Any]:
    try:
        return mapping.archive_event(page_id, req.confirm, session_id=req.session_id)
    except NotionError as exc:
        raise _http(exc)


@router.post("/notion/disconnect")
def notion_disconnect() -> dict[str, Any]:
    result = service.disconnect()
    capability.sync_ledger()
    return result


@router.get("/notion/test-pages")
def notion_test_pages() -> dict[str, Any]:
    return {"test_pages": service.all_test_pages(), "leftover": service.leftover_test_pages()}
