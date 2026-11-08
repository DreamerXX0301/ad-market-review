"""Filesystem contracts shared by the desktop and native service."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.infrastructure import storage
from app.infrastructure.file_operations import bound_file, locked_file, publish_immutable
from app.infrastructure.storage import atomic_json


def _probe_lock(path, *, exclusive):
    script = """
import sys
from app.infrastructure.file_operations import locked_file

try:
    with locked_file(sys.argv[1], exclusive=sys.argv[2] == "exclusive", blocking=False):
        print("acquired")
except BlockingIOError:
    print("busy")
"""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path), "exclusive" if exclusive else "shared"],
        cwd=root, check=True, capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def test_readers_share_the_lock_but_writer_requires_exclusive_access(tmp_path):
    path = tmp_path / "worker.lock"
    with locked_file(path):
        assert _probe_lock(path, exclusive=False) == "acquired"
        assert _probe_lock(path, exclusive=True) == "busy"
    with locked_file(path, exclusive=True, blocking=False):
        assert _probe_lock(path, exclusive=False) == "busy"
        assert _probe_lock(path, exclusive=True) == "busy"
    assert _probe_lock(path, exclusive=True) == "acquired"


def test_immutable_publication_rejects_duplicate_destination(tmp_path):
    source, destination = tmp_path / "new", tmp_path / "immutable"
    source.write_bytes(b"old")
    publish_immutable(source, destination)
    source.unlink(missing_ok=True)
    source.write_bytes(b"new")
    with pytest.raises(FileExistsError):
        publish_immutable(source, destination)
    assert destination.read_bytes() == b"old"
    assert source.read_bytes() == b"new"


def test_atomic_metadata_uses_portable_utf8_bytes(tmp_path):
    path = tmp_path / "manifest.json"
    atomic_json(path, {"状态": "旧"})
    atomic_json(path, {"状态": "新"})
    assert path.read_bytes() == '{"状态":"新"}\n'.encode()


def test_atomic_metadata_removes_only_its_temporary_file_after_failed_replace(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    atomic_json(path, {"generation": "old"})
    unrelated = tmp_path / "manifest.json.other.tmp"
    unrelated.write_bytes(b"another writer")
    failure = PermissionError("fixture replacement denied")

    def deny_replace(source, destination):
        assert destination == path and source != unrelated
        assert source.read_bytes() == b'{"generation":"new"}\n'
        raise failure

    monkeypatch.setattr(storage, "move_path", deny_replace)
    with pytest.raises(PermissionError) as raised:
        atomic_json(path, {"generation": "new"})
    assert raised.value is failure
    assert path.read_bytes() == b'{"generation":"old"}\n'
    assert unrelated.read_bytes() == b"another writer"
    assert list(tmp_path.glob("*.tmp")) == [unrelated]


def test_bound_read_and_removal_use_the_same_ordinary_file(tmp_path):
    directory = tmp_path / "sealed"
    directory.mkdir()
    source = directory / "source.json"
    source.write_bytes(b"evidence")
    with bound_file(tmp_path, ("sealed", "source.json"), delete=True) as opened:
        assert os.read(opened.descriptor, 1024) == b"evidence"
        assert opened.named_stat().st_size == 8
        if os.name == "nt":
            with pytest.raises(PermissionError):
                source.rename(directory / "swapped.json")
            with pytest.raises(PermissionError):
                directory.rename(tmp_path / "swapped")
        opened.unlink(durable=True)
    assert not source.exists()


def test_bound_directory_refuses_symlink_or_windows_junction(tmp_path):
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    source = outside / "source.json"
    source.write_bytes(b"unchanged")
    alias = root / "alias"
    if os.name == "nt":
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(outside)],
            check=True, capture_output=True, timeout=10,
        )
    else:
        alias.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(OSError):
            with bound_file(root, ("alias", "source.json"), delete=True):
                pytest.fail("A directory alias must never bind its target")
        assert source.read_bytes() == b"unchanged"
    finally:
        if os.name == "nt":
            alias.rmdir()
