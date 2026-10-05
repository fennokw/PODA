"""Best-effort macOS seatbelt sandbox for explicitly approved code execution.

This is stronger than a folder grant but not a general defense against a malicious
local administrator, kernel exploit, or vulnerabilities in macOS sandbox-exec.
If sandbox-exec is missing, code execution is DISABLED by default.
"""
from __future__ import annotations

import os
import json
import platform
import shutil
from pathlib import Path


def unsafe_opt_in() -> bool:
    return os.environ.get("PODA_UNSAFE_EXECUTE", "") == "I_UNDERSTAND_THIS_RUNS_AS_ME"


def sandbox_available() -> bool:
    return platform.system() == "Darwin" and shutil.which("sandbox-exec") is not None


def execution_mode() -> str:
    if sandbox_available(): return "macos_sandbox"
    if unsafe_opt_in(): return "user_opted_in_UNSANDBOXED"
    return "blocked_no_os_sandbox"


def _quoted(path: str) -> str:
    return json.dumps(str(Path(path).resolve()))


def wrap(command: list[str], granted_root: str, scratch: str) -> list[str]:
    mode = execution_mode()
    if mode == "blocked_no_os_sandbox":
        raise PermissionError("Execution blocked: macOS sandbox-exec is unavailable. "
            "Use a dedicated restricted macOS user/container for untrusted code; "
            "PODA_UNSAFE_EXECUTE=I_UNDERSTAND_THIS_RUNS_AS_ME is an explicit unsafe override.")
    if mode == "user_opted_in_UNSANDBOXED": return command
    # No outbound network or Keychain mach service privileges. Read only platform runtimes
    # and the explicitly granted directory, write only the grant and dedicated scratch directory.
    root = Path(granted_root).resolve()
    tmp = Path(scratch).resolve()
    tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile = f"""(version 1)
(deny default)
(allow process*)
(allow sysctl-read)
(allow mach-lookup (global-name "com.apple.system.logger"))
(allow file-read* (subpath "/System") (subpath "/usr") (subpath "/bin")
      (subpath "/Library/Frameworks") (subpath "/opt/homebrew")
      (subpath "/private/etc") (subpath "/private/var/db/dyld"))
(allow file-read* (subpath {_quoted(str(root))}) (subpath {_quoted(str(tmp))}))
(allow file-write* (subpath {_quoted(str(root))}) (subpath {_quoted(str(tmp))}))
"""
    return [shutil.which("sandbox-exec") or "/usr/bin/sandbox-exec", "-p", profile, *command]
