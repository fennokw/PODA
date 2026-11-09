"""Opt-in Docker Desktop sandbox for approved Windows code runs."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

IMAGE = "poda-python:3.12"
PIPE = "npipe:////./pipe/dockerDesktopLinuxEngine"


def docker_path() -> str | None:
    if os.environ.get("PODA_WINDOWS_DOCKER") != "1":
        return None
    # Use the local Docker Desktop CLI and its Linux engine. A remote Docker
    # context or repository-local executable cannot change the destination.
    executable = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe"
    return str(executable) if executable.is_file() else None


def prepare(command: list[str], cwd: Path) -> tuple[list[str], list[str], bytes | None]:
    docker = docker_path()
    if not docker:
        raise PermissionError("Enable the local Docker Desktop sandbox before running Windows code.")
    if not cwd.drive or cwd.drive.startswith("\\") or "," in str(cwd):
        raise PermissionError("The Windows sandbox needs a granted local-drive folder without a comma in its path.")
    args = command[1:]
    payload = None
    if args[0] == "-c":
        payload = args[1].encode("utf-8")
        args = ["-", *args[2:]]
    elif args[0] != "-m":
        script = Path(args[0])
        try:
            args = [script.relative_to(cwd).as_posix(), *args[1:]]
        except ValueError as exc:
            raise PermissionError("The script must be inside the sandbox working folder.") from exc

    container = "poda-" + uuid.uuid4().hex
    client = [docker, "--host", PIPE]
    run = [*client, "run", "--rm", "--pull=never", "--name", container, "--network=none",
           "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only", "--pids-limit=96",
           "--memory=1g", "--cpus=2", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
           "--user", "65534:65534", "--mount", f"type=bind,source={cwd},target=/workspace",
           "--workdir", "/workspace", "-e", "HOME=/tmp", "-e", "PYTHONIOENCODING=utf-8",
           "-e", "PYTHONNOUSERSITE=1", "-e", "PYTHONDONTWRITEBYTECODE=1"]
    if payload is not None:
        run.append("-i")
    return [*run, IMAGE, "python", *args], [*client, "rm", "-f", container], payload
