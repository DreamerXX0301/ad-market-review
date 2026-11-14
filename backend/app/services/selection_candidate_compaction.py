"""Pack historical candidate evidence in a new offline SQLite copy."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from struct import pack
from tempfile import TemporaryDirectory

from app.infrastructure.config import sqlite_path
from app.infrastructure.database import MIGRATIONS_DIR, _raw_connection, check_sqlite_runtime
from app.infrastructure.storage import hash_file, now_ms
from app.services.selection_candidate_storage import candidate_evidence_json, pack_evidence


_SCHEMA_VERSION = 47
_MIGRATION = '047_selection_candidate_packed_evidence.sql'
_TRIGGER = 'freeze_selection_candidate_update'
_INITIAL_MIGRATION = '001_initial.sql'
_CANDIDATE_KEY = 'run_id,instrument_id,decision,close_price_e8'
_EVIDENCE_COLUMNS = 'checks_json,metrics_json,topic_snapshot_json'
_BATCH_SIZE = 32


def _sqlite_paths(path: Path) -> tuple[Path, ...]:
    return tuple(Path(str(path) + suffix) for suffix in ('', '-wal', '-shm', '-journal'))


def _separate_paths(source: Path, output: Path) -> tuple[Path, Path]:
    source = source.expanduser().resolve(strict=True)
    output = output.expanduser().absolute()
    output = output.parent.resolve(strict=True) / output.name
    if not source.is_file():
        raise ValueError('压缩输入必须是独立恢复的 SQLite 文件')
    if source.parent.is_relative_to(output.parent) or output.parent.is_relative_to(source.parent):
        raise ValueError('输入和输出必须使用相互独立的目录')
    active = sqlite_path()
    if source == active or output == active or (active.exists() and source.samefile(active)):
        raise ValueError('必须使用独立恢复副本，不能压缩配置中的业务库')
    if any(os.path.lexists(path) for path in _sqlite_paths(source)[1:]):
        raise ValueError('输入库仍有 SQLite 日志文件，请使用已关闭并完成恢复的副本')
    if any(os.path.lexists(path) for path in _sqlite_paths(output)):
        raise FileExistsError('输出数据库或同名 SQLite 日志已存在')
    return source, output


def _open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA recursive_triggers=ON')
    connection.execute('PRAGMA busy_timeout=10000')
    return connection


def _file_identity(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _schema_version(connection: sqlite3.Connection) -> int:
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version not in (_SCHEMA_VERSION - 1, _SCHEMA_VERSION):
        raise ValueError(f'仅支持 schema 46 或 47 的恢复副本，实际为 {version}')
    known = {
        file.name: hash_file(file) for file in MIGRATIONS_DIR.glob('[0-9]*.sql')
        if int(file.name.split('_', 1)[0]) <= version
    }
    installed = dict(connection.execute('SELECT name,sha256 FROM _schema_migration'))
    if known != installed:
        raise ValueError('输入库的迁移记录与当前发布源码不一致')
    return version


def _apply_migration(connection: sqlite3.Connection) -> None:
    migration = MIGRATIONS_DIR / _MIGRATION
    body = ''
    # executescript() would commit the current transaction before running SQL.
    for line in migration.read_text(encoding='utf-8').splitlines(keepends=True):
        body += line
        if sqlite3.complete_statement(body):
            connection.execute(body)
            body = ''
    if body.strip():
        raise ValueError(f'迁移 {_MIGRATION} 包含未完成的 SQL')
    connection.execute('INSERT INTO _schema_migration VALUES (?,?,?)', (_MIGRATION, hash_file(migration), now_ms()))
    connection.execute(f'PRAGMA user_version={_SCHEMA_VERSION}')


def _schema(connection: sqlite3.Connection) -> tuple[tuple, ...]:
    return tuple(tuple(row) for row in connection.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name'
    ))


def _verify_integrity(connection: sqlite3.Connection) -> None:
    integrity = connection.execute('PRAGMA integrity_check').fetchone()
    if integrity is None or integrity[0] != 'ok':
        raise ValueError('SQLite integrity_check 未通过')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise ValueError('SQLite foreign_key_check 未通过')


def _candidate_digest(connection: sqlite3.Connection, version: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    packed = ',packed_evidence' if version >= _SCHEMA_VERSION else ''
    with closing(connection.execute(
        f'SELECT rowid,{_CANDIDATE_KEY},{_EVIDENCE_COLUMNS}{packed} '
        'FROM selection_candidate ORDER BY run_id,instrument_id'
    )) as rows:
        for row in rows:
            evidence = candidate_evidence_json(row)
            values = (*row[:5], *evidence)
            digest.update(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            digest.update(b'\n')
            count += 1
    return count, digest.hexdigest()


def _rowid_digests(connection: sqlite3.Connection) -> dict[str, str]:
    table_names = sorted(
        row['name'] for row in connection.execute('PRAGMA main.table_list')
        if row['type'] == 'table' and not row['wr'] and row['name'] != 'sqlite_schema'
    )
    digests = {}
    for name in table_names:
        quoted = '"' + name.replace('"', '""') + '"'
        digest = hashlib.sha256()
        for row in connection.execute(f'SELECT rowid FROM {quoted} ORDER BY rowid'):
            digest.update(pack('>q', row[0]))
        digests[name] = digest.hexdigest()
    return digests


def _pack_candidates(connection: sqlite3.Connection) -> int:
    definition = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=? AND tbl_name='selection_candidate'",
        (_TRIGGER,),
    ).fetchone()
    expected = next(
        line.rstrip(';') for line in (MIGRATIONS_DIR / _INITIAL_MIGRATION).read_text(encoding='utf-8').splitlines()
        if line.startswith(f'CREATE TRIGGER {_TRIGGER} ')
    )
    if definition is None or definition['sql'] != expected:
        raise ValueError('策略候选冻结触发器与迁移定义不一致')

    connection.execute(f'DROP TRIGGER {_TRIGGER}')
    updated = 0
    first_rowid = -(2 ** 63)
    while True:
        rows = connection.execute(
            f'SELECT rowid,{_EVIDENCE_COLUMNS} '
            'FROM selection_candidate WHERE rowid>=? AND packed_evidence IS NULL ORDER BY rowid LIMIT ?',
            (first_rowid, _BATCH_SIZE),
        ).fetchall()
        if not rows:
            break
        changes = []
        for row in rows:
            blob = pack_evidence(*row[1:])
            if blob is not None:
                changes.append((blob, row[0]))
        connection.executemany(
            "UPDATE selection_candidate SET checks_json='{}',metrics_json='{}',topic_snapshot_json='[]',"
            'packed_evidence=? WHERE rowid=?',
            changes,
        )
        updated += len(changes)
        if rows[-1][0] == (2 ** 63) - 1:
            break
        first_rowid = rows[-1][0] + 1
    connection.execute(definition['sql'])
    return updated


def compact_selection_candidates(source: Path, output: Path) -> dict:
    """Validate and compact the given offline input, publishing only a new file."""
    check_sqlite_runtime()
    source, output = _separate_paths(source, output)
    source_identity = _file_identity(source)
    source_bytes = source_identity[2]
    source_mode = stat.S_IMODE(source.stat().st_mode) & 0o777
    available_bytes = shutil.disk_usage(output.parent).free
    # The copied database coexists with either its rollback journal or the
    # vacuumed file. Allow one additional database size for temporary I/O.
    estimated_scratch_bytes = source_bytes * 3
    if available_bytes < estimated_scratch_bytes:
        raise OSError(f'输出文件系统空间不足，估计需要 {estimated_scratch_bytes} 字节，当前可用 {available_bytes} 字节')

    with TemporaryDirectory(prefix='.selection-compaction-', dir=output.parent) as directory:
        working_copy = Path(directory) / 'app.sqlite3'
        compacted_copy = Path(directory) / 'compacted.sqlite3'
        with closing(_open_readonly(source)) as original:
            version = _schema_version(original)
            expected = _candidate_digest(original, version)
            with closing(_raw_connection(working_copy)) as candidate:
                original.backup(candidate, pages=4096)
                candidate.execute('PRAGMA journal_mode=DELETE')
                with candidate:
                    candidate.execute('BEGIN IMMEDIATE')
                    if version < _SCHEMA_VERSION:
                        _apply_migration(candidate)
                    expected_schema = _schema(candidate)
                    packed = _pack_candidates(candidate)
                    if _schema(candidate) != expected_schema:
                        raise ValueError('压缩后 SQLite 表、索引或触发器定义发生意外变化')
                expected_rowids = _rowid_digests(candidate)
                # In-place VACUUM can reassign rowids in tables without indexes.
                candidate.execute('VACUUM INTO ?', (str(compacted_copy),))
            working_copy.unlink()

        with closing(_open_readonly(compacted_copy)) as candidate:
            _verify_integrity(candidate)
            _schema_version(candidate)
            if _schema(candidate) != expected_schema or _candidate_digest(candidate, _SCHEMA_VERSION) != expected:
                raise ValueError('压缩前后策略候选内容或 SQLite 定义不一致')
            if _rowid_digests(candidate) != expected_rowids:
                raise ValueError('压缩前后 SQLite 记录的 rowid 不一致')

        if _file_identity(source) != source_identity or any(os.path.lexists(path) for path in _sqlite_paths(source)[1:]):
            raise ValueError('压缩期间输入库发生变化，请对离线恢复副本重试')
        output_bytes = compacted_copy.stat().st_size
        with compacted_copy.open('rb') as stream:
            os.fchmod(stream.fileno(), source_mode)
            os.fsync(stream.fileno())
        if any(os.path.lexists(path) for path in _sqlite_paths(output)):
            raise FileExistsError('输出数据库或同名 SQLite 日志已存在')
        directory_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.link(compacted_copy, output)
            try:
                os.fsync(directory_fd)
            except OSError:
                if output.samefile(compacted_copy):
                    output.unlink()
                raise
        finally:
            os.close(directory_fd)

    return {
        'ok': True,
        'input_schema_version': version,
        'output_schema_version': _SCHEMA_VERSION,
        'verified_candidates': expected[0],
        'newly_packed_candidates': packed,
        'candidate_sha256': expected[1],
        'verified_rowid_tables': len(expected_rowids),
        'input_bytes': source_bytes,
        'output_bytes': output_bytes,
        'size_difference_bytes': source_bytes - output_bytes,
        'available_bytes_before': available_bytes,
        'estimated_scratch_bytes': estimated_scratch_bytes,
    }
