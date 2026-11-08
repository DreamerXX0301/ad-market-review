"""Carry a stopped Windows preview into an independent native tray profile."""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.fernet import Fernet

from app.infrastructure.config import APP_VERSION
from app.infrastructure.database import MIGRATIONS_DIR, check_sqlite_runtime, ensure_ready
from app.infrastructure.file_operations import (
    bound_file, flush_file, is_link_or_reparse, locked_file, move_path, open_exclusive_file,
    path_is_link_or_reparse,
)
from app.infrastructure.storage import atomic_json, dumps, hash_file

LEGACY_COMMIT = "9b39537add6cd098bfc5e99ace004aa4459e9895"
LEGACY_TRAY_COMMIT = "3839e431c22fe855fec2c94e8e9dbc2d6b2486c9"
LEGACY_NATIVE_COMMIT = "74880c2c9b138877e7e4bef04d8604afbd85ad31"
LEGACY_SCHEMA = 39
TARGET_SCHEMA = 40
_TARGET_MIGRATION = "040_auction_retention_30_days.sql"
_PREVIEW = "workbench-preview-v1"
_TRAY = "workbench-tray-v1"
_NATIVE_V1 = "workbench-tray-native-v1"
_NATIVE = "workbench-tray-native-v2"
_SOURCE_COMMITS = {_PREVIEW: LEGACY_COMMIT, _TRAY: LEGACY_TRAY_COMMIT, _NATIVE_V1: LEGACY_NATIVE_COMMIT}
_LEGACY_APP_VERSION = "0.1.24"
_DATABASE = ("business", "app.sqlite3")
_KEY = ("secrets", "credential-master.key")
_EXTRAS = ("published", "raw", "work")
_DISPOSABLE_WORK = {"worker.lock", ".condition-basis-cache", ".supplement-retention"}
_BUSINESS_TABLES = (
    "dataset_snapshot", "snapshot_artifact", "job", "strategy", "strategy_version",
    "tracking_round", "tracking_event", "sector_strategy", "sector_tracking_round",
    "watchlist_item", "research_report", "backtest_run", "market_auction_capture",
    "market_auction_snapshot", "market_auction_purge", "market_noon_snapshot",
    "market_noon_close_review", "market_noon_note", "market_intraday_capture", "ai_session",
)


def roots(license_directory: Path) -> tuple[Path, Path, Path, Path]:
    if license_directory.parent.name != "license-preview-v1" or not re.fullmatch(
        r"[0-9a-f]{20}", license_directory.name
    ):
        raise ValueError("工作台授权目录身份无效")
    base = license_directory.parents[1]
    return (
        base / _PREVIEW / license_directory.name,
        base / _TRAY / license_directory.name,
        base / _NATIVE_V1 / license_directory.name,
        base / _NATIVE / license_directory.name,
    )


def _ordinary(path: Path, *, directory: bool, optional: bool = False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return None
        raise
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or is_link_or_reparse(info):
        raise ValueError(f"导入路径必须是普通{'目录' if directory else '文件'}，{path.name}")
    return info


def select_import_source(preview_root: Path, tray_root: Path, native_v1_root: Path) -> Path | None:
    """Prefer the newest installed business database and stop on invalid evidence."""
    for root in (native_v1_root, tray_root, preview_root):
        for directory in (root.parent, root, root / "data", root / "data" / "business"):
            if _ordinary(directory, directory=True, optional=True) is None:
                break
        else:
            business = root / "data" / "business"
            if _ordinary(business / "app.sqlite3", directory=False, optional=True) is not None:
                _source_commit(root)
                return root
            if any((business / ("app.sqlite3" + suffix)).exists()
                   or path_is_link_or_reparse(business / ("app.sqlite3" + suffix))
                   for suffix in ("-wal", "-shm", "-journal")):
                raise ValueError("旧版业务库缺少主文件，请先核对该版本的恢复资料")
    return None


def _read_small(root: Path, parts: tuple[str, ...], limit: int) -> bytes:
    with bound_file(root, parts) as source:
        info = os.fstat(source.descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError(f"导入文件大小或类型无效，{parts[-1]}")
        with os.fdopen(source.descriptor, "rb", closefd=False) as file:
            result = file.read(limit + 1)
        if len(result) != info.st_size:
            raise ValueError(f"导入文件读取时发生变化，{parts[-1]}")
        return result


def _source_commit(root: Path) -> str:
    expected = _SOURCE_COMMITS.get(root.parent.name)
    try:
        marker = json.loads(_read_small(root, ("release.json",), 4096))
    except FileNotFoundError as error:
        raise ValueError("旧版业务库缺少发行标记，请先核对该版本的恢复资料") from error
    if expected is None or marker != {"format": 1, "source_commit": expected, "app_version": _LEGACY_APP_VERSION}:
        raise ValueError("旧版发行标记与本次导入支持的版本不符")
    return expected


def _legacy_lock(stack: ExitStack, path: Path, *, required: bool = False) -> None:
    if _ordinary(path, directory=False, optional=not required) is None:
        return
    try:
        stack.enter_context(locked_file(path, exclusive=True, blocking=False))
    except BlockingIOError:
        raise RuntimeError("旧版工作台仍在运行或收尾，请完整退出后重新导入") from None


def _files(root: Path, *, category: str | None = None):
    """Walk ordinary files and reject a reparse point before entering it."""
    for path in sorted(root.iterdir()):
        if path.name.startswith(".staging") or (
            category == "work" and (
                path.name in _DISPOSABLE_WORK or path.name.startswith("import-spill-")
            )
        ):
            continue
        info = path.lstat()
        if is_link_or_reparse(info):
            raise ValueError(f"旧版数据含符号链接或重解析点，{path.name}")
        if stat.S_ISDIR(info.st_mode):
            yield from _files(path)
        elif stat.S_ISREG(info.st_mode):
            yield path, info
        else:
            raise ValueError(f"旧版数据含特殊文件，{path.name}")


def _copy_source_database(data: Path, shadow: Path) -> None:
    shadow.parent.mkdir()
    for name in ("app.sqlite3", "app.sqlite3-wal", "app.sqlite3-journal"):
        required = name == "app.sqlite3"
        path = data / "business" / name
        if _ordinary(path, directory=False, optional=not required) is None:
            continue
        with bound_file(data, ("business", name)) as source:
            with os.fdopen(source.descriptor, "rb", closefd=False) as reader:
                with shadow.with_name(name).open("xb") as writer:
                    shutil.copyfileobj(reader, writer)


@contextmanager
def _temporary_environment(updates: dict[str, str]):
    original = {name: os.environ.get(name) for name in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _maintenance():
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from backup import check_db, connect_sqlite, perform_backup
    from restore import perform_restore

    return check_db, connect_sqlite, perform_backup, perform_restore


def _migration_shas() -> tuple[dict[str, str], dict[str, str]]:
    files = sorted(MIGRATIONS_DIR.glob("[0-9]*.sql"))
    if not files or files[-1].name != _TARGET_MIGRATION:
        raise ValueError("安装包缺少本次升级需要的迁移清单")
    current = {path.name: hash_file(path) for path in files}
    prefix = {name: sha for name, sha in current.items() if name != _TARGET_MIGRATION}
    return prefix, current


def _business_identity(database: Path, *, schema: int, expected: dict[str, str], check_db, connect_sqlite) -> dict:
    with closing(connect_sqlite(database, "ro")) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != schema:
            raise ValueError(f"业务库结构版本不符，期望 schema {schema}")
        migrations = dict(connection.execute("SELECT name, sha256 FROM _schema_migration"))
        if migrations != expected:
            raise ValueError("业务库迁移登记或 SHA 与发行清单不一致")
        check_db(connection)
        receipts = sha256()
        for row in connection.execute("SELECT * FROM market_auction_purge ORDER BY snapshot_id"):
            receipts.update(dumps(dict(row)).encode("utf-8") + b"\n")
        return {
            "auction_purge_sha256": receipts.hexdigest(),
            "business_clock": [dict(row) for row in connection.execute("SELECT * FROM business_clock")],
            "published_heads": [dict(row) for row in connection.execute(
                "SELECT * FROM published_head ORDER BY module, scope_key"
            )],
            "rows": {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in _BUSINESS_TABLES
            },
        }

def _safe_member(key: str) -> PurePosixPath:
    path = PurePosixPath(key)
    if path.as_posix() != key or not path.parts or path.is_absolute() or ".." in path.parts:
        raise ValueError("恢复集成员路径无效")
    if "\\" in key or ":" in key:
        raise ValueError("恢复集成员路径无效")
    if os.name == "nt":
        import ntpath
        if any(ntpath.isreserved(part) for part in path.parts):
            raise ValueError("恢复集成员使用 Windows 保留名称")
    return path


def _verify_and_extract(archive_path: Path, verification: Path, expected: dict, source_commit: str) -> None:
    with ZipFile(archive_path) as archive:
        members = archive.namelist()
        if len(members) != len(expected) + 1 or set(members) != {"recovery-manifest.json", *expected}:
            raise ValueError("压缩恢复集内容与清单不一致")
        manifest = json.loads(archive.read("recovery-manifest.json"))
        if manifest != {"format": 1, "source_commit": source_commit, "files": expected}:
            raise ValueError("压缩恢复集清单校验失败")
        for name, evidence in expected.items():
            member = _safe_member(name)
            target = verification.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            if target.stat().st_size != evidence["bytes"] or hash_file(target) != evidence["sha256"]:
                raise ValueError(f"压缩恢复集文件校验失败，{member.name}")


def _archive_recovery(archive_path: Path, bundle: Path, extras: dict[str, Path], source_commit: str) -> dict:
    sources = {
        "standard/" + path.relative_to(bundle).as_posix(): path for path, _ in _files(bundle)
    }
    sources.update({"extra/" + key: path for key, path in extras.items()})
    expected = {}
    seen = set()
    with ZipFile(archive_path, "x", compression=ZIP_DEFLATED, compresslevel=3) as archive:
        for name, source in sorted(sources.items()):
            _safe_member(name)
            canonical = name.casefold() if os.name == "nt" else name
            if canonical in seen:
                raise ValueError("压缩恢复集含重复文件名")
            seen.add(canonical)
            # The old app locks stay held during archival and independent extraction.
            before = source.stat()
            with source.open("rb") as file:
                checksum = file_digest(file, "sha256").hexdigest()
            archive.write(source, name)
            expected[name] = {"bytes": before.st_size, "sha256": checksum}
        archive.writestr("recovery-manifest.json", dumps({
            "format": 1, "source_commit": source_commit, "files": expected,
        }))
    flush_file(archive_path)
    return expected


def _legacy_files(data: Path, key: bytes, key_path: Path) -> tuple[dict[str, Path], int]:
    key_info = key_path.stat()
    extras = {}
    size = 0
    for category in ("business", "credentials", *_EXTRAS):
        folder = data / category
        _ordinary(folder, directory=True)
        for path, info in _files(folder, category=category):
            if (info.st_dev, info.st_ino) == (key_info.st_dev, key_info.st_ino):
                raise ValueError("检测到主密钥混入旧版业务数据")
            if 0 < info.st_size <= 128 and path.read_bytes().strip() == key.strip():
                raise ValueError("检测到主密钥副本混入旧版业务数据")
            size += info.st_size
            if category in _EXTRAS:
                extras[path.relative_to(data).as_posix()] = path
    return extras, size


def _restore_extras(verification: Path, data: Path, extras: dict[str, Path]) -> None:
    for key in extras:
        member = _safe_member(key)
        source = verification.joinpath("extra", *member.parts)
        if not source.exists():
            continue  # Already covered and verified by the standard backup.
        target = data.joinpath(*member.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        flush_file(source)
        move_path(source, target, replace=False)


def _stage_import(
    legacy_root: Path, stage: Path, record: dict, *, source_commit: str, available_before: int,
) -> dict:
    check_sqlite_runtime()
    check_db, connect_sqlite, perform_backup, perform_restore = _maintenance()
    old_data = legacy_root / "data"
    old_key = legacy_root.joinpath(*_KEY)
    key = _read_small(legacy_root, _KEY, 128)
    Fernet(key.strip())
    extras, total = _legacy_files(old_data, key, old_key)
    # Shadow + standard bundle + archive + independently extracted evidence +
    # restored copy can overlap. Count logical sizes as an upper-bound input.
    required = 4 * total + 256 * 1024**2
    if available_before < required:
        raise ValueError(f"可用空间不足，导入预计需要 {required / 1024**3:.1f} GiB")

    prefix, current = _migration_shas()
    shadow = stage / "shadow" / "app.sqlite3"
    _copy_source_database(old_data, shadow)
    before = _business_identity(
        shadow, schema=LEGACY_SCHEMA, expected=prefix, check_db=check_db, connect_sqlite=connect_sqlite,
    )

    bundle = stage / "standard"
    with _temporary_environment({
        "SHIXU_RAW_DIR": str(old_data / "raw"),
        "SHIXU_CREDENTIAL_MASTER_KEY_FILE": str(old_key),
    }):
        perform_backup(old_data, bundle, sqlite_path=shadow, published_dir=old_data / "published",
                       credential_dir=old_data / "credentials")
    shutil.rmtree(shadow.parent)
    manifest = json.loads((bundle / "backup-manifest.json").read_text(encoding="utf-8"))
    covered = {entry["path"] for entry in manifest["files"]}
    extras = {key: path for key, path in extras.items() if key not in covered}

    profile = stage / "profile"
    recovery = profile / "migration" / "recovery.zip"
    recovery.parent.mkdir(parents=True, mode=0o700)
    archived = _archive_recovery(recovery, bundle, extras, source_commit)
    verification = stage / "verification"
    _verify_and_extract(recovery, verification, archived, source_commit)
    shutil.rmtree(bundle)

    data = profile / "data"
    restored = perform_restore(verification / "standard", data)
    _restore_extras(verification, data, extras)
    database = data.joinpath(*_DATABASE)
    restored_identity = _business_identity(
        database, schema=LEGACY_SCHEMA, expected=prefix, check_db=check_db, connect_sqlite=connect_sqlite,
    )
    if before != restored_identity:
        raise ValueError("恢复后的业务日期、发布头或业务记录不一致")
    # Route every runtime path to the independent staged copy before migrating.
    with _temporary_environment({
        "SHIXU_DATA_DIR": str(data),
        "SHIXU_SQLITE_PATH": str(database),
        "SHIXU_PUBLISHED_DIR": str(data / "published"),
        "SHIXU_RAW_DIR": str(data / "raw"),
        "SHIXU_WORK_DIR": str(data / "work"),
        "SQLITE_TMPDIR": str(data / "work"),
    }):
        ensure_ready()
    after = _business_identity(
        database, schema=TARGET_SCHEMA, expected=current, check_db=check_db, connect_sqlite=connect_sqlite,
    )
    if restored_identity != after:
        raise ValueError("迁移后的业务日期、发布头或业务记录不一致")

    new_key = profile.joinpath(*_KEY)
    new_key.parent.mkdir(mode=0o700)
    with os.fdopen(open_exclusive_file(new_key), "wb") as file:
        file.write(key)
        file.flush()
        os.fsync(file.fileno())
    if _read_small(profile, _KEY, 128) != key:
        raise ValueError("新工作台主密钥复制校验失败")
    atomic_json(profile / "release.json", record)
    receipt = {
        "format": 1,
        "source_commit": source_commit,
        "source_profile": legacy_root.parent.name,
        "target_commit": record["source_commit"],
        "backup_id": restored["backup_id"],
        "archive_sha256": hash_file(recovery),
        "source_schema": LEGACY_SCHEMA,
        "schema": TARGET_SCHEMA,
        "migration_sha256": current[_TARGET_MIGRATION],
        "auction_purge_sha256": after["auction_purge_sha256"],
        "files_verified": len(archived),
        "business_rows": after["rows"],
        "paused_jobs": len(restored["paused_jobs"]),
        "available_bytes_before": available_before,
    }
    atomic_json(profile / "migration" / "receipt.json", receipt)
    return receipt


def import_legacy(legacy_root: Path, native_root: Path, release_record: dict) -> dict:
    """Import under the caller's native-instance lock, before creating native_root."""
    if native_root.exists() or path_is_link_or_reparse(native_root):
        raise ValueError("新版数据目录已经存在，导入只接受全新目录")
    _ordinary(native_root.parent, directory=True)
    _ordinary(legacy_root.parent, directory=True)
    _ordinary(legacy_root, directory=True)
    namespace = legacy_root.parent.name
    source_commit = _SOURCE_COMMITS.get(namespace)
    if (legacy_root.parent.parent != native_root.parent.parent or legacy_root.name != native_root.name
            or source_commit is None or native_root.parent.name != _NATIVE
            or not re.fullmatch(r"[0-9a-f]{20}", native_root.name)):
        raise ValueError("新旧工作台目录身份不同")
    target_commit = release_record.get("source_commit")
    if (release_record.get("format") != 1 or release_record.get("app_version") != APP_VERSION
            or target_commit in _SOURCE_COMMITS.values()
            or not isinstance(target_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", target_commit)):
        raise ValueError("新版发行标记无效")
    instance_lock = (legacy_root / "instance.lock" if namespace == _PREVIEW
                     else legacy_root.parent / f"instance-{legacy_root.name}.lock")
    with ExitStack() as locks:
        _legacy_lock(locks, instance_lock, required=True)
        _ordinary(legacy_root / "data", directory=True)
        _ordinary(legacy_root / "data" / "business", directory=True)
        _ordinary(legacy_root / "data" / "work", directory=True)
        _legacy_lock(locks, legacy_root / "data" / "work" / "worker.lock")
        _legacy_lock(locks, legacy_root / "data" / "business" / "supplement-access.lock")
        _legacy_lock(locks, legacy_root / "data" / "business" / "migration.lock")
        _source_commit(legacy_root)
        available_before = shutil.disk_usage(native_root.parent).free
        with TemporaryDirectory(prefix=".tray-import-", dir=native_root.parent) as temporary:
            stage = Path(temporary)
            receipt = _stage_import(
                legacy_root, stage, release_record, source_commit=source_commit, available_before=available_before,
            )
            move_path(stage / "profile", native_root, replace=False)
    available_after = shutil.disk_usage(native_root).free
    return {**receipt, "available_bytes_after": available_after}
