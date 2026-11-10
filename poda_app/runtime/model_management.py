"""Local model inventory, downloads, role assignments and llama.cpp controls."""
from __future__ import annotations

from collections.abc import Callable
import json
from threading import Lock, Thread
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..security import egress
from . import config, hardware, inference, llama_install, model_catalog, models
from .db import set_setting

router = APIRouter(prefix="/system/model-manager", tags=["models"])
_jobs: dict[str, dict] = {}
_lock = Lock()


class Role(BaseModel):
    profile: str
    model: str


class Download(BaseModel):
    model: str


class Import(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    projector: str | None = Field(default=None, max_length=4096)
    thinking: bool = False
    tools: bool = False


class RuntimeInstall(BaseModel):
    backend: Literal["auto", "vulkan"] = "auto"


class EngineSettings(BaseModel):
    backend: Literal["auto", "cpu", "vulkan"]
    cache_k: str
    cache_v: str
    flash_attention: Literal["auto", "on", "off"]
    speculation: Literal["none", "ngram-simple", "ngram-cache"]
    context_limit: int = Field(ge=2048, le=131072)
    gpu_layers: int = Field(ge=-1, le=256)

    @field_validator("cache_k", "cache_v")
    @classmethod
    def supported_type(cls, value: str) -> str:
        if value not in model_catalog.CACHE_TYPES:
            raise ValueError("Choose a supported llama.cpp cache type.")
        return value


def _start(label: str, size: int | None, action: Callable, *, network: bool) -> dict:
    if network and egress.offline_mode():
        raise HTTPException(403, "Turn off Offline Mode to download, or import a GGUF already on your computer.")
    identifier = uuid.uuid4().hex
    item = {"id": identifier, "label": label, "state": "running", "bytes": 0, "total": size}
    with _lock:
        if any(job["state"] == "running" for job in _jobs.values()):
            raise HTTPException(409, "A model or engine installation is already in progress.")
        while len(_jobs) >= 12:
            _jobs.pop(next(iter(_jobs)))
        _jobs[identifier] = item

    def progress(count: int) -> None:
        with _lock:
            item["bytes"] = count

    def run() -> None:
        try:
            result = action(progress)
        except Exception as exc:
            with _lock:
                item.update(state="failed", error=str(exc)[:300])
        else:
            with _lock:
                item.update(state="completed", result=result)

    Thread(target=run, daemon=True, name="poda-model-install").start()
    return dict(item)


@router.get("/catalog")
def catalog() -> dict:
    hw = hardware.probe()
    with _lock:
        active = next((dict(item) for item in _jobs.values() if item["state"] == "running"), None)
    return {"active_job": active,"catalog": model_catalog.catalog(hw), "installed": [model_catalog.summary(item) for item in model_catalog.inventory()],
            "selected": models.selected_profiles(), "roles": models.registry()["profiles"],
            "engine": inference.status(), "settings": inference.settings(), "cache_types": model_catalog.CACHE_TYPES,
            "vulkan_available": config.WINDOWS and llama_install.executable("vulkan") is not None,
            "windows": config.WINDOWS, "hardware": {key: hw.get(key) for key in
                ("ram_total_gb", "gpu_memory_dedicated_gb", "unified_memory_gb", "disk_free_gb")}}


@router.get("/jobs/{identifier}")
def job(identifier: str) -> dict:
    with _lock:
        if identifier not in _jobs:
            raise HTTPException(404, "This installation is no longer listed.")
        return dict(_jobs[identifier])


@router.post("/runtime", status_code=202)
def install_runtime(body: RuntimeInstall) -> dict:
    if body.backend == "vulkan" and not config.WINDOWS:
        raise HTTPException(422, "The optional Vulkan runtime is available for Windows x64.")
    try:
        build = llama_install.platform_build(body.backend)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _start("llama.cpp " + build, None, lambda _: {"engine": str(llama_install.install(backend=body.backend).name)}, network=True)


@router.post("/download", status_code=202)
def download(body: Download) -> dict:
    item = model_catalog.CATALOG.get(body.model)
    if not item:
        raise HTTPException(422, "Choose a model from the verified GGUF catalog.")
    return _start(item.label + " " + item.quant, item.size,
                  lambda report: model_catalog.summary(model_catalog.install_model(item.id, report)), network=True)


@router.post("/import", status_code=202)
def import_gguf(body: Import) -> dict:
    return _start("Import local GGUF", None, lambda _: model_catalog.summary(model_catalog.import_model(
        body.path, body.projector, thinking=body.thinking, tools=body.tools)), network=False)


@router.put("/profile")
def assign(body: Role) -> dict:
    try:
        models.set_profile(body.profile, body.model, inference.installed_models())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"saved": True, "profile": body.profile, "model": body.model}


@router.put("/settings")
def configure(body: EngineSettings) -> dict:
    if body.backend == "vulkan" and (not config.WINDOWS or not llama_install.executable("vulkan")):
        raise HTTPException(422, "Install the Windows Vulkan runtime before selecting it.")
    if body.flash_attention == "off" and body.cache_v not in {"f16", "bf16", "f32"}:
        raise HTTPException(422, "Quantized V cache requires Flash Attention. Select Auto or On.")
    settings = body.model_dump()
    set_setting("llama_settings", json.dumps(settings))
    return {"saved": True, "settings": settings}
