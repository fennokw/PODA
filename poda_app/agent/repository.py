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


def _git(area: Path, cwd: Path, *args: str) -> dict:
    from .exec_tools import _run

    return _run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                 "-c", "core.hooksPath=" + os.devnull, "-C", str(cwd), *args], area, 35)


def _output(result: dict) -> str:
    if not result["ok"]:
        detail = (result.get("result") or {}).get("stderr") or result.get("error")
        raise ValueError(str(detail or "Git did not complete.")[:500])
    return result["result"]["stdout"].strip()


def isolated_worktree(root: str, destination: str, base_sha: str) -> dict:
    """Create and read back a detached worktree under the same approved grant."""
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("Use the full Git commit SHA for the approved starting revision.")
    repo, grant = grants.resolve(root, "execute")
    target, target_grant = grants.resolve(destination, "execute")
    area = Path(grant["root_path"])
    if grant["id"] != target_grant["id"]:
        raise PermissionError("The repo and worktree must be covered by the same execute-enabled folder grant.")
    if target.is_relative_to(repo) or repo.is_relative_to(target) or not target.parent.is_dir():
        raise ValueError("Choose a separate, non-existing worktree folder under an existing parent in the grant.")
    actual = Path(_output(_git(area, repo, "rev-parse", "--show-toplevel"))).resolve()
    if actual != repo:
        raise ValueError("Choose the root of the Git repository.")
    expected = _output(_git(area, repo, "rev-parse", "--verify", base_sha + "^{commit}"))
    if target.exists():
        original = _output(_git(area, repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        linked = _output(_git(area, target, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        target_root = Path(_output(_git(area, target, "rev-parse", "--show-toplevel"))).resolve()
        if linked != original or target_root != target:
            raise ValueError("The destination is already in use by a different workspace.")
    else:
        _output(_git(area, repo, "worktree", "add", "--detach", "--", str(target), expected))
    actual_head = _output(_git(area, target, "rev-parse", "--verify", "HEAD"))
    if actual_head != expected:
        raise ValueError("The worktree has a different HEAD. Review the existing workspace before continuing.")
    return {"ok": True, "result": {"root": str(target), "base_sha": actual_head},
            "verification": "Read back the isolated worktree HEAD at the approved starting revision."}


def git_state(root: str) -> dict:
    """Return the staged and unstaged changes, plus untracked path names."""
    repo, grant = grants.resolve(root, "execute")
    area = Path(grant["root_path"])
    actual = Path(_output(_git(area, repo, "rev-parse", "--show-toplevel"))).resolve()
    if actual != repo:
        raise ValueError("Choose the root of a Git repository or worktree.")
    head = _output(_git(area, repo, "rev-parse", "--verify", "HEAD"))
    status = _output(_git(area, repo, "status", "--porcelain=v1", "--untracked-files=normal"))
    diff = _git(area, repo, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--")
    patch = _output(diff)
    return {"ok": True, "result": {"root": str(repo), "head": head, "status": status[:4000],
                                  "diff": patch[:18000], "truncated": len(patch) > 18000 or diff["result"]["stdout_truncated"]},
            "verification": "Read local Git HEAD, working-tree status, and diff."}
