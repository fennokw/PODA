"""Authenticated uploads and exports for the app-owned workspace."""
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from urllib.parse import quote

from . import sandbox_files

router = APIRouter(prefix="/agent/sandbox")


@router.get("")
def list_files():
    return {"files": sandbox_files.files()}


@router.post("/upload")
def upload_file(file: UploadFile):
    try:
        data = file.file.read(sandbox_files.MAX_BYTES + 1)
        if len(data) > sandbox_files.MAX_BYTES:
            raise HTTPException(413, "A PODA Sandbox file may be up to 16 MiB.")
        return sandbox_files.save(file.filename or "upload.bin", data, unique=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/download/{filename}")
def download_file(filename: str):
    try:
        data = sandbox_files.read_bytes(filename)
    except (OSError, ValueError) as exc:
        raise HTTPException(404, "This file is unavailable in PODA Sandbox.") from exc
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "Cache-Control": "no-store",
    })
