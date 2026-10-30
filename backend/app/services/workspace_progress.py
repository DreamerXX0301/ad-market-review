"""Read-only projection of the exact increment's local strategy work."""
import json


def children(connection, root):
    from .task_service import _activity_view
    prefix = root['job_id'] + ':selection:'
    rows = connection.execute("""SELECT * FROM job WHERE
        (kind='selection' AND request_key>=? AND request_key<?)
        OR (kind='tracking_review' AND request_key=?) ORDER BY created_at_ms,job_id""",
        (prefix, prefix + '\uffff', root['job_id'] + ':tracking-review')).fetchall()
    linked = []
    for row in rows:
        parameters = json.loads(row['parameters_json'])
        if row['kind'] == 'selection' and row['request_key'] != prefix + str(parameters.get('strategy_id', '')):
            continue
        if row['created_at_ms'] < root['created_at_ms']:
            continue
        linked.append({**_activity_view(connection, row), 'workspace_child': True,
                       'parent_task_id': root['job_id']})
    return linked


def summary(connection, root, items):
    from .sync_workspace_followup import active, enabled
    tasks = [item for item in items if item.get('workspace_child') and item.get('parent_task_id') == root['job_id']]
    event = connection.execute("""SELECT details_json FROM job_event WHERE job_id=?
        AND stage='workspace_followup' ORDER BY seq DESC LIMIT 1""", (root['job_id'],)).fetchone()
    results = json.loads(event[0]).get('results', []) if event else []
    strategies = [{key: item[key] for key in ('strategy_id', 'name', 'matched', 'unknown', 'added_count', 'error') if key in item}
                  for item in results if isinstance(item, dict) and 'strategy_id' in item]
    busy = active(connection, root['job_id']) or any(item['status'] in ('queued', 'running') for item in tasks)
    failed = any('error' in item or 'tracking_review_error' in item for item in results)
    completed = connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='workspace_followup_completed' LIMIT 1", (root['job_id'],)).fetchone()
    blocked = connection.execute("SELECT message FROM job_event WHERE job_id=? AND stage='workspace_followup_blocked' ORDER BY seq DESC LIMIT 1", (root['job_id'],)).fetchone()
    stopped = root['status'] in ('paused', 'cancelled', 'failed') or any(item['status'] in ('paused', 'cancelled') for item in tasks)
    state = ('running' if busy else 'failed' if failed or blocked or any(item['status'] == 'failed' for item in tasks)
             else 'completed' if event or completed else 'stopped' if stopped else 'waiting')
    if not (tasks or event or completed or blocked or enabled(json.loads(root['parameters_json']))):
        state = 'unknown'
    return {'state': state, 'strategies': strategies,
            'completed_strategies': sum('error' not in item for item in strategies) if event else sum(item['kind'] == 'selection' and item['status'] in ('succeeded', 'no_change', 'partial') for item in tasks),
            'started_strategies': sum(item['kind'] == 'selection' for item in tasks),
            'tracking': next((item['tracking_review'] for item in results if isinstance(item, dict) and 'tracking_review' in item), None),
            'message': blocked[0] if blocked and state == 'failed' else {'running': '正在执行本次增量的策略筛选与跟踪核对',
                        'completed': '本次策略筛选与跟踪核对已结束',
                        'failed': '本次策略或跟踪核对存在失败，请查看结果',
                        'stopped': '本次数据链已停止，尚未取得完整策略执行回执',
                        'waiting': '数据步骤完成并核验后接续策略与跟踪',
                        'unknown': '尚未取得本次策略执行回执'}[state]}
