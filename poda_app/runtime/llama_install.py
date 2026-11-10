"""Install checksum-pinned, upstream llama.cpp binaries into PODA's private runtime."""
from __future__ import annotations

import hashlib
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
import zipfile

from . import config

RELEASE = "b12247"
BUILDS = {
    "macos-arm64": ("tar.gz", "b64dc877683899be407bfe2d1776047ee137b7f1aaf1d90a25f29225f89e5f28", 16743183),
    "macos-x64": ("tar.gz", "0fd38423ee9e475cfa073dafc8dbe22686ddae6fed765f90b1c1b6eb4a412fa9", 12490263),
    "ubuntu-x64": ("tar.gz", "b353ef1ba235d377d4370d5e5e891f2b934db6fba81a21fd6a58f50a686e719b", 23158343),
    "win-cpu-x64": ("zip", "5d3a384d959c0c5f5848bdabc3e395b62b524d350ccede00b349dda1bbdb82bc", 24932066),
    "win-vulkan-x64": ("zip", "ece6edf09625b59205dcaed7d80ab4c5f303957b84cfa27fc553876df7641a3d", 37892194),
}


def platform_build(backend: str = "auto") -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in {"arm64", "x86_64"}:
        return "macos-" + ("arm64" if machine == "arm64" else "x64")
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "win-vulkan-x64" if backend == "vulkan" else "win-cpu-x64"
    if sys.platform.startswith("linux") and machine in {"amd64", "x86_64"}:
        return "ubuntu-x64"
    raise ValueError("No packaged llama.cpp build is available for this operating system and architecture.")


def download(url: str, target: Path, digest: str, size: int, progress=None) -> None:
    """Verify content before publication. A failed transfer leaves no usable file."""
    for attempt in range(3):
        checksum = hashlib.sha256()
        received = 0
        try:
            with urlopen(url, timeout=45) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    received += len(chunk)
                    if received > size:
                        raise ValueError("Download is larger than its verified manifest.")
                    checksum.update(chunk)
                    output.write(chunk)
                    if progress:
                        progress(received)
            if received != size or checksum.hexdigest() != digest:
                raise ValueError("Download failed size or SHA-256 verification.")
            return
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def _binary(root: Path, build: str, tool: str) -> Path | None:
    marker = root / "version.txt"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != f"{RELEASE} {build}":
        return None
    name = tool + (".exe" if build.startswith("win-") else "")
    return next((p for p in (root / name, root / f"llama-{RELEASE}" / name) if p.is_file()), None)


def executable(backend: str = "auto", tool: str = "llama-server") -> Path | None:
    try:
        build = platform_build(backend)
    except ValueError:
        return None
    return next((p for root in (config.DATA_DIR / "llama-runtime" / build, config.BASE_DIR / "llama-runtime")
                 if (p := _binary(root, build, tool))), None)


def install(target: Path | None = None, backend: str = "auto") -> Path:
    build = platform_build(backend)
    target = target or config.DATA_DIR / "llama-runtime" / build
    existing = _binary(target, build, "llama-server")
    if existing:
        return existing
    extension, digest, size = BUILDS[build]
    artifact = f"llama-{RELEASE}-bin-{build}.{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".llama-", dir=target.parent) as temporary:
        root = Path(temporary)
        archive = root / artifact
        download(f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/{artifact}", archive, digest, size)
        staged = root / "unpacked"
        staged.mkdir()
        if extension == "zip":
            with zipfile.ZipFile(archive) as package:
                for item in package.infolist():
                    path = PurePosixPath(item.filename)
                    if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                        raise ValueError("Unsafe llama.cpp archive path.")
                package.extractall(staged)
        else:
            with tarfile.open(archive) as package:
                package.extractall(staged, filter="data")
        (staged / "version.txt").write_text(f"{RELEASE} {build}\n", encoding="utf-8")
        if not _binary(staged, build, "llama-server"):
            raise ValueError("The verified package does not contain llama-server.")
        if target.exists():
            shutil.rmtree(target)
        os.replace(staged, target)
    return _binary(target, build, "llama-server")


if __name__ == "__main__":
    print(install(Path(sys.argv[1]) if len(sys.argv) > 1 else None))
