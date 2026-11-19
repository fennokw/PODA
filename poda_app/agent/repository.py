"""Bounded, read-only source navigation for a granted coding workspace."""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import code_context, grants

SKIP = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
        "dist", "build", "vendor", "target", ".next", ".cache"}
MANIFESTS = {"readme.md", "package.json", "pyproject.toml", "requirements.txt", "cargo.toml",
             "go.mod", "makefile", "gemfile", "pom.xml", "tsconfig.json", "agents.md"}
SOURCE = {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".c", ".cpp", ".h", ".cs"}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def map_workspace(root: str, query: str = "", limit: int = 25) -> dict:
    root_path, _ = grants.resolve(root, "read")
    if not root_path.is_dir():
        return {"ok": False, "error": "Choose an existing workspace folder."}
    candidates = []
    scanned = 0
    truncated = False
    terms = _tokens(query)
    for parent, directories, names in os.walk(root_path, followlinks=False):
        directory = Path(parent)
        directories[:] = sorted(name for name in directories if name not in SKIP and not name.startswith("."))
        if scanned + len(names) > 8000:
            truncated = True
            names = sorted(names)[:max(0, 8000 - scanned)]
        scanned += len(names)
        for name in sorted(names):
            path = directory / name
            manifest = name.casefold() in MANIFESTS
            if name.startswith(".") or path.is_symlink() or (not manifest and path.suffix.lower() not in SOURCE):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if not path.is_file() or stat.st_size > 500_000:
                continue
            relative = path.relative_to(root_path).as_posix()
            filename_tokens = _tokens(relative)
            is_test = "test" in filename_tokens or "tests" in filename_tokens or name.startswith("test_")
            score = 12 * len(terms & filename_tokens) + (5 if manifest else 0)
            if name.lower().split(".")[0] in {"main", "app", "index", "server", "cli"}:
                score += 4
            if is_test and terms & {"test", "tests", "pytest", "regression"}:
                score += 3
            candidates.append((score, relative.count("/"), relative, manifest, is_test))
        if scanned >= 8000:
            truncated = True
            break
    files = []
    for _, _, relative, manifest, is_test in sorted(candidates, key=lambda e: (-e[0], e[1], e[2]))[:limit]:
        path = root_path / relative
        if path.resolve() != path:
            continue
        if manifest:
            files.append({"path": relative, "kind": "manifest"})
            continue
        inspected = code_context.inspect_code(str(path))
        if not inspected["ok"]:
            continue
        content = inspected["result"]
        symbols = sorted(content["symbols"], key=lambda entry: not bool(terms & _tokens(entry["name"])))
        files.append({"path": relative, "kind": "test" if is_test else "source",
                      "symbols": symbols[:8], "dependencies": content["dependencies"][:6]})
    return {"ok": True, "result": {"root": str(root_path), "files": files,
            "candidates": len(candidates), "truncated": truncated or len(candidates) > limit},
            "verification": "Read granted workspace source and mapped selected symbols without executing project files."}
