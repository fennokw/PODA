"""FastAPI routes for local LLM-history import."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..runtime import config
from .archive import ImportArchiveError, fingerprint, parse_archive
from .service import history, import_conversations

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("/history")
def import_history():
    return {"imports": history()}


@router.post("/llm-export")
async def import_llm_export(file: UploadFile = File(...), apply_profile: bool = Form(True)):
    name = (file.filename or "llm-export.zip").strip()
    if not name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Select the ZIP file produced by the provider's main/data export.")
    config.ensure_dirs()
    staging = config.DATA_DIR / "import-staging"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(staging, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix="llm-export-", suffix=".zip", dir=staging)
    os.close(fd)
    path = Path(temp_name)
    try:
        size = 0
        with path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 1_500_000_000:
                    raise HTTPException(status_code=413, detail="Archive exceeds the 1.5 GB compressed import limit.")
                out.write(chunk)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        fp = fingerprint(path)
        provider, conversations, meta = parse_archive(path)
        return import_conversations(provider, conversations, name, fp, apply_profile=apply_profile, parser_meta=meta)
    except ImportArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        try:
            await file.close()
        except Exception:
            pass
        path.unlink(missing_ok=True)
