"""Interactive collection controls; internal recovery retains original semantics."""
import json
import statistics

from fastapi import HTTPException


def protected(connection, row):
    parameters = json.loads(row['parameters_json'])
    if row['kind'] == 'increment' or parameters.get('operation') in ('auction_snapshot', 'noon_snapshot'):
        return True
    parent = parameters.get('parent_sync_job_id')
    if not parent and row['kind'] in ('selection', 'tracking_review'):
        key = row['request_key']
        parent = key.split(':selection:', 1)[0] if ':selection:' in key else key.removesuffix(':tracking-review') if key.endswith(':tracking-review') else None
    if parent and connection.execute("SELECT 1 FROM job WHERE job_id=? AND kind='increment'", (parent,)).fetchone():
        return True
    if row['status'] in ('queued', 'running'):
        identifier = row['job_id']
        seen = {identifier}
        for _ in range(32):
            previous = connection.execute("""SELECT j.job_id,j.kind FROM job_event e JOIN job j ON j.job_id=e.job_id
                WHERE e.stage IN ('followup_queued','line_alert_check_queued','listing_status_check_queued','increment_retry_queued')
                AND json_extract(e.details_json,'$.next_task_id')=? ORDER BY e.occurred_at_ms DESC,e.seq DESC LIMIT 1""", (identifier,)).fetchone()
            if not previous or previous['job_id'] in seen:
                break
            if previous['kind'] == 'increment':
                return True
            identifier = previous['job_id']
            seen.add(identifier)
    return False


def require_interactive_action(connection, row, action):
    if action in ('pause', 'cancel') and protected(connection, row):
        raise HTTPException(409, '盘后增量、盘前竞价和午评及其后续步骤不提供暂停或取消，请等待结果；可刷新查看阶段和预计剩余时间')


def estimate(connection, row, observed):
    operation = json.loads(row['parameters_json']).get('operation')
    history = connection.execute("""SELECT finished_at_ms-created_at_ms AS elapsed FROM job
        WHERE kind=? AND job_id<>? AND created_at_ms<=? AND status IN ('succeeded','partial','no_change')
        AND json_extract(parameters_json,'$.operation')=? AND finished_at_ms>created_at_ms
        ORDER BY created_at_ms DESC LIMIT 12""", (row['kind'], row['job_id'], row['created_at_ms'], operation)).fetchall()
    samples = sorted(item[0] for item in history)
    result = {'sample_count': len(samples), 'remaining_min_ms': None, 'remaining_max_ms': None,
              'expected_finish_at_ms': None, 'reason': '同类已完成任务不足3次，暂无法估计剩余时间'}
    if len(samples) >= 3:
        elapsed = max(0, observed-row['created_at_ms'])
        lower, upper = samples[len(samples)//4], samples[min(len(samples)-1, len(samples)*3//4)]
        if elapsed < upper:
            result.update(remaining_min_ms=max(0, lower-elapsed), remaining_max_ms=upper-elapsed,
                          expected_finish_at_ms=row['created_at_ms']+int(statistics.median(samples)), reason=None)
        else:
            result['reason'] = '本次耗时已超过历史参考范围，剩余时间暂无法估计'
    return result
