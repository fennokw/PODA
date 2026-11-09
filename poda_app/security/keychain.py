"""Operating system credential storage. Secrets stay outside SQLite, logs, and prompts."""
from __future__ import annotations

import subprocess
import sys

from ..runtime.config import CREDENTIAL_NAMESPACE

WINDOWS = sys.platform == "win32"
STORE_NAME = "Windows Credential Manager" if WINDOWS else "macOS Keychain"


def _target(service: str, account: str) -> str:
    return f"{CREDENTIAL_NAMESPACE}/{service}/{account}"


def _mac_service(service: str) -> str:
    return service if CREDENTIAL_NAMESPACE == "PODA" else f"{CREDENTIAL_NAMESPACE}/{service}"


def _windows_credential(service: str, account: str):
    import win32cred
    import pywintypes

    try:
        return win32cred.CredRead(_target(service, account), win32cred.CRED_TYPE_GENERIC, 0)
    except pywintypes.error as exc:
        if exc.winerror == 1168:  # ERROR_NOT_FOUND
            return None
        raise



def keychain_get(service: str, account: str) -> str | None:
    if WINDOWS:
        credential = _windows_credential(service, account)
        return credential["CredentialBlob"].decode("utf-16-le") if credential else None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", _mac_service(service), "-a", account, "-w"],
            check=True, capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def keychain_exists(service: str, account: str) -> bool:
    if WINDOWS:
        return _windows_credential(service, account) is not None
    try:
        subprocess.run(["security", "find-generic-password", "-s", _mac_service(service), "-a", account],
                       check=True, capture_output=True, text=True, timeout=10)
        return True
    except Exception:
        return False


def keychain_set(service: str, account: str, secret: str) -> None:
    if not secret.strip():
        raise ValueError("Secret cannot be empty")
    if WINDOWS:
        import win32cred

        win32cred.CredWrite({
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": _target(service, account),
            "UserName": account,
            "CredentialBlob": secret.strip(),
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        }, 0)
        return
    try:
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", _mac_service(service), "-a", account, "-w", secret.strip()],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("macOS Keychain command 'security' is unavailable") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError((exc.stderr or "Unable to save secret in macOS Keychain").strip()) from exc


def keychain_delete(service: str, account: str) -> bool:
    if WINDOWS:
        import win32cred
        import pywintypes

        try:
            win32cred.CredDelete(_target(service, account), win32cred.CRED_TYPE_GENERIC, 0)
        except pywintypes.error as exc:
            if exc.winerror == 1168:
                return False
            raise
        return True
    try:
        result = subprocess.run(["security", "delete-generic-password", "-s", _mac_service(service), "-a", account],
                                capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False
