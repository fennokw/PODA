"""Software skills API (CLI-Anything harnesses): catalog, install, grants, direct runs, receipts, sources."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import software, receipts

router = APIRouter()


def _err(status: int, code: str, message: str, remedy: str | None = None) -> HTTPException:
    detail = {"code": code, "message": message}
    if remedy:
        detail["remedy"] = remedy
    return HTTPException(status_code=status, detail=detail)


class InstallRequest(BaseModel):
    confirm: bool = False
    allow_network: bool = False
    extras: str | None = None


class ConfirmRequest(BaseModel):
    confirm: bool = False


class GrantRequest(BaseModel):
    enabled: bool = True
    allow_mutating: bool = False
    cwd_grant_id: str | None = None
    env: dict[str, str] | None = None


class RunRequest(BaseModel):
    args: list[str] = []
    timeout_s: int = 60
    approve: bool = False
    json: bool = True


class SourcesRequest(BaseModel):
    paths: list[str]


@router.get("/software")
def list_software(refresh: bool = False) -> dict[str, Any]:
    items = software.catalog(force=refresh)
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in items:
        groups.setdefault(e.get("category") or "other", []).append({k: v for k, v in e.items() if k != "skill_excerpt"})
    usable = [e["name"] for e in items if e["installed"] and e.get("enabled")]
    return {"count": len(items), "installed": [e["name"] for e in items if e["installed"]], "usable": usable if software.external_execution_enabled() else [], "groups": groups,
            "sources": [str(p) for p in software.repo_paths()], "venv": str(software.SOFTWARE_VENV), "venv_exists": software._venv_python().exists(),
            "capability": {"can_drive_software": bool(usable) and software.external_execution_enabled(), "detail": (f"PODA can drive: {', '.join(usable)}" if usable and software.external_execution_enabled() else "External software execution is disabled until explicit developer opt-in." if usable else "No harness is installed and enabled — PODA cannot drive any external program yet.")}}


@router.get("/software/receipts")
def software_receipts(limit: int = 50) -> dict[str, Any]:
    items = [r for r in receipts.list_receipts(max(1, min(limit * 4, 500))) if str(r.get("tool", "")).startswith("software_")][:limit]
    return {"receipts": items}


@router.post("/software/sources")
def set_sources(req: SourcesRequest) -> dict[str, Any]:
    return {"sources": software.set_repo_paths(req.paths), "count": len(software.catalog(force=True))}


@router.get("/software/{name}")
def get_software(name: str) -> dict[str, Any]:
    e = software.get(name)
    if not e:
        raise _err(404, "UNKNOWN_HARNESS", f"No harness named '{name}' in the configured sources.", "Check Files & Agent → Software skills → sources.")
    return {**e, "skill_md": software.skill_text(name)}


@router.post("/software/{name}/install")
def install_software(name: str, req: InstallRequest) -> dict[str, Any]:
    try:
        return software.install(name, confirm=req.confirm, allow_network=req.allow_network, extras=req.extras)
    except LookupError as exc:
        raise _err(404, "UNKNOWN_HARNESS", str(exc))
    except PermissionError as exc:
        raise _err(400, "CONFIRM_REQUIRED" if "confirm" in str(exc) else "NETWORK_REQUIRED", str(exc))
    except Exception as exc:
        raise _err(500, "INSTALL_FAILED", str(exc)[:600])


@router.post("/software/{name}/uninstall")
def uninstall_software(name: str, req: ConfirmRequest) -> dict[str, Any]:
    try:
        return software.uninstall(name, confirm=req.confirm)
    except PermissionError as exc:
        raise _err(400, "CONFIRM_REQUIRED", str(exc))
    except LookupError as exc:
        raise _err(404, "NOT_INSTALLED", str(exc))


@router.post("/software/{name}/grant")
def grant_software(name: str, req: GrantRequest) -> dict[str, Any]:
    if not software.get(name):
        raise _err(404, "UNKNOWN_HARNESS", f"No harness named '{name}'.")
    try:
        grant = software.set_grant(name, req.enabled, req.allow_mutating, req.cwd_grant_id, req.env)
    except ValueError as exc:
        raise _err(400, "INVALID_GRANT", str(exc))
    software._CACHE["catalog"] = None
    software.refresh_tools()
    return {"grant": grant, "harness": software.get(name)}


@router.delete("/software/{name}/grant")
def revoke_software(name: str) -> dict[str, Any]:
    ok = software.revoke_grant(name)
    software._CACHE["catalog"] = None
    software.refresh_tools()
    if not ok:
        raise _err(404, "NO_GRANT", "No active grant for this harness.")
    return {"revoked": True, "harness": software.get(name)}


@router.post("/software/{name}/run")
def run_software(name: str, req: RunRequest) -> dict[str, Any]:
    out = software.run(name, req.args, timeout_s=req.timeout_s, want_json=req.json, approved=req.approve)
    code = out.get("code")
    if not out["ok"] and out.get("receipt", {}).get("outcome") == "blocked":
        status = 404 if code in {"UNKNOWN_HARNESS", "NOT_INSTALLED"} else 403
        raise _err(status, code or "BLOCKED", out.get("error") or "blocked", "Files & Agent → Software skills: install, enable, or allow mutating commands; mutating runs need approve=true.")
    return out
