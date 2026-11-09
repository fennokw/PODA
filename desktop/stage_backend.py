"""Copy this checkout's application source into a desktop runtime."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP_FILES = ("requirements.txt", "PODA_VERSION.txt", "LICENSE")
APP_SUFFIXES = {".py", ".html", ".js", ".css", ".svg"}


def stage(target: Path) -> None:
    if target.exists() and any(target.iterdir()):
        if target != HERE / ".backend" or not (target / "desktop_backend.py").is_file():
            raise ValueError("Use an empty directory to stage the desktop backend.")
        shutil.rmtree(target)

    source = ROOT / "poda_app"
    if not (source / "main.py").is_file():
        raise ValueError("The local PODA application source is missing.")
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Refusing a link in the app source: {path.relative_to(ROOT)}")
        if not path.is_file() or path.suffix not in APP_SUFFIXES or path == source / "server.py":
            continue
        destination = target / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    for name in APP_FILES:
        shutil.copy2(ROOT / name, target / name)
    if (ROOT / "LICENSE-PODA").is_file():
        shutil.copy2(ROOT / "LICENSE-PODA", target / "LICENSE-PODA")
    for name in ("desktop_backend.py", *json.loads((HERE / "package.json").read_text(encoding="utf-8"))["podaSetupFiles"]):
        shutil.copy2(HERE / name, target / name)


if __name__ == "__main__":
    stage(Path(sys.argv[1]).resolve())
