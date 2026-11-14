"""Read-only projection of the exact increment's local strategy work."""
import json


def children(connection, root):
    from .task_service import _activity_view
    from .workspace_automation import SECTOR_AUTOMATION_MARKER, SECTOR_AUTOMATION_PLAN, SECTOR_AUTOMATION_VERSION
    root_id = root['job_id']
    prefix = root_id + ':selection:'
    stock_review = root_id + ':tracking-review'
    sector_selection = root_id + ':sector-selection:'
    sector_review = root_id + ':sector-tracking-review:'
    frozen = json.loads(root['parameters_json'])
    sector_plan = ({entry['strategy_id']: entry['auto_track'] for entry in frozen.get(SECTOR_AUTOMATION_PLAN, [])}
                   if frozen.get(SECTOR_AUTOMATION_MARKER) == SECTOR_AUTOMATION_VERSION else {})
    rows = connection.execute("""SELECT * FROM job WHERE
        (kind='selection' AND (request_key>=? AND request_key<? OR request_key>=? AND request_key<?))
        OR (kind='tracking_review' AND (request_key=? OR request_key>=? AND request_key<?))
        ORDER BY created_at_ms,job_id""",
        (prefix, prefix + '\uffff', sector_selection, sector_selection + '\uffff',
         stock_review, sector_review, sector_review + '\uffff')).fetchall()
    linked = []
    for row in rows:
        if row['created_at_ms'] < root['created_at_ms']:
            continue
        parameters = json.loads(row['parameters_json'])
        sid = str(parameters.get('strategy_id', ''))
        if parameters.get('target_type') == 'sector':
            if row['kind'] == 'selection':
                if sid not in sector_plan or row['request_key'] != sector_selection + sid:
                    continue
            elif (not sector_plan.get(sid) or row['request_key'] != sector_review + sid
                  or parameters.get('scope') != 'current_business_date'):
                continue
        elif row['request_key'] != (prefix + sid if row['kind'] == 'selection' else stock_review):
            continue
        linked.append({**_activity_view(connection, row), 'workspace_child': True, 'parent_task_id': root_id})
    return linked


def summary(connection, root, items):
    from .sync_workspace_followup import active, enabled
    tasks = [item for item in items if item.get('workspace_child') and item.get('parent_task_id') == root['job_id']]
    event = connection.execute("""SELECT details_json FROM job_event WHERE job_id=?
        AND stage='workspace_followup' ORDER BY seq DESC LIMIT 1""", (root['job_id'],)).fetchone()
    results = json.loads(event[0]).get('results', []) if event else []
    strategy_fields = ('strategy_id', 'target_type', 'name', 'matched', 'unknown', 'added_count',
                       'ignored_count', 'tracking_skipped_reason', 'tracking_review', 'error')
    strategies = [{key: item[key] for key in strategy_fields if key in item}
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
            'tracking': next((item['tracking_review'] for item in results if isinstance(item, dict)
                              and item.get('target_type') != 'sector' and 'tracking_review' in item), None),
            'message': blocked[0] if blocked and state == 'failed' else {'running': '正在执行本次增量的策略筛选与跟踪核对',
                        'completed': '本次策略筛选与跟踪核对已结束',
                        'failed': '本次策略或跟踪核对存在失败，请查看结果',
                        'stopped': '本次数据链已停止，尚未取得完整策略执行回执',
                        'waiting': '数据步骤完成并核验后接续策略与跟踪',
                        'unknown': '尚未取得本次策略执行回执'}[state]}
