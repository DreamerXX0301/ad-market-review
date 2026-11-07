"""Small SQLite completion receipts; successful work is not submitted twice.

Main data is gated by the Beijing post-close day, supplements by the published
market generation and their requested scope. Partial supplements remain repairable.
No quote files or credentials are opened inside the task creation transaction.
"""
import json
from fastapi import HTTPException
from app.core import connect, dumps, now_ms
from .sync_clock import safe_completion_date


def _increment(connection, day):
    row = connection.execute("""SELECT j.job_id,j.status,j.finished_at_ms FROM job j
        WHERE j.kind='increment' AND j.status IN ('succeeded','partial','no_change')
        AND (json_extract(j.parameters_json,'$.sync_completion_date')=? OR
          (json_extract(j.parameters_json,'$.sync_completion_date') IS NULL AND
           (j.target_date=? OR EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id
            AND e.stage='chain_target_frozen' AND json_extract(e.details_json,'$.target_date')=?))))
        ORDER BY j.finished_at_ms DESC LIMIT 1""", (day, day, day)).fetchone()
    return dict(row) if row else None


def _key(snapshot, day):
    return 'sync-supplement-completion:' + str(snapshot) + ':' + str(day)


def replay_supplement_request(request_key, scope):
    """Replay the caller's frozen scope before comparing with today's head."""
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
        return None
    with connect() as connection:
        row = connection.execute('SELECT job_id,kind,parameters_json FROM job WHERE request_key=?', (request_key,)).fetchone()
    if not row:
        return None
    previous = json.loads(row['parameters_json'])
    valid = row['kind'] == 'source_check' and previous.get('operation') == 'market_supplement'
    valid = valid and previous.get('revalidate_only', False) == scope.get('revalidate_only', False)
    if not scope.get('revalidate_only'):
        valid = valid and all(previous.get(key, default) == scope.get(key, default) for key, default in
                              (('year_history', True), ('sector_history', True), ('recent_days', 1), ('coverage_plan_id', None)))
    if not valid:
        raise HTTPException(409, '此请求标识已用于不同任务参数，请使用新的请求标识')
    from .task_service import task_view
    return task_view(row['job_id'])


def remember_supplement(job_id, result, parameters):
    """The worker saves a receipt only after publication; terminal status is rechecked."""
    if result.get('revalidate_only') or parameters.get('revalidate_only'):
        return
    snapshot, day = result.get('snapshot_id'), result.get('target_date')
    if not snapshot or not day:
        return
    receipt = {key: parameters.get(key, default) for key, default in
               (('year_history', True), ('sector_history', False), ('recent_days', 20))}
    receipt.update(job_id=job_id, quality=result.get('quality'), snapshot_id=snapshot, target_date=day)
    with connect() as connection:
        connection.execute('INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms',
                           (_key(snapshot, day), dumps(receipt), now_ms()))


def _supplement(connection, snapshot, day, scope):
    row = connection.execute('SELECT value_json FROM platform_setting WHERE key=?', (_key(snapshot, day),)).fetchone()
    if not row:
        return None
    receipt = json.loads(row[0])
    job = connection.execute('SELECT status,finished_at_ms FROM job WHERE job_id=?', (receipt.get('job_id'),)).fetchone()
    if not job or job['status'] not in ('succeeded', 'no_change') or receipt.get('quality') != 'complete':
        return None
    if any(scope.get(key, default) and not receipt.get(key) for key, default in (('year_history', True), ('sector_history', True))):
        return None
    if scope.get('recent_days', 1) > receipt.get('recent_days', 0):
        return None
    return {**receipt, 'finished_at_ms': job['finished_at_ms']}


def _published_supplement(snapshot, day, scope):
    """Recognize a successful pre-upgrade publication without writing on GET."""
    from .market_supplement import load_supplement_status
    doc = load_supplement_status(snapshot)
    if (snapshot not in (doc.get('snapshot_id'), doc.get('compatible_snapshot_id')) or doc.get('target_date') != day
            or doc.get('quality') != 'complete' or doc.get('revalidate_only') or not doc.get('job_id')):
        return None
    with connect() as connection:
        row = connection.execute('SELECT status,parameters_json,finished_at_ms FROM job WHERE job_id=?', (doc['job_id'],)).fetchone()
    if not row or row['status'] not in ('succeeded', 'no_change'):
        return None
    params = json.loads(row['parameters_json'])
    if any(scope.get(key, default) and not params.get(key, fallback) for key, default, fallback in
           (('year_history', True, True), ('sector_history', True, False))):
        return None
    if scope.get('recent_days', 1) > params.get('recent_days', 20):
        return None
    return {'job_id': doc['job_id'], 'finished_at_ms': row['finished_at_ms']}


def require_published_supplement_available(snapshot, day, scope):
    # This file read stays outside create_task's SQLite transaction.
    completed = _published_supplement(snapshot, day, scope)
    if completed:
        raise HTTPException(409, {'message': '当前行情日与代际的所选盘面补充已完整成功，请勿重复提交',
                                  'completed_task_id': completed['job_id']})


def read_view(snapshot, day):
    with connect() as connection:
        increment = _increment(connection, safe_completion_date())
        supplement = _supplement(connection, snapshot, day, {})
    supplement = supplement or _published_supplement(snapshot, day, {})
    return {
        'increment': {'completed': bool(increment), 'task': increment,
                      'reason': '本日主行情已更新；已有缺口请使用盘面修补或重试失败步骤' if increment else None},
        'supplement': {'completed': bool(supplement), 'task': supplement,
                       'reason': '当前行情日与代际的盘面补充已完整成功，无需重复提交' if supplement else None},
    }


def require_available(connection, kind, parameters):
    if kind == 'increment':
        completed = _increment(connection, safe_completion_date())
        reason = '本日主行情已更新，请勿重复提交；缺口请单独修补'
    elif (kind == 'source_check' and parameters.get('operation') == 'market_supplement'
          and not parameters.get('revalidate_only') and not parameters.get('coverage_plan_id')):
        completed = _supplement(connection, parameters.get('market_snapshot_id'),
                                parameters.get('completion_target_date'), parameters)
        reason = '当前行情日与代际的所选盘面补充已完整成功，请勿重复提交'
    else:
        return
    if completed:
        raise HTTPException(409, {'message': reason, 'completed_task_id': completed.get('job_id')})
