"""Durable gate and bounded receipts for one explicitly started increment."""
from __future__ import annotations

import json
import statistics
import time

from fastapi import HTTPException

from app.core import connect, data_dir, now_ms, dumps, new_id
from app.services.task_service import _activity_view

SUCCESS = {'succeeded', 'partial', 'no_change'}
STOPPED = {'failed', 'cancelled', 'paused'}
LINK_STAGES = ('followup_queued', 'line_alert_check_queued', 'listing_status_check_queued', 'increment_retry_queued')
LOCK_VERSION = 'increment-workbench-lock-v1'
LOCK_SETTING = 'increment-workbench-session'
_history_cache = {}


def _chain(connection, root):
    pending = [root['job_id']]
    seen = set()
    items, waiting, problems = [], [], []
    while pending and len(seen) < 32:
        identifier = pending.pop(0)
        if identifier in seen:
            continue
        seen.add(identifier)
        row = connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone()
        if row is None:
            problems.append('后续任务记录缺失，请到任务记录核查')
            continue
        item = _activity_view(connection, row)
        if row['status'] == 'paused':
            paused = connection.execute("SELECT occurred_at_ms FROM job_event WHERE job_id=? AND stage='paused' ORDER BY seq DESC LIMIT 1", (identifier,)).fetchone()
            item['stopped_at_ms'] = paused[0] if paused else row['created_at_ms']
        items.append(item)
        events = connection.execute("""SELECT stage,details_json FROM job_event WHERE job_id=?
            AND (stage IN ('chain_authorized','followup_queued','chain_blocked',
                'line_alert_check_queued','listing_status_check_queued','increment_retry_queued')
                OR stage IN ('workspace_followup_started','workspace_followup_completed','workspace_followup_blocked')
                OR stage LIKE 'local_check_queue_error:%') ORDER BY seq LIMIT 128""", (identifier,)).fetchall()
        stages = {event['stage'] for event in events}
        for event in events:
            if event['stage'] in LINK_STAGES:
                child = json.loads(event['details_json']).get('next_task_id')
                if isinstance(child, str) and child and child not in seen:
                    pending.append(child)
        if row['status'] in SUCCESS:
            if 'chain_blocked' in stages:
                problems.append('后续数据链未能排队，请到任务记录查看失败原因')
            elif ('chain_authorized' in stages or
                  row['kind'] == 'increment' and json.loads(row['parameters_json']).get('increment_session_version') == LOCK_VERSION or
                  json.loads(row['parameters_json']).get('increment_chain') is True) and 'followup_queued' not in stages:
                waiting.append(identifier)
            parameters = json.loads(row['parameters_json'])
            if row['kind'] == 'increment':
                from .sync_workspace_followup import enabled, COMPLETE, BLOCKED
                if enabled(parameters):
                    if BLOCKED in stages:
                        problems.append('本次数据或同步后计算存在缺口，策略/提醒未全部执行；见任务记录')
                    elif COMPLETE not in stages:
                        waiting.append(identifier)
                for marker, stage, label in (
                    ('line_alert_check_version', 'line_alert_check_queued', '划线提醒'),
                    ('listing_status_check_version', 'listing_status_check_queued', '上市身份'),
                ):
                    if not parameters.get(marker) or stage in stages:
                        continue
                    if any(value.startswith('local_check_queue_error:') for value in stages):
                        problems.append(label + '核验暂未排队，请查看任务记录')
                    else:
                        waiting.append(identifier)
    if pending:
        problems.append('后续任务链超过核验上限，请到任务记录核查')
    # Inline selection jobs own the worker slot but are not followup_queued
    # collection nodes. Their exact idempotency keys prove this root, including
    # older increments that ran selection before sector collection.
    from .workspace_progress import children
    identifiers = {item['job_id'] for item in items}
    for item in list(items):
        if item['kind'] == 'increment':
            for child in children(connection, item):
                if child['job_id'] not in identifiers:
                    items.append(child)
                    identifiers.add(child['job_id'])
    return items, waiting, list(dict.fromkeys(problems))


def _state(items, waiting, problems):
    # A failed child must not hide another already-authorized running child.
    if any(item['status'] in ('queued', 'running') for item in items):
        return 'running', False
    for status in ('failed', 'paused', 'cancelled'):
        if any(item['status'] == status for item in items):
            return status, True
    if problems:
        return 'blocked', True
    if waiting:
        return 'awaiting_followup', False
    if any(item['status'] == 'partial' for item in items):
        return 'partial', True
    return ('no_change' if items and all(item['status'] == 'no_change' for item in items) else 'succeeded'), True


def lock_record(connection):
    """One small durable pointer; no market/file scan or cross-request cache."""
    saved = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (LOCK_SETTING,)).fetchone()
    return json.loads(saved[0]) if saved else None


def root_for_task(connection, identifier):
    row = connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone()
    if row is None:
        return None
    if row['kind'] == 'increment':
        return row
    parent = json.loads(row['parameters_json']).get('parent_sync_job_id')
    if parent:
        root = connection.execute("SELECT * FROM job WHERE job_id=? AND kind='increment'", (parent,)).fetchone()
        if root is not None:
            return root
    saved = lock_record(connection)
    if saved:
        root = connection.execute('SELECT * FROM job WHERE job_id=?', (saved['root_task_id'],)).fetchone()
        if root and any(item['job_id'] == identifier for item in _chain(connection, root)[0]):
            return root
    # Local checks predate parent_sync_job_id in their frozen parameters. Their
    # original durable next_task_id link still proves the same root on resume.
    seen = {identifier}
    for _ in range(32):
        parent = connection.execute("SELECT job_id FROM job_event WHERE stage IN ('followup_queued','line_alert_check_queued','listing_status_check_queued','increment_retry_queued') AND json_extract(details_json,'$.next_task_id')=? ORDER BY occurred_at_ms DESC,seq DESC,job_id LIMIT 1", (identifier,)).fetchone()
        if not parent or parent[0] in seen:
            break
        identifier = parent[0]; seen.add(identifier)
        row = connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone()
        if row and row['kind'] == 'increment':
            return row
    return None


def bind_session(connection, root, *, attempt_task_id=None):
    """Called in the same write transaction as create/resume, never on a GET."""
    from app.services.task_service import append_event
    saved = lock_record(connection)
    if saved and saved['root_task_id'] != root['job_id']:
        raise HTTPException(409, {'message': '已有盘后增量尚待确认，请先查看进度和结果', 'increment_session': saved})
    if saved:
        return saved
    value = {'root_task_id': root['job_id'], 'request_key': root['request_key'],
             'created_at_ms': now_ms(), 'root_created_at_ms': root['created_at_ms'],
             'epoch': new_id('increment_session'),
             **({'attempt_task_id': attempt_task_id} if attempt_task_id and attempt_task_id != root['job_id'] else {})}
    connection.execute('INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms',
                       (LOCK_SETTING, dumps(value), now_ms()))
    append_event(connection, root['job_id'], 'increment_session_opened', '本次盘后增量锁定工作台，完整结果确认后解锁',
                 details={'epoch': value['epoch'], 'attempt_task_id': value.get('attempt_task_id')})
    return value


def require_unlocked(connection):
    saved = lock_record(connection)
    if saved:
        raise HTTPException(409, {'message': '盘后增量期间工作台已锁定，请先查看进度并确认完整结果', 'increment_session': saved})


def chain_stop_action(connection, job, *, parent_task_id=None):
    saved = lock_record(connection)
    params = json.loads(job['parameters_json'])
    root_id = job['job_id'] if job['kind'] == 'increment' else params.get('parent_sync_job_id')
    if not root_id and saved:
        root = root_for_task(connection, job['job_id'])
        root_id = root['job_id'] if root is not None else None
    if not root_id:
        return None
    # A child retry may clear its own stop, never a stop on the original root
    # or siblings. Durable ancestry also covers legacy local-check parameters.
    ancestors = {job['job_id'], root_id}
    identifier = parent_task_id or job['job_id']
    ancestors.add(identifier)
    for _ in range(32):
        parent = connection.execute("SELECT job_id FROM job_event WHERE stage IN ('followup_queued','line_alert_check_queued','listing_status_check_queued','increment_retry_queued') AND json_extract(details_json,'$.next_task_id')=? ORDER BY occurred_at_ms DESC,seq DESC,job_id LIMIT 1", (identifier,)).fetchone()
        if not parent or parent[0] in ancestors:
            break
        identifier = parent[0]; ancestors.add(identifier)
    placeholders = ','.join('?' for _ in ancestors)
    marker = connection.execute("SELECT stage,details_json FROM job_event WHERE job_id=? AND stage IN ('increment_session_opened','increment_chain_stop_requested') AND CASE WHEN stage='increment_session_opened' THEN coalesce(json_extract(details_json,'$.attempt_task_id'),?) ELSE coalesce(json_extract(details_json,'$.scope_task_id'),?) END IN (" + placeholders + ") ORDER BY seq DESC LIMIT 1", (root_id, root_id, root_id, *ancestors)).fetchone()
    if marker and marker['stage'] == 'increment_chain_stop_requested':
        action = json.loads(marker['details_json']).get('action')
        return action if action in ('pause', 'cancel') else None
    return None


def mark_stop(connection, identifier, was_running):
    """A database status alone cannot prove an executing worker has stopped."""
    saved = lock_record(connection)
    root = root_for_task(connection, identifier) if was_running and saved else None
    if root is not None and root['job_id'] == saved['root_task_id']:
        from app.services.task_service import append_event
        pending = connection.execute("SELECT max(seq) FROM job_event WHERE job_id=? AND stage='increment_stop_requested'", (identifier,)).fetchone()[0]
        if pending and not connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='increment_worker_released' AND seq>?", (identifier, pending)).fetchone():
            return
        append_event(connection, identifier, 'increment_stop_requested', '等待工作进程结束当前步骤并安全释放')


def worker_released(identifier=None, *, recovered=False):
    """Recovery is called only after acquiring the existing exclusive worker lock."""
    from app.services.task_service import append_event
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        saved = lock_record(connection)
        if not saved:
            return
        root = connection.execute('SELECT * FROM job WHERE job_id=?', (saved['root_task_id'],)).fetchone()
        if root is None:
            return
        if recovered:
            from .sync_workspace_followup import active, RELEASED
            if active(connection, root['job_id']):
                append_event(connection, root['job_id'], RELEASED,
                             '唯一工作进程已重新取得执行锁，旧同步后计算已退出',
                             details={'recovered_under_exclusive_worker_lock': True})
        items = _chain(connection, root)[0]
        for item in items:
            if identifier is not None and item['job_id'] != identifier:
                continue
            if recovered and item['status'] in ('queued', 'running'):
                continue
            append_event(connection, item['job_id'], 'increment_worker_released',
                         '唯一工作进程已重新取得执行锁，旧执行已退出' if recovered else '工作进程已退出本步骤，写入与任务派发已释放',
                         details={'recovered_under_exclusive_worker_lock': recovered})


def _chain_state(connection, root):
    items, waiting, problems = _chain(connection, root)
    saved = lock_record(connection)
    current = saved if saved and saved['root_task_id'] == root['job_id'] else None
    previous = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='increment_session_acknowledged' ORDER BY seq DESC LIMIT 1", (root['job_id'],)).fetchone() if current is None else None
    attempt_id = (current or (json.loads(previous[0]) if previous else {})).get('attempt_task_id')
    effective = items; attempt = None
    if attempt_id and attempt_id != root['job_id']:
        attempt = connection.execute('SELECT * FROM job WHERE job_id=?', (attempt_id,)).fetchone()
        if attempt is None:
            problems = ['本次重试任务记录缺失，请核查服务端任务存档']
        else:
            effective, waiting, current_problems = _chain(connection, attempt)
            problems = [problem for problem in problems if '记录缺失' in problem or '超过核验上限' in problem] + current_problems
            effective_ids = {item['job_id'] for item in effective}
            for item in items:
                item['historical_attempt'] = item['job_id'] not in effective_ids
    stop = chain_stop_action(connection, attempt if attempt_id and attempt_id != root['job_id'] and attempt is not None else root)
    if stop:
        waiting = []  # Explicit stop revokes the remaining, not-yet-queued stages.
    status, done = _state(effective, waiting, problems)
    draining = []
    for item in effective:
        stopped = connection.execute("SELECT max(seq) FROM job_event WHERE job_id=? AND stage='increment_stop_requested'", (item['job_id'],)).fetchone()[0]
        if stopped and not connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='increment_worker_released' AND seq>? LIMIT 1", (item['job_id'], stopped)).fetchone():
            draining.append(item['job_id'])
    from .sync_workspace_followup import active
    workspace_active=active(connection, root['job_id'])
    if stop:
        if workspace_active and root['job_id'] not in draining:
            draining.append(root['job_id'])
    if draining:
        status, done = 'stopping', False
    elif workspace_active:
        status, done = 'running', False
    elif any('记录缺失' in problem or '超过核验上限' in problem for problem in problems):
        status, done = 'unknown', False
    elif stop and not any(item['status'] in ('queued', 'running') for item in effective):
        if status not in ('failed', 'blocked'):
            status = 'paused' if stop == 'pause' else 'cancelled'
        done = True
    return items, waiting, problems, status, done, draining


def _history(connection, root):
    parameters = json.loads(root['parameters_json'])
    version = parameters.get('data_chain_version')
    key = (str(data_dir()), version, root['job_id'])
    cached = _history_cache.get(key)
    if cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    rows = connection.execute("""SELECT * FROM job WHERE kind='increment' AND job_id<>? AND created_at_ms<=?
        AND status IN ('succeeded','partial','no_change')
        AND json_extract(parameters_json,'$.data_chain_version') IS ?
        ORDER BY created_at_ms DESC LIMIT 12""", (root['job_id'], root['created_at_ms'], version)).fetchall()
    durations = []
    for row in rows:
        items, waiting, problems = _chain(connection, row)
        state, done = _state(items, waiting, problems)
        if done and state in SUCCESS and all(item.get('finished_at_ms') for item in items):
            duration = max(item['finished_at_ms'] for item in items) - row['created_at_ms']
            if duration > 0:
                durations.append(duration)
    result = sorted(durations)
    _history_cache[key] = (time.monotonic(), result)
    while len(_history_cache) > 32:
        _history_cache.pop(next(iter(_history_cache)))
    return result


def session(*, task_id=None, request_key=None):
    if not task_id and not request_key:
        with connect() as connection:
            saved = lock_record(connection)
        active = session(task_id=saved['root_task_id']) if saved else None
        return {'active': active if active and active['locked'] else None}
    with connect() as connection:
        connection.execute('BEGIN')
        root = connection.execute('SELECT * FROM job WHERE job_id=?' if task_id else
                                  'SELECT * FROM job WHERE request_key=?', (task_id or request_key,)).fetchone()
        if root is None:
            raise HTTPException(404, '本次增量任务尚未确认')
        if root['kind'] != 'increment':
            raise HTTPException(422, '此进度窗口仅用于盘后增量')
        items, waiting, problems, status, done, draining = _chain_state(connection, root)
        from .workspace_progress import summary
        workspace = summary(connection, root, items)
        saved = lock_record(connection)
        current = saved if saved and saved['root_task_id'] == root['job_id'] else None
        ack_row = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='increment_session_acknowledged' ORDER BY seq DESC LIMIT 1", (root['job_id'],)).fetchone()
        acknowledged = ack_row is not None
        scope = current or (json.loads(ack_row[0]) if ack_row else {})
        samples = [] if done or status in ('stopping', 'unknown') or current and current.get('attempt_task_id') else _history(connection, root)
        started = scope.get('created_at_ms', root['created_at_ms'])
    observed = now_ms()
    finished = max((item.get('finished_at_ms') or item.get('stopped_at_ms') or started for item in items if not item.get('historical_attempt')), default=observed)
    elapsed = max(0, (finished if done else observed) - started)
    estimate = {'sample_count': len(samples), 'remaining_min_ms': None, 'remaining_max_ms': None,
                'expected_finish_at_ms': None, 'basis': '最近最多12次同数据链的已完成增量，包含排队和全部后续任务',
                'reason': '历史完整增量样本不足3次，暂无法估计剩余时间'}
    if len(samples) >= 3:
        lower, upper = samples[len(samples)//4], samples[min(len(samples)-1, len(samples)*3//4)]
        if elapsed < upper:
            estimate.update(remaining_min_ms=max(0, lower-elapsed), remaining_max_ms=upper-elapsed,
                            expected_finish_at_ms=started+int(statistics.median(samples)),
                            reason=None)
        else:
            estimate['reason'] = '本次耗时已超过历史参考区间，剩余时间暂无法估计'
    return {'root_task_id': root['job_id'], 'request_key': root['request_key'], 'status': status,
            'locked': current is not None, 'session_epoch': current.get('epoch') if current else None,
            'attempt_task_id': scope.get('attempt_task_id'),
            'acknowledged': acknowledged and current is None, 'draining_task_ids': draining,
            'can_acknowledge': done, 'created_at_ms': started, 'root_created_at_ms': root['created_at_ms'], 'observed_at_ms': observed,
            'finished_at_ms': finished if done else None, 'elapsed_ms': elapsed,
            'tasks': items, 'workspace': workspace, 'waiting_followup': bool(waiting), 'issues': problems, 'estimate': estimate,
            'message': '本次任务链记录尚未核验完整，请核查服务端任务存档' if status == 'unknown' else
                       '正在等待工作进程安全停止，已发布数据保留' if draining else
                       '本次数据链已结束，请确认结果后继续操作' if done else
                       workspace['message'] if workspace['state'] == 'running' else
                       '等待已授权后续任务排队' if waiting and not any(item['status']=='running' for item in items) else
                       '本次增量数据链正在运行'}


def acknowledge(*, request_key, task_id=None, epoch=None):
    """Recheck the same root and complete chain atomically before unlocking."""
    from app.services.task_service import append_event
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
        raise HTTPException(422, '增量请求标识无效')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        saved = lock_record(connection)
        root = connection.execute("SELECT * FROM job WHERE request_key=? AND kind='increment'", (request_key,)).fetchone()
        if saved and (saved['request_key'] != request_key or task_id and saved['root_task_id'] != task_id or epoch and saved['epoch'] != epoch):
            raise HTTPException(409, {'message': '增量会话已变化，请读取当前任务后再确认', 'increment_session': saved})
        if root is None:
            if saved or task_id:
                raise HTTPException(409, '本次任务记录缺失，仍保持锁定，请先核查服务端任务存档')
            # A late original POST must check this tombstone inside its create
            # transaction. This proves abandonment without guessing from time/404.
            connection.execute('INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?) '
                               'ON CONFLICT(key) DO NOTHING',
                               ('increment-abandoned:' + request_key, dumps({'request_key': request_key}), now_ms()))
            return {'acknowledged': True, 'status': 'not_received', 'request_key': request_key}
        if task_id and root['job_id'] != task_id:
            raise HTTPException(409, '任务标识与请求不一致')
        items, waiting, problems, status, done, draining = _chain_state(connection, root)
        if not done:
            raise HTTPException(409, '整条增量数据链尚未安全结束，暂不能确认解锁')
        if saved:
            scope_id = saved.get('attempt_task_id') or root['job_id']
            scope_row = connection.execute('SELECT * FROM job WHERE job_id=?', (scope_id,)).fetchone()
            if status in ('failed', 'paused', 'cancelled', 'blocked') and not chain_stop_action(connection, scope_row):
                append_event(connection, root['job_id'], 'increment_chain_stop_requested', '用户已确认未完整结果，未执行的后续阶段须手动处理',
                             details={'action': 'pause' if status == 'paused' else 'cancel', 'reason': 'acknowledged_incomplete_result',
                                      'scope_task_id': saved.get('attempt_task_id') or root['job_id']})
            append_event(connection, root['job_id'], 'increment_session_acknowledged', '用户已确认本次完整结果，工作台解锁',
                         details={'epoch': saved['epoch'], 'status': status, 'task_ids': [item['job_id'] for item in items],
                                  'attempt_task_id': saved.get('attempt_task_id'), 'created_at_ms': saved['created_at_ms']})
            connection.execute('DELETE FROM platform_setting WHERE key=?', (LOCK_SETTING,))
        return {'acknowledged': True, 'status': status, 'root_task_id': root['job_id'], 'request_key': request_key}


def control(*, task_id, action, epoch=None):
    """Stop the existing chain; never resume, retry or create new work."""
    from app.services.task_service import append_event, change_task
    if action not in ('pause', 'cancel'):
        raise HTTPException(422, '进度窗口只支持安全暂停或取消本次增量')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        saved = lock_record(connection)
        if not saved or saved['root_task_id'] != task_id or epoch and saved['epoch'] != epoch:
            raise HTTPException(409, '增量会话已变化，请先重新读取')
        root = connection.execute('SELECT * FROM job WHERE job_id=?', (task_id,)).fetchone()
        items, waiting, problems, status, done, draining = _chain_state(connection, root)
        if done:
            return {'stopping': False, 'status': status}
        value = {**saved, 'stop_action': action}
        connection.execute('UPDATE platform_setting SET value_json=?,updated_at_ms=? WHERE key=?', (dumps(value), now_ms(), LOCK_SETTING))
        for item in items:
            if item['status'] == 'running':
                mark_stop(connection, item['job_id'], True)
        # The root can have finished SQL while dispatch still runs local checks
        # or queues its child. Its real finally receipt is the release proof.
        if root['status'] in SUCCESS and connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='chain_authorized'", (task_id,)).fetchone() and not connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='increment_worker_released'", (task_id,)).fetchone():
            mark_stop(connection, task_id, True)
        append_event(connection, task_id, 'increment_chain_stop_requested', '用户请求安全暂停本次数据链' if action == 'pause' else '用户请求安全取消本次数据链',
                     details={'action': action, 'scope_task_id': saved.get('attempt_task_id') or task_id})
        identifiers = [item['job_id'] for item in items if item['status'] in ('queued', 'running')]
    for identifier in identifiers:
        change_task(identifier, action)
    return {'stopping': True, 'root_task_id': task_id}
