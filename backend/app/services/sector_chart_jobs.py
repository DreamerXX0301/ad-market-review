"""Explicit single-board chart jobs; readers never contact a provider."""
from app.core import connect, get_market
from app.services import task_service as tasks
from app.services.sector_charts import plan_sector_chart, read_sector_chart, collect_sector_chart


def _parameters(plan):
    return {'operation': 'sector_chart', 'source_id': 'fuyao', **plan}


def chart_view(market, topic_code, day, period='D'):
    plan = plan_sector_chart(market, topic_code, day)
    chart = read_sector_chart(market, plan['topic_code'], day, period)
    with connect() as connection:
        row = connection.execute("""SELECT job_id FROM job WHERE kind='source_check'
            AND json_extract(parameters_json,'$.operation')='sector_chart'
            AND json_extract(parameters_json,'$.topic_code')=?
            AND json_extract(parameters_json,'$.as_of')=?
            AND json_extract(parameters_json,'$.snapshot_id')=?
            AND json_extract(parameters_json,'$.source')=?
            AND json_extract(parameters_json,'$.request_signature')=?
            ORDER BY created_at_ms DESC,rowid DESC LIMIT 1""",
            (plan['topic_code'], day, plan['snapshot_id'], plan['source'], plan['request_signature'])).fetchone()
    return {**chart, 'topic_code': plan['topic_code'],
            'task': tasks.task_view(row[0]) if row else None}


def create_chart_job(market, topic_code, day, request_key=None):
    plan = plan_sector_chart(market, topic_code, day)
    cached = read_sector_chart(market, plan['topic_code'], day)
    if cached['available']:
        return {'cache_hit': True, 'topic_code': plan['topic_code'], 'as_of': day}
    from app.services.source_service import client
    if not client('fuyao').key:
        raise ValueError('加载板块历史K线需要已配置的扶摇Key；权限以实际接口验证为准')
    return tasks.create_task('source_check', _parameters(plan), target_date=day, request_key=request_key)


def execute_chart_job(job_id, progress=None):
    with connect() as connection:
        row = connection.execute('SELECT * FROM job WHERE job_id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('板块K线任务不存在')
    import json
    params = json.loads(row['parameters_json'])
    if row['kind'] != 'source_check' or params.get('operation') != 'sector_chart':
        raise ValueError('任务不是指定板块K线同步')
    market = get_market()
    def guard():
        with connect() as connection:
            state = connection.execute('SELECT status FROM job WHERE job_id=?', (job_id,)).fetchone()
        if not state or state[0] == 'cancelled':
            raise tasks.TaskCancelled()
        if state[0] == 'paused':
            raise tasks.TaskPaused()
        if state[0] != 'running':
            raise ValueError('板块K线任务未运行，未继续发布')
        # The collected MarketData is immutable: also inspect the active head,
        # otherwise publication during a slow provider call evades this guard.
        if (market.status().get('snapshot_id') != params.get('snapshot_id')
                or get_market().status().get('snapshot_id') != params.get('snapshot_id')):
            raise ValueError('主行情代际已变化，请重新打开板块K线后加载')
    guard()
    from app.services.source_service import client
    return collect_sector_chart(market, params, client('fuyao'), guard=guard, progress=progress)
