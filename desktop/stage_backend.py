"""Copy the pinned PODA Web application into the desktop bundle."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
APP_FILES = {"requirements.txt", "PODA_VERSION.txt", "LICENSE", "LICENSE-PODA"}
APP_SUFFIXES = {".py", ".html", ".js", ".css", ".svg"}


def git(cwd: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(cwd), *args])


def stage(target: Path) -> None:
    lock = json.loads((HERE / "backend-lock.json").read_text(encoding="utf-8"))
    revision = lock["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("The PODA Web lock requires a full commit SHA.")
    with tempfile.TemporaryDirectory(prefix="poda-web-pin-") as directory:
        repo = Path(os.environ["PODA_WEB_CHECKOUT"]).resolve() if os.getenv("PODA_WEB_CHECKOUT") else Path(directory)
        if repo == Path(directory):
            git(repo, "init", "--quiet")
            git(repo, "fetch", "--quiet", "--depth=1", "--no-tags", lock["repository"], revision)
        if git(repo, "rev-parse", revision + "^{commit}").decode().strip() != revision:
            raise ValueError("The PODA Web commit does not match the lock.")
        archive = git(repo, "archive", "--format=zip", revision, "poda_app", *sorted(APP_FILES))
    if target.exists() and any(target.iterdir()):
        if target != HERE / ".backend" or not (target / "backend-lock.json").is_file():
            raise ValueError("Use an empty directory to stage the desktop backend.")
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        for member in source.infolist():
            if member.is_dir():
                continue
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts) or "\\" in member.filename:
                raise ValueError("Invalid path in the pinned runtime archive.")
            if path.as_posix() == "poda_app/server.py":
                continue
            if not (path.as_posix() in APP_FILES or (path.parts[0] == "poda_app" and path.suffix in APP_SUFFIXES)):
                raise ValueError(f"Unexpected runtime file: {path}")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError(f"Unexpected link in runtime archive: {path}")
            destination = target.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read(member))
    for name in ("desktop_backend.py", *json.loads((HERE / "package.json").read_text())["podaSetupFiles"]):
        shutil.copy2(HERE / name, target / name)
    shutil.copyfile(HERE / "backend-lock.json", target / "backend-lock.json")


if __name__ == "__main__":
    stage(Path(sys.argv[1]).resolve())
