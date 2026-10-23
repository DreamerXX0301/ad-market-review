"""Read-only, bounded receipts for one explicitly started incremental chain."""
from __future__ import annotations

import json
import statistics
import time

from fastapi import HTTPException

from app.core import connect, data_dir, now_ms
from app.services.task_service import _activity_view

SUCCESS = {'succeeded', 'partial', 'no_change'}
STOPPED = {'failed', 'cancelled', 'paused'}
LINK_STAGES = ('followup_queued', 'line_alert_check_queued', 'listing_status_check_queued')
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
                'line_alert_check_queued','listing_status_check_queued')
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
            elif 'chain_authorized' in stages and 'followup_queued' not in stages:
                waiting.append(identifier)
            parameters = json.loads(row['parameters_json'])
            if row['kind'] == 'increment':
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
        raise HTTPException(422, '需要本次增量任务或请求标识')
    with connect() as connection:
        connection.execute('BEGIN')
        root = connection.execute('SELECT * FROM job WHERE job_id=?' if task_id else
                                  'SELECT * FROM job WHERE request_key=?', (task_id or request_key,)).fetchone()
        if root is None:
            raise HTTPException(404, '本次增量任务尚未确认')
        if root['kind'] != 'increment':
            raise HTTPException(422, '此进度窗口仅用于手动盘后增量')
        items, waiting, problems = _chain(connection, root)
        status, done = _state(items, waiting, problems)
        samples = [] if done else _history(connection, root)
    observed = now_ms()
    finished = max((item.get('finished_at_ms') or item.get('stopped_at_ms') or root['created_at_ms'] for item in items), default=observed)
    elapsed = max(0, (finished if done else observed) - root['created_at_ms'])
    estimate = {'sample_count': len(samples), 'remaining_min_ms': None, 'remaining_max_ms': None,
                'expected_finish_at_ms': None, 'basis': '最近最多12次同数据链的已完成增量，包含排队和全部后续任务',
                'reason': '历史完整增量样本不足3次，暂无法估计剩余时间'}
    if len(samples) >= 3:
        lower, upper = samples[len(samples)//4], samples[min(len(samples)-1, len(samples)*3//4)]
        if elapsed < upper:
            estimate.update(remaining_min_ms=max(0, lower-elapsed), remaining_max_ms=upper-elapsed,
                            expected_finish_at_ms=root['created_at_ms']+int(statistics.median(samples)),
                            reason=None)
        else:
            estimate['reason'] = '本次耗时已超过历史参考区间，剩余时间暂无法估计'
    return {'root_task_id': root['job_id'], 'request_key': root['request_key'], 'status': status,
            'can_acknowledge': done, 'created_at_ms': root['created_at_ms'], 'observed_at_ms': observed,
            'finished_at_ms': finished if done else None, 'elapsed_ms': elapsed,
            'tasks': items, 'waiting_followup': bool(waiting), 'issues': problems, 'estimate': estimate,
            'message': '本次数据链已结束，请确认结果后继续操作' if done else
                       '等待已授权后续任务排队' if waiting and not any(item['status']=='running' for item in items) else
                       '本次增量数据链正在运行'}
