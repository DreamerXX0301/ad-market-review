"""SQLite connections and checksummed, atomic, cross-process migrations."""
from __future__ import annotations
import contextlib
import fcntl
from pathlib import Path
import sqlite3
import threading
from typing import Iterator
from .config import data_dir, sqlite_path, published_dir, raw_dir, work_dir
from .storage import hash_file, now_ms
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
_ready = {}
_ready_lock = threading.RLock()

def _raw_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10, isolation_level="DEFERRED")
    connection.row_factory = sqlite3.Row
    # Fail closed on every ordinary connection; only the authorized cleanup worker
    # installs a transaction-local exact-ID implementation, then clears it.
    connection.create_function("workspace_purge_allowed", 2, lambda *_: 0)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA recursive_triggers=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def check_sqlite_runtime() -> None:
    version = sqlite3.sqlite_version_info
    patched = version >= (3, 51, 3) or ((3, 50, 7) <= version < (3, 51, 0)) or ((3, 44, 6) <= version < (3, 45, 0))
    if not patched:
        raise RuntimeError(f"SQLite {sqlite3.sqlite_version}未达到WAL修复要求，请使用SQLite≥3.51.3的Python运行库")


def ensure_ready() -> None:
    """Apply each immutable migration exactly once under a cross-process lock."""
    path = sqlite_path()
    files = sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"))
    signature = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    with _ready_lock:
        if path.exists() and _ready.get(str(path)) == signature:
            return
        check_sqlite_runtime()
        for directory in (data_dir(), path.parent, published_dir(), raw_dir(), work_dir()):
            directory.mkdir(parents=True, exist_ok=True)
        with (path.parent / "migration.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            connection = _raw_connection(path)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE IF NOT EXISTS _schema_migration (name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at_ms INTEGER NOT NULL)")
                connection.commit()
                for file in files:
                    sha = hash_file(file)
                    old = connection.execute("SELECT sha256 FROM _schema_migration WHERE name=?", (file.name,)).fetchone()
                    if old:
                        if old["sha256"] != sha:
                            raise RuntimeError(f"已执行迁移{file.name}校验和变化，请增加新迁移")
                        continue
                    lines = file.read_text(encoding="utf-8").splitlines()
                    body = "\n".join(line for line in lines if not line.strip().upper().startswith("PRAGMA ") and line.strip().upper() not in ("BEGIN IMMEDIATE;", "BEGIN;", "COMMIT;"))
                    version = max(connection.execute("PRAGMA user_version").fetchone()[0], int(file.name.split("_", 1)[0]))
                    # File names and SHA are controlled repository data, never request input.
                    script = "BEGIN IMMEDIATE;\n" + body + f"\nINSERT INTO _schema_migration VALUES ('{file.name}','{sha}',{now_ms()});\nPRAGMA user_version={version};\nCOMMIT;"
                    try:
                        connection.executescript(script)
                    except BaseException:
                        connection.rollback()
                        raise
                seed_connections(connection)
                connection.commit()
            finally:
                connection.close()
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        _ready[str(path)] = signature


@contextlib.contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    # An HTTP task can be cancelled while its synchronous route still runs.
    # Pin the shared file lease until this real thread commits/rolls back and
    # closes. Import lazily: access uses core configuration during assembly.
    from app.services.supplement_access import supplement_request_access
    with supplement_request_access():
        ensure_ready()
        connection = _raw_connection(sqlite_path())
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def seed_connections(connection: sqlite3.Connection) -> None:
    for identifier, provider, label, mode in (
        ("tickflow-public", "tickflow_public", "TickFlow 公共历史", "public"),
        ("fuyao", "fuyao", "扶摇金融数据", "key"),
        ("tickflow", "tickflow", "TickFlow 账号服务", "key"),
    ):
        if not connection.execute("SELECT 1 FROM provider_connection WHERE connection_id=?", (identifier,)).fetchone():
            connection.execute("INSERT INTO provider_connection(connection_id,provider_code,label,access_mode,credential_ref,enabled,created_at_ms) VALUES(?,?,?,?,?,?,?)", (identifier, provider, label, mode, None if mode == "public" else "unconfigured:" + identifier, 1, now_ms()))
