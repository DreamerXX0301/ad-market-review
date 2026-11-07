"""Canonical JSON, hashing and durable local file operations."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any
import uuid

from .file_operations import flush_directory, move_path

def now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_id(prefix: str = "") -> str:
    return (prefix + "_" if prefix else "") + uuid.uuid4().hex


def json_clean(value: Any) -> Any:
    # Most chart/strategy leaves are already Python JSON scalars. Exact types
    # keep numpy/custom scalar conversion and date handling on the old path.
    kind = type(value)
    if value is None or kind is str or kind is int or kind is bool:
        return value
    if kind is float:
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_clean(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if hasattr(value, "item"):
        return json_clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def dumps(value: Any) -> str:
    return json.dumps(json_clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as writer:
        writer.write(dumps(value) + "\n")
        writer.flush()
        os.fsync(writer.fileno())
    move_path(temporary, path)
    flush_directory(path.parent)
