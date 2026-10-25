"""Bounded, reference-safe supplement GC for explicitly frozen worker jobs.

K-line source chains and saved business evidence are roots. Files are never
removed by directory age, and a failed collection cannot invalidate a successful
publication. No filesystem operation is performed inside a write transaction.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time

from app.core import connect, digest, new_id, now_ms, published_dir, work_dir
from app.services.supplement_access import supplement_access
from app.services.task_service import TaskCancelled, TaskPaused

VERSION = 'supplement-retention-250-v1'
WINDOW_SESSIONS = 250
MAX_DELETE_FILES = 4096
MAX_DELETE_BYTES = 256 * 1024 * 1024
MAX_INVENTORY_FILES = 2000000
MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_EDGES = 10000000
_CAS = re.compile(r'(artifacts|raw|indexes)/[0-9a-f]{64}\.json\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}\Z')
_REFERENCE_MARKER = re.compile(rb'"(?:[^"\\]*(?:artifact|_ref)|__supplement_(?!payload_v1")[^"\\]*|storage_version)"\s*:')
_cache = OrderedDict()
_cache_cost = 0


def _day(value):
    try:
        return isinstance(value, str) and bool(_DATE.fullmatch(value)) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _window(calendar, target):
    if not _day(target) or not isinstance(calendar, (list, tuple)) or not calendar:
        raise ValueError('缺少可核验的市场交易日历或补充观察日')
    if any(not _day(day) for day in calendar) or any(a >= b for a, b in zip(calendar, calendar[1:])) or target not in calendar:
        raise ValueError('市场日历日期无效、重复、无序或观察日不在日历中')
    observed = calendar[:calendar.index(target) + 1]
    return {'anchor_date': target, 'cutoff_date': observed[-WINDOW_SESSIONS] if len(observed) >= WINDOW_SESSIONS else observed[0],
            'window_sessions': WINDOW_SESSIONS, 'verified_sessions': min(len(observed), WINDOW_SESSIONS),
            'calendar_sufficient': len(observed) >= WINDOW_SESSIONS,
            'protected_annual_year': target[:4], 'main_history_policy': 'separate-five-year-window',
            'basis': '已核验市场交易日，含观察日；个股、指数及板块K线的五年窗口独立保留'}


def trim_document(document, calendar, target):
    """Pure trim; incomplete calendar evidence never invents a cutoff."""
    result = dict(document)
    try:
        window = _window(calendar, target)
        fields = ('public_pools', 'annual_pools', 'annual_down_pools', 'fund_flows', 'sector_index_history')
        for field in fields:
            values = document.get(field, {})
            if not isinstance(values, dict) or any(not _day(day) or day > target for day in values):
                raise ValueError('补充日期映射不可核验，未裁剪 '+field)
        if window['calendar_sufficient']:
            for field in fields:
                result[field] = {day: value for day, value in document.get(field, {}).items()
                                 if day >= window['cutoff_date'] or field in ('annual_pools', 'annual_down_pools') and day[:4] == target[:4]}
        result['retention'] = {'version': VERSION, **window, 'trim_applied': window['calendar_sufficient'],
                               'annual_exception': '保留当前自然年涨跌停统计证据；与250交易日窗口取并集',
                               'reason': None if window['calendar_sufficient'] else '已留存日历不足250交易日，未扩大推算，暂不裁剪'}
    except (ValueError, TypeError) as error:
        result['retention'] = {'version': VERSION, 'window_sessions': WINDOW_SESSIONS,
                               'trim_applied': False, 'calendar_sufficient': False, 'reason': str(error),
                               'main_history_policy': 'separate-five-year-window'}
    return result


def _safe(root, key):
    if not isinstance(key, str) or not key or '\\' in key or ':' in key:
        raise ValueError('补充引用路径无效')
    parts = PurePosixPath(key)
    if parts.is_absolute() or '..' in parts.parts or parts.as_posix() != key:
        raise ValueError('补充引用越出命名空间')
    path = root.joinpath(*parts.parts)
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError('补充文件或父目录不允许符号链接')
        if candidate == root:
            break
    return path


def _identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OBJECT_BYTES:
        raise ValueError('补充对象类型或体积不可核验')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _references(value):
    """Recognize CAS, compact fields, sector full/base/tail and raw references."""
    pending = [value]
    found = set()
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(name.startswith('__supplement_') and name not in ('__supplement_fields_v1', '__supplement_payload_v1') for name in item):
                raise ValueError('补充对象包含未知存储引用版本')
            if 'storage_version' in item and item['storage_version'] not in (None, 'supplement-index-v2'):
                raise ValueError('补充索引存储版本未知')
            if 'sha256' in item and any(name.endswith('_ref') and name != 'record_ref' for name in item):
                raise ValueError('发现未知补充引用类型')
            for name, relative in item.items():
                if name == 'record_ref':
                    expected = item.get('sha256')
                elif name == 'artifact' or name.endswith('_artifact'):
                    expected = item.get(name[:-len('artifact')] + 'sha256')
                else:
                    continue
                if relative is None and expected is None:
                    continue
                if not isinstance(relative, str) or not _CAS.fullmatch(relative) or not isinstance(expected, str) or not _SHA.fullmatch(expected):
                    raise ValueError('发现未知路径或缺少SHA的补充依赖')
                found.add((relative, expected))
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return tuple(sorted(found))


def _node(root, key):
    global _cache_cost
    path = _safe(root, key)
    identity = _identity(path)
    cache_key = (str(root), key, *identity)
    cached = _cache.get(cache_key)
    if cached is not None:
        _cache.move_to_end(cache_key)
        return cached[0]
    content = path.read_bytes()
    if _identity(path) != identity:
        raise ValueError('读取补充对象时文件身份发生变化')
    value = json.loads(content)
    if not isinstance(value, (dict, list)):
        raise ValueError('补充JSON对象结构不可核验')
    # The writer emits canonical UTF-8 JSON plus one LF. Verifying these exact
    # bytes avoids recursively re-encoding hundreds of MB of numerical payload.
    # Any escaped key can hide a dependency marker, including a backslash in
    # an otherwise valid *_artifact name. Escapes always use decoded validation.
    canonical = bool(_CAS.fullmatch(key)) and b'\\' not in content and content.endswith(b'\n') and hashlib.sha256(content[:-1]).hexdigest() == path.stem
    if _CAS.fullmatch(key) and not canonical and digest(value) != path.stem:
        raise ValueError('补充CAS内容标识不一致')
    if (key == 'head.json' or key.startswith('indexes/')) and (not isinstance(value, dict) or value.get('version') != 'market-supplement-v1'):
        raise ValueError('补充发布索引版本未知')
    # A canonical pointer-free provider payload needs no Python tree traversal.
    # Legacy/noncanonical JSON still takes the full decoded-key path, including
    # escaped key names. Only dependency tuples enter the process LRU.
    refs = _references(value) if not canonical or _REFERENCE_MARKER.search(content) else ()
    meta = {name: value.get(name) for name in ('target_date', 'date', 'as_of', 'snapshot_id', 'created_at_ms')} if isinstance(value, dict) else {}
    # Only dependency identities and tiny scalar metadata are retained, not JSON trees.
    if any(value is not None and not isinstance(value, (str, int, float, bool)) for value in meta.values()):
        raise ValueError('补充索引元信息格式不可核验')
    node = {'sha256': hashlib.sha256(content).hexdigest(), 'byte_count': len(content),
            'identity': identity, 'references': refs, **meta}
    cost = 512 + sum(len(a) + len(b) + 64 for a, b in refs)
    _cache[cache_key] = (node, cost)
    _cache_cost += cost
    while len(_cache) > 32768 or _cache_cost > 32 * 1024 * 1024:
        _, (_, old_cost) = _cache.popitem(last=False)
        _cache_cost -= old_cost
    return node


def clear_retention_cache():
    global _cache_cost
    _cache.clear()
    _cache_cost = 0


def plan_retention(root, calendar, target, *, protected_generations=(), max_files=MAX_DELETE_FILES, max_bytes=MAX_DELETE_BYTES, guard=None, graph_directory=None):
    """Read-only source plan over a disposable, bounded-memory SQLite DAG.

    Call close() (or use its context manager) after consuming the plan. Only the
    private temporary graph is written; source JSON and business SQL are not.
    """
    from app.services.supplement_retention_graph import build_plan
    window = _window(calendar, target)
    if not window['calendar_sufficient']:
        raise ValueError('已留存日历不足250交易日，本轮跳过物理清理')
    if not 1 <= max_files <= MAX_DELETE_FILES or not 0 < max_bytes <= MAX_DELETE_BYTES:
        raise ValueError('删除预算超出冻结上限')
    return build_plan(root, window, protected_generations=protected_generations,
                      max_files=max_files, max_bytes=max_bytes, node_loader=_node,
                      max_inventory_files=MAX_INVENTORY_FILES, max_edges=MAX_EDGES, guard=guard, graph_directory=graph_directory)


def _database_scope(job_id):
    """Read only tiny identities plus streamed business strings; no JSON trees."""
    from app.services.market_storage_gc import _IGNORED, _closure
    from app.services.workspace_common import current
    with connect() as connection:
        connection.execute('BEGIN')
        job = connection.execute('SELECT * FROM job WHERE job_id=?', (job_id,)).fetchone()
        if not job:
            raise ValueError('补充清理任务不存在')
        job = dict(job)
        params = json.loads(job['parameters_json'])
        if params.get('retention_policy_version') != VERSION:
            return None, '旧冻结任务未授权补充清理'
        if job['status'] not in ('running', 'succeeded', 'partial', 'no_change'):
            return None, '任务未处于可核验的发布后状态'
        if connection.execute("SELECT 1 FROM job WHERE job_id<>? AND (status IN ('queued','running','paused') OR lease_owner IS NOT NULL OR lease_expires_at_ms IS NOT NULL) LIMIT 1", (job_id,)).fetchone():
            return None, '存在其他待执行、运行或暂停任务，保护其恢复证据'
        if connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
            return None, '存在业务备份pin，本轮保留全部补充文件'
        day = current(connection)
        snapshots = [dict(row) for row in connection.execute("SELECT * FROM dataset_snapshot WHERE state='published'")]
        identifiers = {row['snapshot_id'] for row in snapshots}
        generations = {json.loads(row['lineage_json']).get('market_snapshot_id') for row in snapshots}
        known = identifiers | {value for value in generations if isinstance(value, str)}
        pattern = re.compile('|'.join(re.escape(value) for value in sorted(known, key=len, reverse=True))) if known else None
        roots = {row[0] for row in connection.execute('SELECT snapshot_id FROM published_head')}
        roots.update(row[0] for row in connection.execute('SELECT snapshot_id FROM retention_pin WHERE expires_at_ms IS NULL OR expires_at_ms>?', (now_ms(),)))
        unicode_fallback = False
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            if table.startswith('sqlite_') or table in _IGNORED or table in ('supplement_retention_run', 'supplement_retention_file'):
                continue
            quoted_table = '"'+table.replace('"', '""')+'"'
            fields = [row[1] for row in connection.execute('PRAGMA table_info('+quoted_table+')') if 'snapshot' in row[1] or row[1].endswith('_json')]
            if not fields:
                continue
            sql = 'SELECT '+','.join('"'+value.replace('"', '""')+'"' for value in fields)+' FROM '+quoted_table
            if table == 'job':
                sql += " WHERE status IN ('queued','running','paused')"
            for row in connection.execute(sql):
                for value in row:
                    if value is None:
                        continue
                    if not isinstance(value, str):
                        raise ValueError('业务引用字段类型不可核验')
                    if '\\u' in value:
                        # Escaped nested JSON can conceal a literal identifier.
                        # Protecting all known generations is conservative and
                        # avoids materializing enormous candidate JSON trees.
                        unicode_fallback = True
                        roots.update(known)
                    elif pattern is not None:
                        roots.update(pattern.findall(value))
        protected = _closure(snapshots, roots)
        guard_signature = digest({'day': day, 'roots': sorted(roots), 'protected': sorted(protected),
                                  'snapshots': sorted((row['snapshot_id'], row['lineage_json']) for row in snapshots),
                                  'job': {key: job[key] for key in ('job_id','kind','target_date','status','parameters_json','parameters_hash')}})
        active = connection.execute('SELECT * FROM supplement_retention_run WHERE job_id=? AND version=?', (job_id, VERSION)).fetchone()
        return {'business_date': day, 'job': job, 'protected_generations': sorted(value for value in protected if value in generations),
                'unicode_reference_fallback': unicode_fallback, 'guard_signature': guard_signature, 'existing_run': dict(active) if active else None}, None


def _emit(job_id, level, message, details):
    from app.services.workspace_common import log
    try:
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            log(connection, job_id, level, 'supplement_retention', message, details)
    except Exception:
        # A diagnostic cannot replace or downgrade the successful publication.
        pass


@contextmanager
def _ledger_transaction(connection):
    connection.execute('BEGIN IMMEDIATE')
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _registration_gate(connection, job_id, target, database_version):
    """Called under the short SQLite write reservation, before ledger changes."""
    if connection.execute('PRAGMA data_version').fetchone()[0] != database_version:
        raise ValueError('登记边界发现业务库外部写入，原保护roots需重新核验')
    job = connection.execute('SELECT parameters_json,status,target_date FROM job WHERE job_id=?', (job_id,)).fetchone()
    if not job or json.loads(job['parameters_json']).get('retention_policy_version') != VERSION or job['status'] not in ('running', 'succeeded', 'partial', 'no_change'):
        raise ValueError('登记边界的任务授权或状态已变化')
    if job['target_date'] is not None and job['target_date'] != target:
        raise ValueError('登记边界的冻结日期不匹配')
    clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
    if not clock or clock[0] != target:
        raise ValueError('登记边界的业务日变化')
    if connection.execute("SELECT 1 FROM job WHERE job_id<>? AND (status IN ('queued','running','paused') OR lease_owner IS NOT NULL OR lease_expires_at_ms IS NOT NULL) LIMIT 1", (job_id,)).fetchone():
        raise ValueError('登记边界出现其他活动任务')
    if connection.execute("SELECT 1 FROM retention_pin WHERE owner_kind='backup' LIMIT 1").fetchone():
        raise ValueError('登记边界出现备份pin')


def collect_after_publication(job_id, market, document, guard=None):
    """Worker-only entry, after a successful index publication. Never fetches."""
    started = time.perf_counter()
    report = {'version': VERSION, 'status': 'skipped', 'removed_files': 0, 'removed_bytes': 0}
    plan = None
    reference_signature = None
    try:
        with supplement_access(exclusive=True, guard=guard), connect() as ledger:
            # This same connection performs every ledger write. Its data_version
            # does not change for our own commits, but detects API writes, task
            # creation, backup pins and the worker's separate heartbeat thread.
            database_version = ledger.execute('PRAGMA data_version').fetchone()[0]
            def unchanged_database():
                nonlocal database_version
                if ledger.in_transaction:
                    raise ValueError('删除边界不能持有SQLite事务')
                current_version = ledger.execute('PRAGMA data_version').fetchone()[0]
                if current_version != database_version:
                    rechecked, blocked = _database_scope(job_id)
                    after_version = ledger.execute('PRAGMA data_version').fetchone()[0]
                    if blocked or rechecked['guard_signature'] != reference_signature or after_version != current_version:
                        raise ValueError('计划后业务引用、任务或pin发生变化，停止并保留原ledger待复核')
                    # Exclude worker_heartbeat timestamps / this job's lease;
                    # the same complete protection signature was re-read.
                    database_version = after_version
                    report['benign_database_rechecks'] = report.get('benign_database_rechecks', 0) + 1
            if guard:
                guard()
            state, skipped = _database_scope(job_id)
            if skipped:
                report['reason'] = skipped
                return report
            reference_signature = state['guard_signature']
            target = document.get('target_date')
            if document.get('retention', {}).get('version') != VERSION or document.get('retention', {}).get('trim_applied') is not True:
                raise ValueError('本次补充未通过已核验250交易日裁剪，跳过物理清理')
            status = market.status()
            if target != state['business_date'] or target != status.get('data_end') or document.get('snapshot_id') != status.get('snapshot_id'):
                raise ValueError('发布后基础行情或业务日已变化，保留原清理范围')
            if state['job']['target_date'] is not None and state['job']['target_date'] != target:
                raise ValueError('任务冻结观察日与本次发布不一致')
            root = published_dir()/'market-supplement'
            existing = state['existing_run']
            if existing and existing['status'] == 'completed':
                scope = json.loads(existing['scope_json'])
                if digest(scope) != existing['scope_sha256'] or scope.get('head_sha256') != _node(root, 'head.json')['sha256']:
                    raise ValueError('已完成清理的原授权或发布head已变化')
                report.update(status='completed', already_completed=True, collection_id=existing['collection_id'],
                              cutoff_date=scope['cutoff_date'], backlog_files=scope['backlog_files'])
                return report
            plan = plan_retention(root, market.calendar(), target, protected_generations=state['protected_generations'], guard=guard,
                                  graph_directory=work_dir()/'.supplement-retention')
            head = plan['nodes']['head.json']
            if not any(plan['nodes'][key].get('snapshot_id') == document['snapshot_id'] for key, _ in head['references']):
                raise ValueError('当前补充head不属于本次成功发布')
            unchanged_database()
            if existing:
                scope = json.loads(existing['scope_json'])
                if digest(scope) != existing['scope_sha256'] or scope.get('head_sha256') != head['sha256']:
                    raise ValueError('恢复清理的原授权或发布head已变化')
                collection_id = existing['collection_id']
                all_files = [dict(row) for row in ledger.execute('SELECT * FROM supplement_retention_file WHERE collection_id=? ORDER BY rowid', (collection_id,))]
                if sorted((row['object_key'], row['sha256'], row['byte_count']) for row in all_files) != sorted((row['object_key'], row['sha256'], row['byte_count']) for row in scope['files']):
                    raise ValueError('恢复清理的逐文件ledger不匹配原scope')
                files = [row for row in all_files if row['status'] == 'planned']
                pending_keys = {row['object_key'] for row in files}
                for row in files:
                    key = row['object_key']
                    current = plan['nodes'].get(key)
                    if key in plan['protected_keys'] or current and (current['sha256'] != row['sha256'] or current['byte_count'] != row['byte_count']):
                        raise ValueError('恢复清理发现新增引用或文件变化，拒绝继续')
                for key, node in plan['nodes'].items():
                    if key not in pending_keys and any(relative in pending_keys for relative, _ in node['references']):
                        raise ValueError('恢复清理发现原范围外新增父引用，拒绝继续')
            else:
                scope = plan['scope']
                files = scope['files']
                collection_id = new_id('supplement_gc')
                if guard:
                    guard()
                unchanged_database()
                with _ledger_transaction(ledger):
                    _registration_gate(ledger, job_id, target, database_version)
                    ledger.execute('INSERT INTO supplement_retention_run(collection_id,job_id,version,anchor_date,cutoff_date,scope_json,scope_sha256,status,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?)',
                                   (collection_id, job_id, VERSION, target, scope['cutoff_date'], json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(',', ':')), digest(scope), 'planned', now_ms()))
                    ledger.executemany('INSERT INTO supplement_retention_file(collection_id,object_key,sha256,byte_count,status) VALUES(?,?,?,?,?)',
                                       [(collection_id, row['object_key'], row['sha256'], row['byte_count'], 'planned') for row in files])
            if len(files) > MAX_DELETE_FILES or sum(row['byte_count'] for row in files) > MAX_DELETE_BYTES:
                raise ValueError('原清理授权超出逐轮删除预算')
            unchanged_database()
            with _ledger_transaction(ledger):
                _registration_gate(ledger, job_id, target, database_version)
                ledger.execute("UPDATE supplement_retention_run SET status='deleting' WHERE collection_id=? AND status='planned'", (collection_id,))
            report['collection_id'] = collection_id
            for row in files:
                if guard:
                    guard()
                unchanged_database()
                key = row['object_key']
                path = _safe(root, key)
                if path.exists():
                    current = _node(root, key)
                    if current['sha256'] != row['sha256'] or current['byte_count'] != row['byte_count']:
                        raise ValueError('删除前补充对象SHA或大小变化')
                    from app.core import hash_file
                    if hash_file(path) != row['sha256'] or _identity(path) != current['identity']:
                        raise ValueError('删除边界的文件身份或SHA变化')
                    unchanged_database()
                    path.unlink()
                    directory = os.open(path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                    outcome, removed = 'removed', row['byte_count']
                    report['removed_files'] += 1
                    report['removed_bytes'] += removed
                else:
                    outcome, removed = 'absent', 0
                with _ledger_transaction(ledger):
                    ledger.execute("UPDATE supplement_retention_file SET status=?,removed_bytes=? WHERE collection_id=? AND object_key=? AND status='planned'", (outcome, removed, collection_id, key))
            unchanged_database()
            with _ledger_transaction(ledger):
                ledger.execute("UPDATE supplement_retention_run SET status='completed',completed_at_ms=? WHERE collection_id=? AND status<>'completed'", (now_ms(), collection_id))
            report.update(status='completed', cutoff_date=scope['cutoff_date'], backlog_files=scope['backlog_files'],
                          protected_files=scope['protected_files'], inventory_files=scope['inventory_files'], planning_ms=plan['elapsed_ms'],
                          unicode_reference_fallback=state['unicode_reference_fallback'])
    except (TaskCancelled, TaskPaused) as error:
        report.update(status='interrupted', reason=type(error).__name__)
        raise
    except Exception as error:
        report.update(status='warning', reason=str(error))
    finally:
        if plan is not None:
            try:
                plan.close()
            except Exception as error:
                # The disposable graph cannot replace cancellation semantics or
                # downgrade a successfully published supplement snapshot.
                report['graph_cleanup_warning'] = str(error)
        report['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
        _emit(job_id, 'info' if report['status'] == 'completed' else 'warn', '补充数据滚动清理：'+report['status'], report)
    return report
