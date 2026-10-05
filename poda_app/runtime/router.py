"""System, build identity, hardware, model registry and capability endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import config, ollama, hardware, models, capability
from .db import encryption_status, now_iso

router = APIRouter()


@router.get("/system/build")
def system_build() -> dict[str, Any]:
    return {"app": config.APP_NAME, "version": config.VERSION, "build_id": config.BUILD_ID, "memory_viewer": "/memory-viewer",
            "static_dir": str(config.STATIC_DIR), "db_path": str(config.DB_PATH), "legacy_memory_modal": False, "port": config.PORT}


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "app": config.APP_NAME, "version": config.VERSION, "build_id": config.BUILD_ID, "local_only": True,
            "fast_model": config.FAST_MODEL, "advanced_model": config.ADVANCED_MODEL, "embedding_model": config.EMBED_MODEL}


@router.get("/system/manifest")
def system_manifest() -> dict[str, Any]:
    return {
        "app": config.APP_NAME, "version": config.VERSION, "build_id": config.BUILD_ID, "scope": "single-device local-only Mac",
        "features": {
            "tiered_models": True, "tiered_thinking": True, "truth_grounding": True, "capability_ledger": True, "unsupported_action_gate": True,
            "action_receipts": True, "scoped_filesystem_tools": True, "scoped_code_execution": __import__("poda_app.agent.sandbox", fromlist=["execution_mode"]).execution_mode() != "blocked_no_os_sandbox", "project_priority_engine": True,
            "explainable_planner": True, "imap_preview": False, "calendar_text_event_extraction": True, "open3d_memory_geometry": True,
            "webgl2_memory_renderer": True, "open3d_volumetric_clouds": True, "persistent_memory_points": True,
            "distance_weighted_memory_web": True, "geometry_cosine_overrides": True, "dedicated_memory_viewer_route": True,
            "legacy_memory_modal_removed": True, "startup_build_identity_guard": True, "ollama_embeddings": config.EMBED_MODEL,
            "hybrid_retrieval_fts5_cosine_geometry": True, "recall_explanations": True, "memory_version_history": True,
            "session_summaries": True, "transactional_memory_editor": True, "visible_chat_memory_invariant": True,
            "notion_database_connection": True, "notion_resource_discovery": True, "notion_structured_url_parser": True,
            "notion_permission_diagnostics": True, "notion_schema_mapper": True, "notion_safe_write_verification": True,
            "sqlcipher_encryption_at_rest": encryption_status().get("database_encrypted_on_disk"), "local_session_origin_protection": True,
            "egress_allowlist_audit": True, "offline_mode": True, "encrypted_backups": True, "network_workers": False, "cloud_models": False,
        },
        "models": {"fast": config.FAST_MODEL, "advanced": config.ADVANCED_MODEL, "deep_optional": config.OPTIONAL_DEEP_MODEL, "embeddings": config.EMBED_MODEL},
    }


@router.get("/models")
def list_models() -> dict[str, Any]:
    try:
        return ollama.tags()
    except ollama.OllamaError as exc:
        return {"models": [], "error": str(exc)}


@router.get("/system/hardware")
def system_hardware() -> dict[str, Any]:
    return hardware.probe()


@router.get("/system/models")
def system_models() -> dict[str, Any]:
    hw = hardware.probe()
    reg = models.registry(hw.get("ollama", {}).get("models") or [], hw.get("unified_memory_gb"))
    reg["benchmarks"] = models.recorded_benchmarks()
    reg["hardware"] = {k: hw.get(k) for k in ("chip", "unified_memory_gb", "memory_available_estimate_gb", "metal", "macos")}
    return reg


class BenchmarkRequest(BaseModel):
    model: str
    num_ctx: int = 8192
    num_predict: int = 200


@router.post("/system/benchmark")
def system_benchmark(req: BenchmarkRequest) -> dict[str, Any]:
    hw = hardware.probe()
    installed = hw.get("ollama", {}).get("models") or []
    if not any(m == req.model or m.startswith(req.model) for m in installed):
        raise HTTPException(status_code=400, detail=f"Model {req.model} is not installed in Ollama; PODA does not pull models silently.")
    try:
        result = ollama.benchmark(req.model, max(1024, min(req.num_ctx, 262144)), num_predict=max(32, min(req.num_predict, 1200)))
    except ollama.OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    models.record_benchmark(result, f"{hw.get('chip')} / {hw.get('unified_memory_gb')} GB")
    result["measured_at"] = now_iso()
    return result


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    return capability.manifest()


@router.get("/capabilities/ledger")
def capabilities_ledger() -> dict[str, Any]:
    snapshot = capability.sync_ledger()
    return {"current": snapshot, "persisted": capability.persisted_ledger()}
