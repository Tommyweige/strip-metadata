"""Find which programs have a file open (Windows Restart Manager), to name them in error messages."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def who_locks(*paths: Path) -> list[str]:
    """Names of the programs holding any of `paths` open, e.g. ["Media Player"]. Empty if unknown."""
    if sys.platform != "win32":
        return []
    try:
        return _query([str(Path(p).resolve()) for p in paths])
    except Exception:  # a better message is a nicety; never fail because of it
        return []


def _query(files: list[str]) -> list[str]:
    import ctypes
    from ctypes import wintypes as w

    class UniqueProcess(ctypes.Structure):
        _fields_ = [("pid", w.DWORD), ("start", w.FILETIME)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("process", UniqueProcess), ("app_name", w.WCHAR * 256),
                    ("service", w.WCHAR * 64), ("app_type", ctypes.c_int), ("status", w.ULONG),
                    ("session", w.DWORD), ("restartable", w.BOOL)]

    rm = ctypes.WinDLL("rstrtmgr")
    handle = w.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rm.RmStartSession(ctypes.byref(handle), 0, key):
        return []
    try:
        arr = (w.LPCWSTR * len(files))(*files)
        if rm.RmRegisterResources(handle, len(files), arr, 0, None, 0, None):
            return []
        needed, count, reasons = w.UINT(0), w.UINT(0), w.DWORD()
        infos = None
        for _ in range(3):  # the list can grow between the size query and the fetch
            count = w.UINT(needed.value)
            infos = (ProcessInfo * max(needed.value, 1))()
            err = rm.RmGetList(handle, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons))
            if err == 0:
                break
            if err != 234:  # ERROR_MORE_DATA
                return []
        else:
            return []
        names: list[str] = []
        for info in infos[:count.value]:
            if info.process.pid == os.getpid():
                continue
            name = info.app_name or f"PID {info.process.pid}"
            if name not in names:
                names.append(name)
        return names
    finally:
        rm.RmEndSession(handle)
