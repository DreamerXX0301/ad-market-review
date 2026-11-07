"""Small Win32 helpers for an isolated desktop preview."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
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


def choose_previous() -> str:
    message = ctypes.WinDLL("user32", use_last_error=True).MessageBoxW
    message.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    message.restype = ctypes.c_int
    choice = message(
        None,
        "检测到旧版工作台数据。\n\n"
        "选「是」可备份旧数据并验证后复制到新版。数据量大时请等候。\n"
        "选「否」会创建空白工作台。选「取消」退出启动。",
        "冒险的梦想家 · 导入上版数据", 0x00040000 | 0x20 | 3,
    )
    return {6: "import", 7: "empty"}.get(choice, "cancel")
