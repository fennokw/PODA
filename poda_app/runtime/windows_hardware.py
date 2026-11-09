"""Read physical RAM and dedicated GPU memory from Windows APIs."""
from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
from uuid import UUID


class MemoryStatus(ct.Structure):
    _fields_ = [("length", wt.DWORD), ("load", wt.DWORD), ("total", ct.c_uint64),
                ("available", ct.c_uint64), ("page_total", ct.c_uint64), ("page_available", ct.c_uint64),
                ("virtual_total", ct.c_uint64), ("virtual_available", ct.c_uint64), ("extended", ct.c_uint64)]


def ram_bytes() -> int:
    status = MemoryStatus()
    status.length = ct.sizeof(status)
    api = ct.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    api.argtypes = [ct.POINTER(MemoryStatus)]
    api.restype = wt.BOOL
    return int(status.total) if api(ct.byref(status)) else 0


class AdapterDescription(ct.Structure):
    _fields_ = [("description", ct.c_wchar * 128), ("vendor", wt.UINT), ("device", wt.UINT),
                ("subsystem", wt.UINT), ("revision", wt.UINT), ("dedicated_video", ct.c_size_t),
                ("dedicated_system", ct.c_size_t), ("shared_system", ct.c_size_t),
                ("luid_low", wt.DWORD), ("luid_high", wt.LONG), ("flags", wt.UINT)]


def _method(pointer, slot, result, *arguments):
    table = ct.cast(pointer, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
    return ct.WINFUNCTYPE(result, ct.c_void_p, *arguments)(table[slot])


def best_gpu() -> tuple[str | None, int | None]:
    """Use the largest hardware adapter's dedicated VRAM, without summing shared memory."""
    factory = ct.c_void_p()
    interface_id = (ct.c_ubyte * 16).from_buffer_copy(UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    create = ct.WinDLL("dxgi").CreateDXGIFactory1
    create.argtypes = [ct.c_void_p, ct.POINTER(ct.c_void_p)]
    create.restype = ct.c_long
    if create(ct.byref(interface_id), ct.byref(factory)) < 0:
        return None, None

    best: tuple[str | None, int | None] = (None, None)
    try:
        enumerate_adapter = _method(factory, 12, ct.c_long, wt.UINT, ct.POINTER(ct.c_void_p))
        for index in range(32):
            adapter = ct.c_void_p()
            if enumerate_adapter(factory, index, ct.byref(adapter)) < 0:
                break
            try:
                description = AdapterDescription()
                get_desc = _method(adapter, 10, ct.c_long, ct.POINTER(AdapterDescription))
                if get_desc(adapter, ct.byref(description)) < 0 or description.flags & 2:
                    continue
                if best[1] is None or description.dedicated_video > best[1]:
                    best = (description.description, int(description.dedicated_video))
            finally:
                _method(adapter, 2, wt.ULONG)(adapter)
    finally:
        _method(factory, 2, wt.ULONG)(factory)
    return best
