"""Facts about this computer that decide how FacadeKit should behave on it."""

from __future__ import annotations

import ctypes
import os
import sys


def total_ram_bytes() -> int:
    """Installed memory in bytes; 0 when it cannot be found out."""
    if sys.platform == "win32":

        class _Status(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_ext", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.length = ctypes.sizeof(_Status)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        return int(status.total_phys) if ok else 0
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return 0
