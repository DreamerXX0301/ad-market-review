"""Small Win32 helpers for an isolated desktop preview."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimit), ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory_limit", ctypes.c_size_t), ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t), ("peak_job_memory_used", ctypes.c_size_t),
    ]


def protect_process_tree() -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateJobObjectW
    create.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    configure = kernel.SetInformationJobObject
    configure.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    configure.restype = wintypes.BOOL
    current = kernel.GetCurrentProcess
    current.argtypes = ()
    current.restype = wintypes.HANDLE
    assign = kernel.AssignProcessToJobObject
    assign.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    assign.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    job = create(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = _ExtendedLimit()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not configure(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not assign(job, current()):
        error = ctypes.get_last_error()
        close(job)
        raise ctypes.WinError(error)
    # The non-inheritable job handle is intentionally owned for process life.
    # On a crash, closing it also cleans up this run's children. On normal exit
    # the supervisor has already drained the writer before the handle closes.


def require_local_ntfs(path: Path) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    volume_path = kernel.GetVolumePathNameW
    volume_path.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    volume_path.restype = wintypes.BOOL
    get_type = kernel.GetDriveTypeW
    get_type.argtypes = (wintypes.LPCWSTR,)
    get_type.restype = wintypes.UINT
    volume_info = kernel.GetVolumeInformationW
    volume_info.argtypes = (
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPWSTR, wintypes.DWORD,
    )
    volume_info.restype = wintypes.BOOL
    mount, fs = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(32)
    if not volume_path(str(path), mount, len(mount)):
        raise ctypes.WinError(ctypes.get_last_error())
    if get_type(mount.value) != 3:
        raise ValueError("工作台数据需位于本机固定磁盘")
    if not volume_info(mount.value, None, 0, None, None, None, fs, len(fs)):
        raise ctypes.WinError(ctypes.get_last_error())
    if fs.value.upper() != "NTFS":
        raise ValueError("首版工作台数据需位于本机 NTFS 文件系统")


def edge_path() -> Path:
    # Search the known installation locations rather than the working directory.
    for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(name)
        if base and Path(base).is_absolute():
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return candidate
    raise RuntimeError("这台 Windows 未找到 Microsoft Edge，无法打开预览窗口")
