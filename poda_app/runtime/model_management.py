"""Explicit, local Ollama model management. Nothing is installed automatically.

On macOS an installation click opens a generated, strictly quoted .command file
in Terminal, showing the exact Ollama pull command before it runs.
"""
from __future__ import annotations

import os
import platform
import re
import shlex
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import config, models, ollama, hardware

router = APIRouter(prefix="/system/model-manager", tags=["models"])

_TAG = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,100}(?::[a-z0-9][a-z0-9._-]{0,80})?$")


class ModelProfile(BaseModel):
    profile: str
    model: str


class InstallRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=180)


@router.get("/catalog")
def catalog():
    hw = hardware.probe()
    return models.catalog(hw["unified_memory_gb"], hw.get("ollama", {}).get("models", []))


@router.put("/profile")
def assign_profile(body: ModelProfile):
    try:
        installed = ollama.installed_models()
        models.set_profile(body.profile, body.model, installed)
    except (ValueError, ollama.OllamaError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"saved": True, "profile": body.profile, "model": body.model,
            "note": "PODA uses this installed model from the next response onward."}


@router.post("/install-terminal")
def install_in_terminal(body: InstallRequest):
    tag = body.tag.strip().lower()
    if not _TAG.fullmatch(tag) or ".." in tag or tag.startswith("/") or "//" in tag:
        raise HTTPException(status_code=422, detail="Invalid Ollama model tag")
    if platform.system() != "Darwin":
        raise HTTPException(status_code=501, detail="The Terminal install button requires macOS.")
    root = config.DATA_DIR / "model_install_scripts"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / ("install-" + str(uuid.uuid4()) + ".command")
    cmd = "ollama pull " + shlex.quote(tag)
    script = ("#!/bin/zsh\nset -e\n"
              "echo 'PODA local Ollama model installation'\n"
              "echo 'Requested: " + tag + "'\n"
              "if ! command -v ollama >/dev/null; then echo 'Ollama is not installed.'; exit 1; fi\n"
              + cmd + "\n"
              "echo 'Install complete. Return to PODA > System and refresh models.'\n"
              "echo 'Press Return to close this window.'\nread\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd,"w",encoding="utf-8") as output:
        output.write(script)
    try:
        subprocess.Popen(["open","-a","Terminal",str(path)],
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except (OSError, ValueError) as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"Could not open Terminal: {exc}") from exc
    return {"terminal_open_requested": True, "model": tag, "command": cmd,
            "note": "PODA opened a local Terminal command file; installation completion is NOT verified. Check System after Ollama finishes."}
