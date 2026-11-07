"""Win32 primitives used by file_operations on native Windows."""
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import errno
import msvcrt
import ntpath
import os
from pathlib import Path
import stat
import time


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t),
        ("offset", wintypes.DWORD), ("offset_high", wintypes.DWORD),
        ("event", wintypes.HANDLE),
    ]


class _AttributeTag(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _function(name, result, *arguments):
    function = getattr(kernel32, name)
    function.restype = result
    function.argtypes = arguments
    return function


_create_file = _function(
    "CreateFileW", wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD,
    wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
)
_close = _function("CloseHandle", wintypes.BOOL, wintypes.HANDLE)
_get_type = _function("GetFileType", wintypes.DWORD, wintypes.HANDLE)
_get_info = _function(
    "GetFileInformationByHandleEx", wintypes.BOOL, wintypes.HANDLE,
    ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
)
_set_info = _function(
    "SetFileInformationByHandle", wintypes.BOOL, wintypes.HANDLE,
    ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
)
_lock = _function(
    "LockFileEx", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped),
)
_unlock = _function(
    "UnlockFileEx", wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped),
)
_move = _function("MoveFileExW", wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_LIST_DIRECTORY = 0x0001
_READ_ATTRIBUTES = 0x0080
_SHARE_READ = 0x00000001
_SHARE_WRITE = 0x00000002
_CREATE_NEW = 1
_OPEN_EXISTING = 3
_OPEN_ALWAYS = 4
_OPEN_REPARSE_POINT = 0x00200000
_BACKUP_SEMANTICS = 0x02000000


def _local_path(path):
    path = Path(path).absolute()
    if len(path.drive) != 2 or path.drive[1] != ":":
        raise OSError(errno.EINVAL, "数据目录必须位于本地驱动器", str(path))
    for part in path.parts[1:]:
        if part == ".." or ntpath.isreserved(part):
            raise OSError(errno.EINVAL, "数据路径包含保留名称", str(path))
    return path


def _native_path(path):
    return "\\\\?\\" + str(path)


def _open_checked(path, access, share, disposition, *, directory=False):
    flags = _OPEN_REPARSE_POINT | (_BACKUP_SEMANTICS if directory else 0)
    handle = _create_file(_native_path(path), access, share, None, disposition, flags, None)
    if handle == _INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _AttributeTag()
        if not _get_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(errno.ELOOP, "数据路径不能包含重解析点", str(path))
        if bool(info.attributes & stat.FILE_ATTRIBUTE_DIRECTORY) != directory or _get_type(handle) != 1:
            raise OSError(errno.EINVAL, "数据路径类型不符", str(path))
        return handle
    except BaseException:
        _close(handle)
        raise


@contextmanager
def directory(path, *, parent=None):
    path = _local_path(parent / path if parent is not None else path)
    # Deny writes and renames of each ancestor while using its child paths.
    # FILE_LIST_DIRECTORY makes the handle participate in Windows sharing checks.
    with ExitStack() as handles:
        components = (*reversed(path.parents), path) if parent is None else (path,)
        for component in components:
            handle = _open_checked(
                component, _LIST_DIRECTORY | _READ_ATTRIBUTES,
                _SHARE_READ, _OPEN_EXISTING, directory=True,
            )
            handles.callback(_close, handle)
        yield path


def _descriptor(path, access, share, disposition, flags):
    handle = _open_checked(path, access, share, disposition)
    try:
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        _close(handle)
        raise


def open_lock(path):
    path = _local_path(path)
    with directory(path.parent):
        return _descriptor(
            path, _GENERIC_READ | _GENERIC_WRITE, _SHARE_READ | _SHARE_WRITE,
            _OPEN_ALWAYS, os.O_RDWR,
        )


def open_exclusive_file(path):
    path = _local_path(path)
    with directory(path.parent):
        return _descriptor(path, _GENERIC_WRITE, _SHARE_READ, _CREATE_NEW, os.O_WRONLY)


def open_bound_file(parent, name, *, delete):
    path = _local_path(parent / name)
    return _descriptor(
        path, _GENERIC_READ | (_DELETE if delete else 0),
        _SHARE_READ, _OPEN_EXISTING, os.O_RDONLY,
    )


def unlink_bound_file(descriptor):
    delete = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile is BOOLEAN.
    if not _set_info(msvcrt.get_osfhandle(descriptor), 4, ctypes.byref(delete), ctypes.sizeof(delete)):
        raise ctypes.WinError(ctypes.get_last_error())


def lock_fd(descriptor, *, exclusive, blocking):
    flags = (2 if exclusive else 0) | (0 if blocking else 1)
    if not _lock(msvcrt.get_osfhandle(descriptor), flags, 0, 1, 0, ctypes.byref(_Overlapped())):
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError(errno.EAGAIN, "数据锁已被占用")
        raise ctypes.WinError(error)


def unlock_fd(descriptor):
    if not _unlock(msvcrt.get_osfhandle(descriptor), 0, 1, 0, ctypes.byref(_Overlapped())):
        error = ctypes.get_last_error()
        if error != 158:  # ERROR_NOT_LOCKED, e.g. after a cancelled acquisition.
            raise ctypes.WinError(error)


def move_path(source, destination, *, replace):
    source, destination = _local_path(source), _local_path(destination)
    # No COPY_ALLOWED: cross-volume moves must fail without copying.
    source_name, destination_name = _native_path(source), _native_path(destination)
    flags = 8 | (1 if replace else 0)
    deadline = time.monotonic() + 1
    while not _move(source_name, destination_name, flags):
        error = ctypes.get_last_error()
        # Python's normal Windows file readers deny delete sharing. A reader
        # may briefly hold a head file open during its atomic replacement.
        if error not in (32, 33) or time.monotonic() >= deadline:
            raise ctypes.WinError(error)
        time.sleep(.02)
