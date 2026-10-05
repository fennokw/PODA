#!/usr/bin/env python3
"""Build a source-only GitHub release from an explicit positive allowlist.

Fail closed: only authored source/docs and tests can be archived. New files need
explicit review before distribution. Checks apply to both the working tree and
EVERY file entering the archive. ZIPs are reproducible apart from compression.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "PODA_VERSION.txt").read_text().strip()
ALLOWED_TOP_FILES = {
    ".gitignore", ".gitattributes", "README.md", "README_PODA_MAC.md",
    "LICENSE", "SECURITY.md", "CONTRIBUTING.md", "RELEASE_NOTES.md", "PODA_VERSION.txt",
    "PODA_FEATURE_MANIFEST.md", "requirements.txt", ".env.example",
    "INSTALL_PODA.command", "setup_poda.command", "start_poda.command",
    "STOP_PODA.command", "VERIFY_PODA.command", "BACKUP_PODA.command",
    "RESTORE_PODA.command", "reset_poda_memory.command",
    "repair_open3d.command", "repair_poda_setup.command",
    "install_ollama_models.command", "PACKAGE_PODA.command",
}
ALLOWED_DIRS = {"poda_app", "tests", "docs", "tools", ".github"}
ALLOWED_EXT = {".py", ".js", ".css", ".html", ".svg", ".md", ".yml", ".yaml", ".txt"}
BLOCKED_PARTS = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".backups", ".patch_backups",
    "data", "logs", "screenshots", "dist", "sandbox", "node_modules", ".mypy_cache", ".ruff_cache",
}
BLOCKED_SUFFIX = {".db", ".sqlite", ".sqlite3", ".sqlcipher", ".podabkp", ".log", ".zip", ".pem", ".key", ".p12", ".png", ".jpg", ".jpeg", ".webp"}
# Generic high-confidence checks only; NEVER embed a private user's name, ID or account domain
# in a public release scanner. For a one-off migration pass, inject private strings through
# PODA_RELEASE_FORBIDDEN_MARKERS in the caller environment (they are never archived).
PRIVATE_PATTERNS = [
    (re.compile(r"\bntn_[A-Za-z0-9]{30,}\b"), "Notion secret"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"), "GitHub secret"),
    (re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"(?i)/Users/[A-Za-z0-9._-]{3,}/"), "literal macOS home path"),
]

BLOCKED_CONTENT_HEAD = (b"SQLite format 3\x00",)


def shipping_paths() -> list[Path]:
    result: list[Path] = []
    for item in sorted(ROOT.iterdir()):
        if item.is_symlink():
            raise ValueError(f"Symlink at checkout root requires manual review: {item.name}")
        if item.is_file() and item.name in ALLOWED_TOP_FILES:
            result.append(item)
        elif item.is_dir() and item.name in ALLOWED_DIRS:
            for path in sorted(item.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"Refusing symlink: {path.relative_to(ROOT)}")
                if not path.is_file():
                    continue
                rel = path.relative_to(ROOT)
                if any(p in BLOCKED_PARTS for p in rel.parts):
                    continue
                if path.suffix.lower() not in ALLOWED_EXT:
                    raise ValueError(f"Unexpected file type in authored tree: {rel}")
                result.append(path)
    return result


def audit(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for must in ("README.md", "SECURITY.md", "LICENSE", ".github/workflows/ci.yml", "poda_app/main.py", "tools/release_guard.py"):
        if ROOT / must not in paths:
            errors.append(f"Missing required public release file: {must}")
    for path in paths:
        rel = path.relative_to(ROOT)
        if any(part in BLOCKED_PARTS for part in rel.parts) or path.suffix.lower() in BLOCKED_SUFFIX:
            errors.append(f"Forbidden archive member: {rel}")
            continue
        if path.stat().st_size > 3 * 1024 * 1024:
            errors.append(f"Suspiciously large authored file: {rel}")
            continue
        blob = path.read_bytes()
        if b"\x00" in blob[:8192] or any(blob.startswith(head) for head in BLOCKED_CONTENT_HEAD):
            errors.append(f"Binary or database-like content: {rel}")
            continue
        text = blob.decode("utf-8", errors="replace")
        for rule, reason in PRIVATE_PATTERNS:
            if rule.search(text):
                errors.append(f"{rel}: {reason}")
        private_markers = [v for v in os.getenv("PODA_RELEASE_FORBIDDEN_MARKERS", "").split(",") if v]
        for marker in private_markers:
            if marker.casefold() in text.casefold():
                errors.append(f"{rel}: caller-defined private marker")
    return errors


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="Audit the source-only shipping list")
    p.add_argument("--package", action="store_true", help="Audit and generate dist/PODA-vX-public.zip")
    a = p.parse_args()
    if not (a.check or a.package):
        p.error("Specify --check or --package")
    paths = shipping_paths()
    errors = audit(paths)
    if errors:
        for issue in errors:
            print("BLOCKED:", issue, file=sys.stderr)
        return 1
    print(f"PASS: {len(paths)} vetted authored files; no excluded databases, logs, images or backups in archive list")
    if a.package:
        output = ROOT / "dist" / f"PODA-v{VERSION}.zip"
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=8) as archive:
            for file in paths:
                rel = file.relative_to(ROOT).as_posix()
                zi = zipfile.ZipInfo(f"PODA/{rel}", date_time=(2026, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = ((0o755 if file.suffix == ".command" else 0o644) << 16)
                archive.writestr(zi, file.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=8)
        print(f"CREATED: {output} ({output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
