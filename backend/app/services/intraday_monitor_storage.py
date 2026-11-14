"""Bounded immutable sampling files and reference-aware worker-only expiry."""
from __future__ import annotations

from datetime import date
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil

from app.core import connect, data_dir, published_dir, raw_dir, work_dir, now_ms, dumps

VERSION = 'intraday-monitor-v1'
SAMPLE_SESSIONS = 20
EVENT_SESSIONS = 60
MAX_RAW_BYTES = 64 * 1024 * 1024
FILE_PATTERN = re.compile(r'intraday-monitor/\d{4}-\d{2}-\d{2}/monitor[_-][a-zA-Z0-9_-]+\.(?:parquet|json\.gz)\Z')


def file_hash(path):
    value = sha256()
    with Path(path).open('rb') as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def checked_path(item):
    root = {'raw': raw_dir(), 'published': published_dir()}.get(item.get('root'))
    relative = item.get('path')
    if root is None or not isinstance(relative, str) or not FILE_PATTERN.fullmatch(relative):
        raise ValueError('盘中采样文件身份或相对路径无效')
    target = root / relative
    if target.is_symlink() or any(part.is_symlink() for part in target.parents if part != root and root in part.parents):
        raise ValueError('盘中采样文件不接受软链接')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('盘中采样文件越出独立保存范围')
    return target


def verify_file(item):
    path = checked_path(item)
    if not path.is_file() or path.stat().st_size != item.get('bytes') or file_hash(path) != item.get('sha256'):
        raise ValueError('盘中采样文件缺失或SHA校验失败')
    return path


def _staging(batch_id):
    if not isinstance(batch_id, str) or not re.fullmatch(r'monitor[_-][a-zA-Z0-9_-]+', batch_id):
        raise ValueError('盘中采样批次身份无效')
    return work_dir() / 'intraday-monitor-staging' / batch_id


def publish_prepared(manifest, batch_id, day, *, register_manifest):
    """Recover exact registered files after a crash, without another quote GET."""
    temporary = _staging(batch_id)
    if manifest.get('version') != VERSION:
        raise ValueError('盘中采样准备版本无效')
    entries = [(manifest['quote_file'], 'quotes.parquet', 'published', '.parquet'),
               (manifest['raw_file'], 'source.json.gz', 'raw', '.json.gz')]
    for item, name, root, suffix in entries:
        if item.get('root') != root or item.get('path') != f'intraday-monitor/{day}/{batch_id}{suffix}':
            raise ValueError('盘中采样准备清单与批次身份不符')
        target = checked_path(item)
        source = temporary / name
        if target.exists():
            verify_file(item)
        elif (not source.is_file() or source.is_symlink() or source.stat().st_size != item.get('bytes')
              or file_hash(source) != item.get('sha256')):
            raise ValueError('盘中采样准备文件缺失或SHA不符，拒绝重采和覆盖')
    # Store exact SHA/path before any rename. Recovery can distinguish a
    # partially published pair from an unregistered, unrelated filesystem file.
    register_manifest({**manifest, 'publication_state': 'prepared'})
    for item, name, _root, _suffix in entries:
        target = checked_path(item)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            # work/raw/published can live on three independent Docker volumes.
            # Copy into the target filesystem, fsync, then publish atomically.
            pending = target.with_name('.' + target.name + '.pending')
            with (temporary / name).open('rb') as reader, pending.open('wb') as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            pending.chmod(0o600)
            if pending.stat().st_size != item['bytes'] or file_hash(pending) != item['sha256']:
                raise ValueError('盘中采样目标文件系统复制校验失败')
            if target.exists():
                verify_file(item)
                pending.unlink()
            else:
                pending.rename(target)
        verify_file(item)
    result = {**manifest, 'publication_state': 'published'}
    register_manifest(result)
    if temporary.exists():
        shutil.rmtree(temporary)
    return result


def recover_prepared(batch_id, day, manifest, *, register_manifest):
    temporary = _staging(batch_id)
    plan = temporary / 'manifest.json'
    if not manifest and plan.is_file() and not plan.is_symlink():
        if plan.stat().st_size > 131072:
            raise ValueError('盘中采样准备清单超出范围')
        manifest = json.loads(plan.read_text())
    if not manifest:
        # A crash before the complete preparation receipt has no accepted
        # capture. Only this job's exact private staging directory is disposable.
        if temporary.exists():
            if temporary.is_symlink():
                raise ValueError('盘中采样临时目录不可为软链接')
            shutil.rmtree(temporary)
        return None
    return publish_prepared(manifest, batch_id, day, register_manifest=register_manifest)


def save_capture(batch_id, day, result, *, calendar, generation, primary_snapshot_id, register_manifest=None):
    """Prepare, register, then publish two exact immutable sampling files."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('盘中采样保存日期无效')
    temporary = _staging(batch_id)
    temporary.mkdir(parents=True, exist_ok=False, mode=0o700)
    payload = dumps({'version': VERSION, 'date': day, 'batch_id': batch_id, 'capture': result}).encode()
    if len(payload) > MAX_RAW_BYTES:
        shutil.rmtree(temporary)
        raise ValueError('盘中采样来源超出本轮有界保存预算')
    (temporary / 'source.json.gz').write_bytes(gzip.compress(payload, compresslevel=6, mtime=0))
    (temporary / 'source.json.gz').chmod(0o600)
    fields = ('instrument_id', 'trade_date', 'latest_price', 'change_pct', 'open', 'high', 'low',
              'volume', 'turnover', 'previous_close', 'received_at_ms', 'source_time_ms',
              'quote_time_ms', 'quality', 'valid', 'reason', 'source_sha256', 'price_basis')
    quotes = [{key: quote.get(key) for key in fields} for quote in result['quotes_by_id'].values()]
    table = pa.Table.from_pylist(quotes) if quotes else pa.table({'instrument_id': pa.array([], type=pa.string())})
    pq.write_table(table, temporary / 'quotes.parquet', compression='zstd')
    (temporary / 'quotes.parquet').chmod(0o600)
    manifest = {'version': VERSION, 'calendar': calendar, 'market_snapshot_id': generation,
                'primary_snapshot_id': primary_snapshot_id, 'publication_state': 'prepared'}
    for name, field, root, suffix in [('quotes.parquet', 'quote_file', 'published', '.parquet'),
                                      ('source.json.gz', 'raw_file', 'raw', '.json.gz')]:
        source = temporary / name
        manifest[field] = {'root': root, 'path': f'intraday-monitor/{day}/{batch_id}{suffix}',
                           'sha256': file_hash(source), 'bytes': source.stat().st_size}
    # An atomic private preparation receipt survives a hard process exit even
    # in the small interval before SQLite's registered manifest is committed.
    receipt = temporary / 'manifest.tmp'
    receipt.write_text(dumps(manifest))
    receipt.chmod(0o600)
    receipt.rename(temporary / 'manifest.json')
    return publish_prepared(manifest, batch_id, day, register_manifest=register_manifest or (lambda _: None))


def load_capture(manifest, batch_id, day):
    if manifest.get('version') != VERSION:
        raise ValueError('盘中采样来源版本无效')
    verify_file(manifest['quote_file'])
    path = verify_file(manifest['raw_file'])
    with gzip.open(path, 'rb') as reader:
        raw = reader.read(MAX_RAW_BYTES + 1)
    if len(raw) > MAX_RAW_BYTES:
        raise ValueError('盘中采样来源解压超出保存预算')
    value = json.loads(raw)
    if (value.get('version'), value.get('batch_id'), value.get('date')) != (VERSION, batch_id, day):
        raise ValueError('盘中采样来源身份与批次不一致')
    return value['capture']


def cleanup(calendar, day, *, limit=50, event_limit=500):
    """Worker-only expiry with a durable, restartable per-file deletion receipt."""
    if not isinstance(calendar, list) or calendar != sorted(set(calendar)) or day not in calendar:
        return {'state': 'calendar_unknown', 'removed_batches': 0, 'removed_events': 0}
    position = calendar.index(day)
    event_cutoff = calendar[max(0, position - EVENT_SESSIONS + 1)]
    sample_cutoff = calendar[max(0, position - SAMPLE_SESSIONS + 1)]
    removed_events = removed_batches = 0
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
            return {'state': 'backup_protected', 'removed_batches': 0, 'removed_events': 0}
        expired = [row[0] for row in connection.execute(
            'SELECT event_id FROM intraday_monitor_event WHERE trade_date<? ORDER BY trade_date,event_id LIMIT ?',
            (event_cutoff, event_limit))]
        allowed = set(expired)
        connection.create_function('intraday_monitor_expiry_allowed', 1, lambda event: int(event in allowed))
        try:
            for event in expired:
                connection.execute('DELETE FROM intraday_monitor_evidence WHERE event_id=?', (event,))
                connection.execute('DELETE FROM intraday_monitor_event WHERE event_id=?', (event,))
                removed_events += 1
        finally:
            connection.create_function('intraday_monitor_expiry_allowed', 1, lambda _: 0)
    with connect() as connection:
        candidates = [dict(row) for row in connection.execute("""SELECT b.* FROM intraday_monitor_batch b
            JOIN job j USING(job_id) WHERE b.purged_at_ms IS NULL
            AND j.status NOT IN ('queued','running','paused') AND b.status NOT IN ('queued','running')
            AND (b.trade_date<? OR json_extract(b.manifest_json,'$.publication_state')='prepared')
            AND NOT EXISTS(SELECT 1 FROM intraday_monitor_evidence e WHERE e.batch_id=b.batch_id)
            ORDER BY b.trade_date,b.slot_key LIMIT ?""", (sample_cutoff, limit))]
    for batch in candidates:
        manifest = json.loads(batch['manifest_json'])
        files = [manifest[key] for key in ('quote_file', 'raw_file') if key in manifest]
        ledger = json.loads(batch['cleanup_json'])
        incomplete = manifest.get('publication_state') == 'prepared'
        for item in files:
            path = checked_path(item)
            phase = ledger.get(item['path'])
            before = None
            if path.exists():
                verify_file(item)  # No hash/network/directory scan in a write transaction.
                before = path.stat()
            elif phase not in ('pending', 'removed') and not incomplete:
                raise ValueError('盘中来源缺失且没有受控清理台账，拒绝猜测已删除')
            # Persist intent before unlink. A hard exit after unlink can be
            # resumed only for this exact registered path and SHA.
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                current = connection.execute('SELECT manifest_json,purged_at_ms,cleanup_json FROM intraday_monitor_batch WHERE batch_id=?',
                                             (batch['batch_id'],)).fetchone()
                if (not current or current['purged_at_ms'] is not None or current['manifest_json'] != batch['manifest_json']
                        or connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone()
                        or connection.execute('SELECT 1 FROM intraday_monitor_evidence WHERE batch_id=?', (batch['batch_id'],)).fetchone()):
                    break
                ledger = json.loads(current['cleanup_json'])
                ledger[item['path']] = 'pending'
                connection.execute('UPDATE intraday_monitor_batch SET cleanup_json=? WHERE batch_id=?',
                                   (dumps(ledger), batch['batch_id']))
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                if connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
                    break
                if path.exists():
                    after = path.stat()
                    if before is None or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        raise ValueError('盘中清理文件在验收后发生变化')
                    path.unlink()
                pending = path.with_name('.' + path.name + '.pending')
                pending.unlink(missing_ok=True)
                ledger[item['path']] = 'removed'
                connection.execute('UPDATE intraday_monitor_batch SET cleanup_json=? WHERE batch_id=?',
                                   (dumps(ledger), batch['batch_id']))
        else:
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                if (connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone()
                        or connection.execute('SELECT 1 FROM intraday_monitor_evidence WHERE batch_id=?', (batch['batch_id'],)).fetchone()):
                    continue
                connection.execute('UPDATE intraday_monitor_batch SET purged_at_ms=? WHERE batch_id=?', (now_ms(), batch['batch_id']))
                connection.execute("DELETE FROM retention_pin WHERE owner_kind='manual' AND owner_id=?", ('intraday-monitor:' + batch['batch_id'],))
                removed_batches += 1
            temporary = _staging(batch['batch_id'])
            if temporary.exists() and not temporary.is_symlink():
                shutil.rmtree(temporary)
    with connect() as connection:
        connection.execute('''DELETE FROM intraday_monitor_baseline WHERE trade_date<?
            AND trade_date<(SELECT max(later.trade_date) FROM intraday_monitor_baseline later
                WHERE later.instrument_id=intraday_monitor_baseline.instrument_id)''', (sample_cutoff,))
        connection.execute('DELETE FROM intraday_monitor_state WHERE trade_date<?', (sample_cutoff,))
    return {'state': 'completed', 'removed_batches': removed_batches, 'removed_events': removed_events}
