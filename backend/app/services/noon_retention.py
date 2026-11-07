"""午评专属28自然日清理；仅在已授权增量发布后由唯一worker调用。"""
from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import gzip
import json
import os
from pathlib import PurePosixPath
import re
import stat

from app.core import connect, dumps, now_ms, raw_dir
from app.infrastructure.file_operations import bound_file
from app.services.supplement_access import supplement_access

VERSION = 'noon-retention-28d-v1'
WINDOW_DAYS = 28
MAX_SNAPSHOTS = 32
MAX_FILES = 4096
MAX_BYTES = 256 * 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_JOB = re.compile(r'job_[0-9a-f]{32}\Z')
_NAME = re.compile(r'[A-Za-z0-9_-]{1,160}\.json\.gz\Z')
_INTERNAL = {'_schema_migration', 'market_noon_capture', 'market_noon_purge',
             'market_noon_close_review', 'market_noon_close_item', 'market_noon_close_attempt',
             'job_event', 'sync_batch', 'snapshot_gc', 'market_storage_collection',
             'market_storage_collection_file', 'supplement_retention_run',
             'supplement_retention_file', 'workspace_purge', 'workspace_purge_file',
             'workspace_purge_audit'}
_CHECK_SEQUENCE_SQL = """CASE WHEN json_type(p.manifest_json,'$.check_sequence')='integer'
    THEN json_extract(p.manifest_json,'$.check_sequence') ELSE 0 END"""


def _day(value):
    return isinstance(value, str) and date.fromisoformat(value).isoformat() == value


def cutoff_date(anchor):
    if not _day(anchor):
        raise ValueError('午评清理缺少已核验交易日')
    return (date.fromisoformat(anchor) - timedelta(days=WINDOW_DAYS - 1)).isoformat()


def validate_source(source, source_job_id=None):
    """只接受午评命名空间内的精确依赖；也供标准备份闭包复用。"""
    if not isinstance(source, dict):
        raise ValueError('午评原件清单结构无效')
    value = source.get('path')
    if not isinstance(value, str) or '\\' in value:
        raise ValueError('午评原件路径无效')
    path = PurePosixPath(value)
    parts = path.parts
    if (path.is_absolute() or len(parts) != 3 or parts[0] != 'noon'
            or not _JOB.fullmatch(parts[1]) or str(path) != value
            or not (_NAME.fullmatch(parts[2]) or parts[2] == 'manifest.json')):
        raise ValueError('午评原件路径越界或身份无效')
    if (not isinstance(source.get('stored_sha256'), str) or not _HASH.fullmatch(source['stored_sha256'])
            or not isinstance(source.get('sha256'), str) or not _HASH.fullmatch(source['sha256'])
            or isinstance(source.get('bytes'), bool) or not isinstance(source.get('bytes'), int)
            or not 0 < source['bytes'] <= MAX_SOURCE_BYTES):
        raise ValueError('午评原件SHA或大小无效')
    return {**source, 'owned': source_job_id is None or parts[1] == source_job_id}


def source_bytes(root, source, *, remove=False):
    """核验后只删除已绑定目录中的同一原件。"""
    source = validate_source(source)
    parts = PurePosixPath(source['path']).parts
    with bound_file(root, parts, delete=remove) as file:
        descriptor = file.descriptor
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != source['bytes']:
            raise ValueError('午评原件类型或大小与清单不符')
        chunks = []; size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > source['bytes']:
                raise ValueError('午评原件读取期间大小变化')
            chunks.append(chunk)
        content = b''.join(chunks)
        after = os.fstat(descriptor)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if signature(before) != signature(after) or sha256(content).hexdigest() != source['stored_sha256']:
            raise ValueError('午评原件SHA或文件身份变化')
        if parts[-1].endswith('.gz'):
            # gzip.decompress can allocate before its result is checked; bounded
            # streaming prevents a corrupt source from expanding without limit.
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(content)) as reader:
                original = reader.read(MAX_SOURCE_BYTES + 1)
            if len(original) > MAX_SOURCE_BYTES:
                raise ValueError('午评原件解压超过上限')
        else:
            original = content
        if sha256(original).hexdigest() != source['sha256']:
            raise ValueError('午评规范原件SHA不符')
        if remove:
            current = file.named_stat()
            if signature(current) != signature(after):
                raise ValueError('午评删除前文件身份变化')
            file.unlink()
        return content


def _anchor(connection, job_id, result):
    saved = connection.execute('SELECT * FROM job WHERE job_id=?', (job_id,)).fetchone()
    if not saved or saved['kind'] != 'increment' or saved['status'] not in ('succeeded', 'partial', 'no_change'):
        return None, '盘后任务尚未成功发布或已停止'
    parameters = json.loads(saved['parameters_json'])
    if parameters.get('noon_retention_policy') != VERSION:
        return None, '旧冻结任务未授权午评清理'
    own = connection.execute("SELECT data_end FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1", (job_id,)).fetchone()
    if own:
        anchor = own['data_end']
        if saved['target_date'] is not None and saved['target_date'] != anchor:
            return None, '本任务冻结日与发布日冲突'
        return anchor, None
    if saved['status'] != 'no_change':
        return None, '本任务没有已发布主行情证据'
    head = connection.execute("SELECT s.snapshot_id,s.data_end,s.lineage_json FROM published_head h JOIN dataset_snapshot s USING(snapshot_id) WHERE h.module='daily_raw' AND s.state='published' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    if not head:
        return None, '无变化任务没有可核验主行情'
    frozen = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_target_frozen' ORDER BY seq LIMIT 1", (job_id,)).fetchone()
    target = saved['target_date'] or (json.loads(frozen[0]).get('target_date') if frozen else None)
    evidence = result.get('data_status', {}) if isinstance(result, dict) else {}
    if evidence:
        generation = json.loads(head['lineage_json']).get('market_snapshot_id')
        if (evidence.get('data_end') != head['data_end']
                or evidence.get('snapshot_id') not in (head['snapshot_id'], generation)):
            return None, '无变化任务结果与主发布指针不符'
        target = target or evidence['data_end']
    if target != head['data_end']:
        return None, '无变化任务缺少对应冻结交易日'
    return target, None


def _protected(connection, snapshots):
    if not snapshots:
        return set(), None
    if connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
        return {row['snapshot_id'] for row in snapshots}, '业务备份正在保护午评资料'
    from app.services.supplement_retention import _reference_pattern
    targets = {row['snapshot_id']: {row['snapshot_id']} for row in snapshots}
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_noon_close_review'").fetchone():
        for row in snapshots:
            for (identifier,) in connection.execute('SELECT review_id FROM market_noon_close_review WHERE snapshot_id=?', (row['snapshot_id'],)):
                targets[identifier] = {row['snapshot_id']}
    protected = set()
    for row in snapshots:
        sources = json.loads(row['sources_json'])
        if not isinstance(sources, list) or len(sources) > MAX_FILES:
            protected.add(row['snapshot_id'])
            sources = sources[:MAX_FILES] if isinstance(sources, list) else []
        for source in sources:
            if isinstance(source, dict) and 'path' not in source:
                continue
            try:
                owned = validate_source(source, row['job_id'])['owned']
            except (TypeError, ValueError):
                # A malformed inventory retains its owner. Its original TEXT
                # still participates below, including references to good rows.
                protected.add(row['snapshot_id'])
                continue
            if owned:
                targets.setdefault(source['path'], set()).add(row['snapshot_id'])
    pattern = _reference_pattern(targets)
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if table.startswith('sqlite_') or table in _INTERNAL:
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [row[1] for row in connection.execute('PRAGMA table_info(' + quoted + ')')
                   if row[2].upper() in ('TEXT', '')]
        if not columns:
            continue
        where = " WHERE status IN ('queued','running','paused')" if table == 'job' else ''
        fields = ','.join('"' + value.replace('"', '""') + '"' for value in columns)
        for values in connection.execute('SELECT ' + fields + ' FROM ' + quoted + where):
            if table == 'market_noon_snapshot':
                values = _provenance_only_fragment(values, columns)
            owner = values[columns.index('snapshot_id')] if table in ('market_noon_snapshot', 'market_noon_note') else None
            for value in values:
                if not isinstance(value, str):
                    continue
                if '\\u' in value:
                    # Unknown nested escaping must protect rather than conceal
                    # a business reference. Ordinary app JSON keeps Unicode.
                    protected.update(targets)
                    protected.update(row['snapshot_id'] for row in snapshots)
                    continue
                if pattern is not None:
                    for match in pattern.findall(value):
                        protected.update(targets[match] - {owner})
    return protected, None


def _provenance_only_fragment(values, columns):
    """Unpin one verified copied label, never arbitrary noon prose or notes."""
    try:
        row = dict(zip(columns, values))
        encoded = row['payload_json']
        if sha256(encoded.encode()).hexdigest() != row['content_sha256']:
            return values
        payload = json.loads(encoded)
        if (payload.get('date') != row['trade_date']
                or payload.get('collected_start_at') != row['collected_start_at']):
            return values
        from app.services.noon_close_context import validated_frozen_provenance
        identifier = validated_frozen_provenance(payload)
        if identifier is None:
            return values
        # Everything except this exact system provenance value continues to
        # participate in the ordinary reference scan, including free text.
        payload['previous_review']['close_review_summary'].pop('review_id')
        result = list(values)
        result[columns.index('payload_json')] = dumps(payload)
        return result
    except (AttributeError, KeyError, TypeError, ValueError):
        return values


def _protection_snapshot(connection, snapshots):
    """同一SQLite读快照内核验全部引用；外部提交变化则保守暂停本轮。"""
    before = connection.execute('PRAGMA data_version').fetchone()[0]
    connection.execute('BEGIN')
    try:
        protected, reason = _protected(connection, snapshots)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    after = connection.execute('PRAGMA data_version').fetchone()[0]
    if before != after:
        raise ValueError('午评业务引用核验期间有外部写入，留待下一次增量重验')
    return protected, reason, after


def _read_manifest(job_id):
    """终止且没有正文的任务只读它的固定manifest，不枚举可疑孤儿文件。"""
    if not isinstance(job_id, str) or not _JOB.fullmatch(job_id):
        raise ValueError('午评终止任务身份无效')
    with bound_file(raw_dir(), ('noon', job_id, 'manifest.json')) as file:
        descriptor = file.descriptor
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SOURCE_BYTES:
            raise ValueError('午评终止任务清单类型或大小无效')
        content = b''
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            content += chunk
            if len(content) > MAX_SOURCE_BYTES:
                raise ValueError('午评终止任务清单超过上限')
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('午评终止任务清单读取期间变化')
        return content


def _orphan_candidates(connection, cutoff, report, *, job_id, anchor, check_sequence):
    rows = connection.execute('''SELECT c.trade_date,c.job_id FROM market_noon_capture c
        JOIN job j ON j.job_id=c.job_id LEFT JOIN market_noon_snapshot s ON s.job_id=c.job_id
        LEFT JOIN market_noon_purge p ON p.snapshot_id='noon_orphan_' || substr(c.job_id,5)
        WHERE c.trade_date<? AND s.snapshot_id IS NULL AND j.status IN ('failed','cancelled')
        AND NOT EXISTS(SELECT 1 FROM market_noon_purge p WHERE p.trade_date=c.trade_date AND p.status='purged')
        ORDER BY ''' + _CHECK_SEQUENCE_SQL + ',c.trade_date LIMIT ?', (cutoff, MAX_SNAPSHOTS)).fetchall()
    candidates = []
    for row in rows:
        row = dict(row); identifier = 'noon_orphan_' + row['job_id'].removeprefix('job_')
        previous = connection.execute('SELECT manifest_json FROM market_noon_purge WHERE snapshot_id=?', (identifier,)).fetchone()
        old = json.loads(previous[0]) if previous else None
        payload = dumps({'date': row['trade_date'], 'version': 'noon-snapshot-v1',
                         'kind': 'failed_capture_cleanup', 'job_id': row['job_id']})
        snapshot = {**row, 'snapshot_id': identifier, 'content_sha256': sha256(payload.encode()).hexdigest(),
                    'payload_json': payload, 'orphan': True}
        try:
            try:
                content = _read_manifest(row['job_id']); document = json.loads(content)
                if (not isinstance(document, dict) or document.get('version') != 'noon-snapshot-v1'
                        or document.get('job_id') != row['job_id'] or document.get('date') != row['trade_date']
                        or not isinstance(document.get('sources'), list)):
                    raise ValueError('午评终止任务清单身份不符')
                source = {'path': f"noon/{row['job_id']}/manifest.json", 'bytes': len(content),
                          'sha256': sha256(content).hexdigest(), 'stored_sha256': sha256(content).hexdigest()}
                sources = [*document['sources'], source]
            except FileNotFoundError:
                # A crash may have unlinked the manifest last, before marking
                # the exact ledger purged. Only its verified frozen list resumes.
                if not old or not old.get('inventory_verified'):
                    raise ValueError('午评终止任务没有可核验原件清单，未扫描或猜测孤儿路径') from None
                sources = old['files']
            candidates.append({**snapshot, 'sources_json': dumps(sources)})
        except (OSError, TypeError, ValueError):
            reason = '终止午评任务原件清单未通过核验，保留未追踪资料'
            report['warnings'].append(reason)
            manifest = old or {'snapshot_id': identifier, 'source_job_id': row['job_id'],
                               'content_sha256': snapshot['content_sha256'], 'files': [],
                               'inventory_verified': False}
            _save_receipt(connection, snapshot, job_id, anchor, cutoff, 'warning', manifest, reason,
                          check_sequence=check_sequence)
    return candidates


def _orphan_is_terminal(connection, snapshot):
    return bool(connection.execute('''SELECT 1 FROM market_noon_capture c JOIN job j ON j.job_id=c.job_id
        WHERE c.job_id=? AND c.trade_date=? AND j.status IN ('failed','cancelled')
        AND NOT EXISTS(SELECT 1 FROM market_noon_snapshot s WHERE s.job_id=c.job_id)''',
        (snapshot['job_id'], snapshot['trade_date'])).fetchone())


def _emit(job_id, report):
    from app.services.task_service import append_event
    try:
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            append_event(connection, job_id, 'noon_retention',
                         '午评到期清理已完成' if report['status'] == 'completed' else '午评到期清理保留待处理项；盘后行情不受影响',
                         'warning' if report['status'] in ('warning', 'partial') else 'info', report)
    except Exception:
        pass


def _save_receipt(connection, snapshot, job_id, anchor, cutoff, status, manifest, reason=None, *, check_sequence):
    manifest.update(check_sequence=check_sequence, last_checked_at_ms=now_ms())
    connection.execute('BEGIN IMMEDIATE')
    connection.execute('''INSERT INTO market_noon_purge
        (snapshot_id,trade_date,job_id,anchor_date,cutoff_date,status,manifest_json,removed_bytes,created_at_ms,finished_at_ms,reason)
        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(snapshot_id) DO UPDATE SET
        job_id=excluded.job_id,anchor_date=excluded.anchor_date,cutoff_date=excluded.cutoff_date,
        status=excluded.status,manifest_json=excluded.manifest_json,removed_bytes=excluded.removed_bytes,
        finished_at_ms=excluded.finished_at_ms,reason=excluded.reason''',
        (snapshot['snapshot_id'], snapshot['trade_date'], job_id, anchor, cutoff, status, dumps(manifest),
         sum(value['bytes'] for value in manifest['files'] if value['state'] == 'removed'),
         now_ms(), None, reason))
    connection.commit()


def _collect(job_id, result):
    report = {'version': VERSION, 'status': 'skipped', 'purged_snapshots': 0, 'protected_snapshots': 0,
              'purged_failed_captures': 0, 'removed_files': 0, 'removed_bytes': 0, 'warnings': []}
    with supplement_access(exclusive=True), connect() as ledger:
        anchor, reason = _anchor(ledger, job_id, result)
        if reason:
            report['reason'] = reason
            return report
        cutoff = cutoff_date(anchor)
        report.update(anchor_date=anchor, cutoff_date=cutoff, calendar_days=WINDOW_DAYS)
        # Persist a logical check round rather than a wall-clock cursor: equal
        # milliseconds or clock rollback must not let retained rows starve others.
        check_sequence = ledger.execute('SELECT coalesce(max(' + _CHECK_SEQUENCE_SQL +
                                        '),0)+1 FROM market_noon_purge p').fetchone()[0]
        snapshots = [dict(row) for row in ledger.execute('''SELECT s.* FROM market_noon_snapshot s
            LEFT JOIN market_noon_purge p USING(snapshot_id) WHERE s.trade_date<? ORDER BY ''' +
            _CHECK_SEQUENCE_SQL + ',s.trade_date LIMIT ?', (cutoff, MAX_SNAPSHOTS))]
        snapshots += _orphan_candidates(ledger, cutoff, report, job_id=job_id, anchor=anchor,
                                        check_sequence=check_sequence)
        protected, reason, guard_version = _protection_snapshot(ledger, snapshots)
        if reason:
            report.update(reason=reason, protected_snapshots=len(snapshots))
            return report
        report['status'] = 'completed'
        total_files = total_bytes = 0
        for snapshot in snapshots:
            identifier = snapshot['snapshot_id']
            previous = ledger.execute('SELECT * FROM market_noon_purge WHERE snapshot_id=?', (identifier,)).fetchone()
            old = json.loads(previous['manifest_json']) if previous else None
            manifest = {'snapshot_id': identifier, 'source_job_id': snapshot['job_id'],
                        'content_sha256': snapshot['content_sha256'], 'files': [],
                        'inventory_verified': bool(old and old.get('inventory_verified'))}
            try:
                if sha256(snapshot['payload_json'].encode()).hexdigest() != snapshot['content_sha256']:
                    raise ValueError('午评正文SHA不符，保留原记录')
                sources = json.loads(snapshot['sources_json'])
                if not isinstance(sources, list) or len(sources) > MAX_FILES:
                    raise ValueError('午评原件清单无效或超过预算')
                seen = set()
                for value in sources:
                    if isinstance(value, dict) and 'path' not in value:
                        continue
                    source = validate_source(value, snapshot['job_id'])
                    if source['path'] in seen:
                        raise ValueError('午评原件清单重复')
                    seen.add(source['path'])
                    source['state'] = 'pending' if source['owned'] else 'external_preserved'
                    if old and old.get('inventory_verified'):
                        prior = next((entry for entry in old.get('files', []) if entry['path'] == source['path']), None)
                        if (old.get('content_sha256') != snapshot['content_sha256'] or not prior
                                or any(prior.get(key) != source.get(key) for key in ('path', 'sha256', 'stored_sha256', 'bytes'))):
                            raise ValueError('午评清理冻结清单与正文冲突')
                        if prior.get('state') in ('removed', 'already_absent'):
                            source['state'] = prior['state']
                    manifest['files'].append(source)
                if identifier in protected:
                    report['protected_snapshots'] += 1
                    _save_receipt(ledger, snapshot, job_id, anchor, cutoff, 'protected', manifest, '仍被业务引用，保留午评及其原件',
                                  check_sequence=check_sequence)
                    continue
                for source in manifest['files']:
                    if source['state'] != 'pending':
                        continue
                    try:
                        source_bytes(raw_dir(), source)
                    except FileNotFoundError:
                        if not old or not old.get('inventory_verified') or previous['status'] not in ('planned', 'deleting', 'warning'):
                            raise ValueError('午评原件缺失，未以缺失推定可安全清理') from None
                        source['state'] = 'already_absent'
                manifest['inventory_verified'] = True
                total_files += len(manifest['files'])
                total_bytes += sum(source['bytes'] for source in manifest['files'])
                if total_files > MAX_FILES or total_bytes > MAX_BYTES:
                    report['warnings'].append('本轮午评清理达到有界预算，剩余下次增量处理')
                    break
                _save_receipt(ledger, snapshot, job_id, anchor, cutoff, 'planned', manifest, check_sequence=check_sequence)
                for source in manifest['files']:
                    if source['state'] != 'pending':
                        continue
                    current_anchor, stopped = _anchor(ledger, job_id, result)
                    version = ledger.execute('PRAGMA data_version').fetchone()[0]
                    if version != guard_version:
                        protected, backup, guard_version = _protection_snapshot(ledger, snapshots)
                        if backup:
                            raise ValueError('午评删除边界的业务引用无法稳定核验')
                    if stopped or current_anchor != anchor or identifier in protected:
                        raise ValueError('午评删除边界的授权或业务引用已变化')
                    if snapshot.get('orphan') and not _orphan_is_terminal(ledger, snapshot):
                        raise ValueError('午评终止任务状态变化，原件未删除')
                    source_bytes(raw_dir(), source, remove=True)
                    source['state'] = 'removed'
                    report['removed_files'] += 1
                    report['removed_bytes'] += source['bytes']
                    _save_receipt(ledger, snapshot, job_id, anchor, cutoff, 'deleting', manifest, check_sequence=check_sequence)
                # No external file read is performed while holding the writer
                # reservation. API mutations remain excluded by the EX lease.
                version = ledger.execute('PRAGMA data_version').fetchone()[0]
                if version != guard_version:
                    protected, backup, guard_version = _protection_snapshot(ledger, snapshots)
                    if backup or identifier in protected:
                        raise ValueError('午评正文清理前出现业务引用')
                ledger.execute('BEGIN IMMEDIATE')
                if ledger.execute('PRAGMA data_version').fetchone()[0] != guard_version:
                    raise ValueError('午评正文清理事务边界出现外部写入')
                current_anchor, stopped = _anchor(ledger, job_id, result)
                if stopped or current_anchor != anchor or ledger.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
                    raise ValueError('午评正文清理边界的授权已变化')
                if snapshot.get('orphan'):
                    if not _orphan_is_terminal(ledger, snapshot):
                        raise ValueError('午评终止任务已变化，保留永久名额及现有正文')
                else:
                    ledger.create_function('noon_purge_allowed', 1, lambda value, exact=identifier: int(value == exact))
                    try:
                        ledger.execute('DELETE FROM market_noon_snapshot WHERE snapshot_id=? AND content_sha256=?', (identifier, snapshot['content_sha256']))
                    finally:
                        ledger.create_function('noon_purge_allowed', 1, lambda *_: 0)
                ledger.execute("UPDATE market_noon_purge SET status='purged',finished_at_ms=?,reason=NULL WHERE snapshot_id=?", (now_ms(), identifier))
                ledger.commit()
                report['purged_failed_captures' if snapshot.get('orphan') else 'purged_snapshots'] += 1
            except Exception as error:
                ledger.rollback()
                reason = str(error) if isinstance(error, ValueError) else '午评清理未完成：' + type(error).__name__
                report['warnings'].append(reason)
                _save_receipt(ledger, snapshot, job_id, anchor, cutoff, 'warning', manifest, reason, check_sequence=check_sequence)
        if report['warnings'] or report['protected_snapshots']:
            report['status'] = 'partial'
    return report


def after_increment(job, result=None):
    """清理错误只能产生警告，不能撤销、降级或阻断已经成功的盘后发布。"""
    parameters = job.get('parameters')
    if parameters is None:
        try:
            parameters = json.loads(job.get('parameters_json', '{}'))
        except (TypeError, ValueError):
            parameters = {}
    if not isinstance(parameters, dict):
        parameters = {}
    if job.get('kind') != 'increment' or parameters.get('noon_retention_policy') != VERSION:
        return {'version': VERSION, 'status': 'skipped', 'reason': '任务未冻结午评清理策略'}
    identifier = job.get('job_id') or job.get('task_id')
    try:
        report = _collect(identifier, result)
    except Exception as error:
        report = {'version': VERSION, 'status': 'warning', 'reason': '午评清理未完成：' + type(error).__name__,
                  'purged_snapshots': 0, 'removed_files': 0, 'removed_bytes': 0}
    _emit(identifier, report)
    return report
