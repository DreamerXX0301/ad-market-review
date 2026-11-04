"""唯一worker在新授权的成功增量后清理竞价内嵌原件；不触碰公共行情文件。"""
from __future__ import annotations

from datetime import date, timedelta
import json

from app.core import connect, now_ms
from app.services.auction_integrity import LIMIT, validate_discovery, validate_snapshot
from app.services.supplement_access import supplement_access

VERSION = 'auction-retention-28d-v1'
DISCOVERY_POLICY = 'auction-discovery-retention-v1'
WINDOW_DAYS = 28
MAX_SNAPSHOTS = 32
MAX_BYTES = 128 * 1024 * 1024
MAX_REFERENCE_BYTES = 256 * 1024 * 1024
MAX_REFERENCE_ROWS = 100000
NOTICE = '保留28个自然日，到期随成功盘后增量自动清理快照正文及内嵌原件；未运行增量时暂留。小型采集日账与清理回执继续保留。'
_INTERNAL = {'_schema_migration', 'market_auction_capture', 'market_auction_purge',
             'market_auction_automation_run', 'market_auction_discovery', 'job_event', 'sync_batch', 'snapshot_gc',
             'market_storage_collection', 'market_storage_collection_file',
             'supplement_retention_run', 'supplement_retention_file',
             'workspace_purge', 'workspace_purge_file', 'workspace_purge_audit'}


def cutoff_date(anchor):
    try:
        valid = isinstance(anchor, str) and date.fromisoformat(anchor).isoformat() == anchor
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('竞价清理缺少已核验交易日')
    return (date.fromisoformat(anchor) - timedelta(days=WINDOW_DAYS - 1)).isoformat()


def retention_status(connection, day=None):
    """只读的小元数据；旧版备份没有032时仍可校验已有原件。"""
    result = {'version': VERSION, 'calendar_days': WINDOW_DAYS, 'notice': NOTICE, 'purged': False}
    if day is not None and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_auction_purge'").fetchone():
        row = connection.execute("SELECT anchor_date,cutoff_date,purged_at_ms FROM market_auction_purge WHERE trade_date=? AND status='purged'", (day,)).fetchone()
        if row:
            result.update(dict(row), purged=True)
    return result


def _anchor(connection, job_id, result):
    saved = connection.execute('SELECT * FROM job WHERE job_id=?', (job_id,)).fetchone()
    if not saved or saved['kind'] != 'increment' or saved['status'] not in ('succeeded', 'partial', 'no_change'):
        return None, '盘后任务尚未成功发布或已停止'
    if json.loads(saved['parameters_json']).get('auction_retention_policy') != VERSION:
        return None, '旧冻结任务未授权竞价清理'
    own = connection.execute("SELECT data_end FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1", (job_id,)).fetchone()
    if own:
        if saved['target_date'] is not None and saved['target_date'] != own['data_end']:
            return None, '本任务冻结日与发布日冲突'
        return own['data_end'], None
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
        if evidence.get('data_end') != head['data_end'] or evidence.get('snapshot_id') not in (head['snapshot_id'], generation):
            return None, '无变化任务结果与主发布指针不符'
        target = target or evidence['data_end']
    if target != head['data_end']:
        return None, '无变化任务缺少对应冻结交易日'
    return target, None


def _protected(connection, snapshots):
    targets = {row['snapshot_id'] for row in snapshots}
    if not targets:
        return set(), None
    if connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
        return targets, '业务备份正在保护竞价资料'
    from app.services.supplement_retention import _reference_pattern
    pattern = _reference_pattern(targets)
    protected = {row['snapshot_id'] for row in snapshots if connection.execute(
        "SELECT 1 FROM job WHERE job_id=? AND status IN ('queued','running','paused')", (row['job_id'],)).fetchone()}
    size = count = 0
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if table.startswith('sqlite_') or table in _INTERNAL:
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [row[1] for row in connection.execute('PRAGMA table_info(' + quoted + ')') if row[2].upper() in ('TEXT', '')]
        if not columns:
            continue
        fields = ','.join('"' + value.replace('"', '""') + '"' for value in columns)
        where = " WHERE status IN ('queued','running','paused')" if table == 'job' else ''
        for values in connection.execute('SELECT ' + fields + ' FROM ' + quoted + where):
            count += 1
            owner = values[columns.index('snapshot_id')] if table == 'market_auction_snapshot' else None
            for value in values:
                if not isinstance(value, str):
                    continue
                size += len(value.encode())
                if count > MAX_REFERENCE_ROWS or size > MAX_REFERENCE_BYTES:
                    raise ValueError('竞价业务引用核验达到有界预算，保留原件待后续核验')
                if '\\u' in value:
                    # Escaped or nested text must never conceal an explicit reference.
                    protected.update(targets - {owner})
                elif pattern is not None:
                    protected.update(set(pattern.findall(value)) - {owner})
    return protected, None


def _protection_snapshot(connection, snapshots):
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
        raise ValueError('竞价业务引用核验期间有外部写入，留待下一次增量重验')
    return protected, reason, after


def _discovery_authorized(connection, job_id):
    saved = connection.execute('SELECT parameters_json FROM job WHERE job_id=?', (job_id,)).fetchone()
    return bool(saved and json.loads(saved[0]).get('auction_discovery_retention_policy') == DISCOVERY_POLICY)


def _receipt(connection, snapshot, job_id, anchor, cutoff, status, reason, sequence, evidence_count, *, in_transaction=False):
    if not in_transaction:
        connection.execute('BEGIN IMMEDIATE')
    identifier = snapshot['snapshot_id']
    connection.create_function('auction_purge_allowed', 1, lambda value: int(value == identifier))
    try:
        connection.execute('''INSERT INTO market_auction_purge
            (snapshot_id,trade_date,source_job_id,cleanup_job_id,anchor_date,cutoff_date,content_sha256,
             payload_bytes,evidence_count,status,reason,check_sequence,checked_at_ms,purged_at_ms)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(snapshot_id) DO UPDATE SET
             cleanup_job_id=excluded.cleanup_job_id,anchor_date=excluded.anchor_date,cutoff_date=excluded.cutoff_date,
             evidence_count=excluded.evidence_count,status=excluded.status,reason=excluded.reason,
             check_sequence=excluded.check_sequence,checked_at_ms=excluded.checked_at_ms,purged_at_ms=excluded.purged_at_ms''',
            (identifier, snapshot['trade_date'], snapshot['job_id'], job_id, anchor, cutoff,
             snapshot['content_sha256'], len(snapshot['payload_json'].encode()), evidence_count, status,
             reason, sequence, now_ms(), now_ms() if status == 'purged' else None))
    finally:
        connection.create_function('auction_purge_allowed', 1, lambda *_: 0)
    if not in_transaction:
        connection.commit()


def _collect(job_id, result):
    report = {'version': VERSION, 'status': 'skipped', 'purged_snapshots': 0,
              'protected_snapshots': 0, 'removed_bytes': 0, 'warnings': []}
    with supplement_access(exclusive=True), connect() as ledger:
        anchor, reason = _anchor(ledger, job_id, result)
        if reason:
            report.update(reason=reason, skipped_reason=reason)
            return report
        cutoff = cutoff_date(anchor)
        report.update(anchor_date=anchor, cutoff_date=cutoff, calendar_days=WINDOW_DAYS, status='completed')
        sequence = ledger.execute('SELECT coalesce(max(check_sequence),0)+1 FROM market_auction_purge').fetchone()[0]
        snapshots = [dict(row) for row in ledger.execute('''SELECT s.* FROM market_auction_snapshot s
            LEFT JOIN market_auction_purge p USING(snapshot_id) WHERE s.trade_date<?
            ORDER BY coalesce(p.check_sequence,0),s.trade_date LIMIT ?''', (cutoff, MAX_SNAPSHOTS))]
        protected, protection_reason, guard_version = _protection_snapshot(ledger, snapshots)
        total_bytes = 0
        for snapshot in snapshots:
            identifier = snapshot['snapshot_id']; evidence_count = 0
            try:
                try:
                    payload = validate_snapshot(snapshot)
                except ValueError:
                    raise ValueError('竞价正文或内嵌来源未通过完整性核验，保留原记录') from None
                evidence_count = len(payload['evidence'])
                primary_size = len(snapshot['payload_json'].encode())
                discovery = ledger.execute('SELECT * FROM market_auction_discovery WHERE primary_snapshot_id=?',
                                           (identifier,)).fetchone()
                size = primary_size
                if discovery:
                    if not _discovery_authorized(ledger, job_id):
                        report['protected_snapshots'] += 1
                        _receipt(ledger, snapshot, job_id, anchor, cutoff, 'protected',
                                 '旧冻结任务未授权清理竞价发现，保留竞价及发现资料', sequence, evidence_count)
                        continue
                    try:
                        validate_discovery(dict(discovery))
                    except ValueError:
                        raise ValueError('竞价发现记录未通过完整性核验，保留原记录') from None
                    size += len(discovery['payload_json'].encode())
                if primary_size > LIMIT or total_bytes + size > MAX_BYTES:
                    report['warnings'].append('本轮竞价清理达到有界容量，剩余下次增量处理')
                    break
                total_bytes += size
                if identifier in protected:
                    report['protected_snapshots'] += 1
                    _receipt(ledger, snapshot, job_id, anchor, cutoff, 'protected',
                             protection_reason or '仍被业务引用或活动任务保护，保留竞价正文及原件', sequence, evidence_count)
                    continue
                if ledger.execute('PRAGMA data_version').fetchone()[0] != guard_version:
                    protected, protection_reason, guard_version = _protection_snapshot(ledger, snapshots)
                    if identifier in protected:
                        report['protected_snapshots'] += 1
                        _receipt(ledger, snapshot, job_id, anchor, cutoff, 'protected',
                                 protection_reason or '删除前出现业务引用，保留竞价正文及原件', sequence, evidence_count)
                        continue
                ledger.execute('BEGIN IMMEDIATE')
                if ledger.execute('PRAGMA data_version').fetchone()[0] != guard_version:
                    raise ValueError('竞价正文清理事务边界出现外部写入')
                current_anchor, stopped = _anchor(ledger, job_id, result)
                if stopped or current_anchor != anchor or ledger.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
                    raise ValueError('竞价清理边界的授权或备份保护已变化')
                if discovery and not _discovery_authorized(ledger, job_id):
                    raise ValueError('竞价发现的清理授权已变化')
                ledger.create_function('auction_purge_allowed', 1, lambda value: int(value == identifier))
                try:
                    if discovery and ledger.execute('''DELETE FROM market_auction_discovery
                            WHERE primary_snapshot_id=? AND content_sha256=?''',
                            (identifier, discovery['content_sha256'])).rowcount != 1:
                        raise ValueError('竞价发现身份已变化，未确认删除')
                    deleted = ledger.execute('DELETE FROM market_auction_snapshot WHERE snapshot_id=? AND content_sha256=?',
                                             (identifier, snapshot['content_sha256'])).rowcount
                finally:
                    ledger.create_function('auction_purge_allowed', 1, lambda *_: 0)
                if deleted != 1:
                    raise ValueError('竞价正文身份已变化，未确认删除')
                _receipt(ledger, snapshot, job_id, anchor, cutoff, 'purged', None, sequence, evidence_count, in_transaction=True)
                ledger.commit()
                report['purged_snapshots'] += 1; report['removed_bytes'] += size
            except Exception as error:
                ledger.rollback()
                reason = str(error) if isinstance(error, ValueError) else '竞价清理未完成：' + type(error).__name__
                report['warnings'].append(reason)
                _receipt(ledger, snapshot, job_id, anchor, cutoff, 'warning', reason, sequence, evidence_count)
        if report['warnings'] or report['protected_snapshots']:
            report['status'] = 'partial'
    return report


def after_increment(job, result=None):
    """旧任务无授权不访问存储；清理失败不能撤销已经成功的行情发布。"""
    parameters = job.get('parameters')
    if parameters is None:
        try:
            parameters = json.loads(job.get('parameters_json', '{}'))
        except (TypeError, ValueError):
            parameters = {}
    if job.get('kind') != 'increment' or not isinstance(parameters, dict) or parameters.get('auction_retention_policy') != VERSION:
        return {'version': VERSION, 'status': 'skipped', 'reason': '任务未冻结竞价清理策略'}
    identifier = job.get('job_id') or job.get('task_id')
    try:
        report = _collect(identifier, result)
    except Exception as error:
        report = {'version': VERSION, 'status': 'warning', 'reason': '竞价清理未完成：' + type(error).__name__,
                  'purged_snapshots': 0, 'protected_snapshots': 0, 'removed_bytes': 0, 'warnings': ['竞价到期清理未完成，原件保留待后续核验']}
    from app.services.task_service import append_event
    try:
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            append_event(connection, identifier, 'auction_retention',
                         '竞价到期清理已完成' if report['status'] == 'completed' else '竞价到期清理保留待处理项；已发布行情不受影响',
                         'warning' if report['status'] in ('warning', 'partial') else 'info', report)
    except Exception:
        pass
    return report
