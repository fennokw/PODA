"""macOS Keychain access via the `security` CLI. Secrets never touch SQLite, logs, or prompts."""
from __future__ import annotations

import subprocess


def keychain_get(service: str, account: str) -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def keychain_exists(service: str, account: str) -> bool:
    try:
        subprocess.run(["security", "find-generic-password", "-s", service, "-a", account],
                       check=True, capture_output=True, text=True, timeout=10)
        return True
    except Exception:
        return False


def keychain_set(service: str, account: str, secret: str) -> None:
    if not secret.strip():
        raise ValueError("Secret cannot be empty")
    try:
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", account, "-w", secret.strip()],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("macOS Keychain command 'security' is unavailable") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or "Unable to save secret in macOS Keychain").strip()) from exc


def keychain_delete(service: str, account: str) -> bool:
    try:
        result = subprocess.run(["security", "delete-generic-password", "-s", service, "-a", account],
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False
