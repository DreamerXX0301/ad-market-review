"""Portable locks, durable publication, and no-follow data file access."""
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import stat

if os.name == "nt":
    from . import _windows_files as windows
else:
    import fcntl
    windows = None


def open_lock(path):
    if windows:
        return windows.open_lock(path)
    return os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)


def lock_fd(descriptor, *, exclusive=False, blocking=True):
    if windows:
        windows.lock_fd(descriptor, exclusive=exclusive, blocking=blocking)
    else:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(descriptor, mode | (0 if blocking else fcntl.LOCK_NB))


def unlock_fd(descriptor):
    if windows:
        windows.unlock_fd(descriptor)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def locked_file(path, *, exclusive=False, blocking=True):
    descriptor = open_lock(path)
    try:
        lock_fd(descriptor, exclusive=exclusive, blocking=blocking)
        try:
            yield descriptor
        finally:
            unlock_fd(descriptor)
    finally:
        os.close(descriptor)


def flush_file(path):
    with Path(path).open("r+b" if windows else "rb") as stream:
        os.fsync(stream.fileno())


def flush_directory(path):
    # Windows lacks a per-directory fsync. Publication uses flushed file data
    # and a same-volume MoveFileExW with MOVEFILE_WRITE_THROUGH.
    if not windows:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def move_path(source, destination, *, replace=True):
    if windows:
        windows.move_path(source, destination, replace=replace)
    elif replace:
        os.replace(source, destination)
    else:
        os.rename(source, destination)


def publish_immutable(source, destination):
    if windows:
        move_path(source, destination, replace=False)
    else:
        os.link(source, destination)


def open_exclusive_file(path):
    if windows:
        return windows.open_exclusive_file(path)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)


def is_link_or_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def path_is_link_or_reparse(path):
    try:
        return is_link_or_reparse(Path(path).lstat())
    except FileNotFoundError:
        return False


@contextmanager
def open_directory(root, *, parent=None):
    if windows:
        with windows.directory(root, parent=parent) as path:
            yield path
    else:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            yield descriptor
        finally:
            os.close(descriptor)


@dataclass
class BoundFile:
    descriptor: int
    parent: int | Path
    name: str

    def named_stat(self):
        if windows:
            # A Windows bound file denies write/delete sharing. Its directory
            # ancestry and its exact handle stay pinned through removal.
            return os.fstat(self.descriptor)
        return os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)

    def unlink(self, *, durable=False):
        if windows:
            windows.unlink_bound_file(self.descriptor)
        else:
            os.unlink(self.name, dir_fd=self.parent)
            if durable:
                os.fsync(self.parent)


@contextmanager
def bound_file(root, parts, *, delete=False):
    if not parts or any(
        not part or part in (".", "..") or "/" in part
        or (windows and ("\\" in part or ":" in part))
        for part in parts
    ):
        raise OSError(errno.EINVAL, "文件路径越出数据目录")
    with open_directory(root) as parent:
        with _bound_file_in_directory(parent, parts, delete=delete) as file:
            yield file


@contextmanager
def _bound_file_in_directory(parent, parts, *, delete):
    name, *remaining = parts
    if remaining:
        with open_directory(name, parent=parent) as child:
            with _bound_file_in_directory(child, remaining, delete=delete) as file:
                yield file
        return
    if windows:
        descriptor = windows.open_bound_file(parent, name, delete=delete)
    else:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield BoundFile(descriptor, parent, name)
    finally:
        os.close(descriptor)
