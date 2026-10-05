"""Real hardware/runtime diagnostics for this Mac. Nothing here is assumed; everything is probed."""
from __future__ import annotations

import platform
import shutil
import subprocess
from typing import Any

from . import config, ollama


def _sysctl(key: str) -> str | None:
    try:
        return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def _vm_stat() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        text = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        page = 16384
        for line in text.splitlines():
            if "page size of" in line:
                page = int("".join(ch for ch in line.split("page size of")[1] if ch.isdigit()) or 16384)
            if ":" in line and line.strip().startswith("Pages"):
                key, value = line.split(":", 1)
                digits = "".join(ch for ch in value if ch.isdigit())
                if digits:
                    out[key.strip().lower().replace(" ", "_")] = int(digits) * page
        out["page_size"] = page
    except Exception:
        pass
    return out


def probe() -> dict[str, Any]:
    mem_total = int(_sysctl("hw.memsize") or 0)
    vm = _vm_stat()
    free_like = vm.get("pages_free", 0) + vm.get("pages_inactive", 0) + vm.get("pages_speculative", 0) + vm.get("pages_purgeable", 0)
    disk = shutil.disk_usage(str(config.BASE_DIR))
    metal = None
    try:
        sp = subprocess.run(["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True, timeout=15).stdout
        for line in sp.splitlines():
            if "Metal" in line:
                metal = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass
    ollama_state = ollama.status()
    return {
        "chip": _sysctl("machdep.cpu.brand_string"),
        "cores": int(_sysctl("hw.ncpu") or 0),
        "unified_memory_bytes": mem_total,
        "unified_memory_gb": round(mem_total / 1024**3, 1),
        "memory_available_estimate_bytes": free_like,
        "memory_available_estimate_gb": round(free_like / 1024**3, 1),
        "disk_free_gb": round(disk.free / 1024**3, 1),
        "disk_total_gb": round(disk.total / 1024**3, 1),
        "metal": metal,
        "macos": platform.mac_ver()[0],
        "python": platform.python_version(),
        "ollama": ollama_state,
        "ollama_resident": ollama.ps(),
    }
