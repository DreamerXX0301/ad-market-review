"""Durable, explicit all-catalog chart jobs and a small offline status model."""
import json
from fastapi import HTTPException

from app.core import connect, get_market
from app.services import task_service as tasks
from app.services.sector_chart_batch import plan_sector_chart_batch, collect_sector_chart_batch

CHAIN_VERSION = 'sector-charts-v1'


def initialization_state(market=None, exclude_job_id=None):
    """A successful initial receipt survives later market dates and catalogs.

    Completion means every frozen board was acquired, not that a supplier has
    supplied every historical day. Initial and incremental receipts are kept in
    separate namespaces, so a same-day no-op cannot erase this evidence.
    """
    from app.services.sector_chart_batch import read_frozen_sector_chart_batch_report
    market = market or get_market()
    with connect() as connection:
        rows = connection.execute("""SELECT * FROM job j WHERE kind='source_check'
            AND status IN ('succeeded','partial')
            AND json_extract(parameters_json,'$.operation')='sector_charts'
            AND COALESCE(json_extract(parameters_json,'$.mode'),'initial')='initial'
            AND job_id!=?
            AND EXISTS (SELECT 1 FROM job_event e WHERE e.job_id=j.job_id
                AND json_extract(e.details_json,'$.coverage.collection_complete')=1)
            ORDER BY created_at_ms DESC LIMIT 200""", (exclude_job_id or '',)).fetchall()
        active = connection.execute("""SELECT 1 FROM job WHERE kind='source_check'
            AND status IN ('queued','running','paused')
            AND json_extract(parameters_json,'$.operation')='sector_charts'
            AND COALESCE(json_extract(parameters_json,'$.mode'),'initial')='initial'
            AND job_id!=? LIMIT 1""", (exclude_job_id or '',)).fetchone()
    for row in rows:
        try:
            params = json.loads(row['parameters_json'])
            report = read_frozen_sector_chart_batch_report(params, mode='initial')
            count = len(params.get('boards', [])); coverage = (report or {}).get('coverage', {})
            if (count > 0 and coverage.get('collection_complete') is True
                    and coverage.get('available_count') == count
                    and coverage.get('total_count') == count
                    and coverage.get('failed_count') == 0 and coverage.get('unrequested_count') == 0):
                return {'initialized': True, 'can_initialize': False,
                        'reason': '板块K线已初始化，后续由每日盘后增量追加；来源历史缺口不重复初始化',
                        'initialized_date': row['target_date'], 'available_count': count,
                        'catalog_count': coverage.get('catalog_count', count), 'job_id': row['job_id']}
        except (ValueError, OSError, TypeError, AttributeError):
            continue
    ready = bool(market.status().get('ready'))
    return {'initialized': False, 'can_initialize': ready and not active,
            'reason': '首次板块K线任务尚在进行，请查看原任务' if active else
                      '请先完成主行情初始化' if not ready else None,
            'initialized_date': None, 'available_count': None, 'catalog_count': None, 'job_id': None}


def require_initialization_allowed(market=None, exclude_job_id=None):
    state = initialization_state(market, exclude_job_id)
    if state['initialized']:
        raise HTTPException(409, state['reason'])
    return state


def create_batch_job(market, day, request_key=None, chain_parameters=None, mode='initial'):
    if mode not in ('initial', 'increment'):
        raise ValueError('板块K线只支持首次补齐或盘后尾部增量')
    if mode == 'initial': require_initialization_allowed(market)
    from app.services.source_service import client
    if not client('fuyao').key:
        raise ValueError('补齐行业概念历史K线需要已配置的扶摇Key；权限以实际接口验证为准')
    plan = plan_sector_chart_batch(market, day) if mode == 'initial' else plan_sector_chart_batch(market, day, mode=mode)
    parameters = {'operation': 'sector_charts', 'mode': mode, 'source_id': 'fuyao', **plan}
    if chain_parameters:
        parameters.update({key: chain_parameters[key] for key in
                           ('initialization_chain', 'increment_chain', 'parent_sync_job_id',
                            'previous_chain_job_id', 'data_chain_version') if key in chain_parameters})
    return tasks.create_task('source_check', parameters, target_date=day, request_key=request_key)


def execute_batch_job(job_id, progress=None):
    with connect() as connection:
        row = connection.execute('SELECT * FROM job WHERE job_id=?', (job_id,)).fetchone()
    if not row:
        raise ValueError('全量板块K线任务不存在')
    params = json.loads(row['parameters_json'])
    if row['kind'] != 'source_check' or params.get('operation') != 'sector_charts':
        raise ValueError('任务不是全量板块K线同步')
    market = get_market()
    if params.get('mode', 'initial') == 'initial':
        initial = initialization_state(market, exclude_job_id=job_id)
        if initial['initialized']: raise ValueError(initial['reason'])

    def guard():
        with connect() as connection:
            state = connection.execute('SELECT status FROM job WHERE job_id=?', (job_id,)).fetchone()
        if not state or state[0] == 'cancelled':
            raise tasks.TaskCancelled()
        if state[0] == 'paused':
            raise tasks.TaskPaused()
        if state[0] != 'running':
            raise ValueError('全量板块K线任务未运行，未继续发布')
        if (market.status().get('snapshot_id') != params.get('snapshot_id')
                or get_market().status().get('snapshot_id') != params.get('snapshot_id')):
            raise ValueError('主行情代际已变化；已验收板块缓存保留，请重新创建补齐任务')

    guard()
    from app.services.source_service import client
    def collection_progress(stage, percentage, message, details=None):
        if progress:
            progress(stage, round(max(0, min(100, percentage))*85/100), message,
                     {**(details or {}), 'phase': 'sector_chart_collection'})

    result = collect_sector_chart_batch(market, params, client('fuyao'), guard=guard,
                                       progress=collection_progress if progress else None)
    guard()
    coverage = result['coverage']
    boards = params.get('boards') or []
    prewarm = {'state': 'skipped', 'reason': '来源板块尚未收齐，暂不预热派生指标'}
    if coverage.get('collection_complete') is True and boards:
        from app.services.sector_strategies import prewarm_sector_metrics
        guard_failure = None

        def warm_guard():
            nonlocal guard_failure
            try:
                guard()
            except Exception as error:
                guard_failure = error
                raise

        def warm_progress(done, total, code):
            if progress:
                progress('预热板块日周月指标', min(99, 85+round(done/max(total, 1)*14)), code,
                    {'phase': 'metrics_prewarm', 'completed_count': done, 'total_count': total,
                     'metrics_completed_count': done, 'metrics_total_count': total})

        if progress:
            progress('预热板块日周月指标', 85, '行情已收齐，正在准备可复验的本地指标摘要',
                     {'phase': 'metrics_prewarm', 'metrics_completed_count': 0,
                      'metrics_total_count': len(boards)})
        try:
            warmed = prewarm_sector_metrics(market, [board['topic_code'] for board in boards],
                day=params['as_of'], guard=warm_guard, progress=warm_progress if progress else None)
            prewarm = {key: warmed.get(key) for key in ('date', 'snapshot_id', 'total_count',
                'available_count', 'failed_count', 'complete', 'contract',
                'persistent_expected_count', 'persistent_available_count', 'persistent_failed_count')}
            prewarm.update(state='completed' if warmed.get('complete') is True else 'partial',
                           failures=(warmed.get('failures') or [])[:20],
                           persistent_failures=(warmed.get('persistent_failures') or [])[:20])
            if (warmed.get('persistent_failed_count') or 0) > 0:
                prewarm.update(state='warning',
                    reason='部分指标摘要未持久留存，已采集行情保留；使用时可重新计算')
        except Exception as error:
            # The original guard error must never become a disposable-cache
            # warning. Source artifacts remain valid, but user control wins.
            if error is guard_failure or isinstance(error, (tasks.TaskPaused, tasks.TaskCancelled)):
                raise
            guard()
            prewarm = {'state': 'warning', 'complete': False,
                'reason': '派生指标预热未完成，已采集行情保留，使用时可重新计算：'+str(error),
                'error_type': type(error).__name__}
        guard()
        if progress and prewarm['state'] in ('partial', 'warning'):
            progress('板块指标预热提示', 99,
                prewarm.get('reason') or '部分板块指标输入待核验，未知未填零；已采集行情不受影响',
                {'phase': 'metrics_prewarm', 'metrics_prewarm': prewarm})
    guard()
    # This receipt belongs to the job result only. Do not rewrite the accepted
    # source-batch manifest or its source coverage with derived-cache status.
    result = {**result, 'metrics_prewarm': prewarm}
    if progress:
        progress('板块历史K线核验完成', 100, result['message'],
                 {**coverage, 'completed_count': coverage['total_count'],
                  'actual_completed_batches': coverage['total_count'],
                  'actual_total_batches': coverage['total_count'], 'coverage': coverage,
                  'metrics_prewarm': prewarm})
    return result


def batch_status(market):
    """Read only a small SHA-verified manifest, never all board histories."""
    from app.services.sector_history import _catalog
    from app.services.sector_chart_batch import read_sector_chart_batch_report
    status = market.status()
    day = status.get('data_end')
    catalog, _, _, _ = _catalog(market.reference()) if status.get('ready') else ({}, [], 0, 0)
    fields = ('available_count', 'complete_count', 'partial_count', 'failed_count',
              'unrequested_count', 'request_count', 'cache_hit_count',
              'collection_complete', 'complete')
    result = {'target_date': day, 'catalog_count': len(catalog),
              **{field: None for field in fields}, 'verified_at': None, 'last_job': None,
              'initialization': initialization_state(market)}
    if not day or not catalog:
        return result
    try:
        report = read_sector_chart_batch_report(market, day, mode='increment') or read_sector_chart_batch_report(market, day)
    except (ValueError, OSError, TypeError):
        report = None
        result['reason'] = '全量板块K线摘要未通过校验，可手动重新核验；单板块缓存分别校验'
    if report:
        result.update({field: report['coverage'].get(field) for field in fields})
        result['verified_at'] = report.get('fetched_at')
    with connect() as connection:
        row = connection.execute("""SELECT job_id FROM job WHERE kind='source_check'
            AND json_extract(parameters_json,'$.operation')='sector_charts'
            AND json_extract(parameters_json,'$.as_of')=?
            AND json_extract(parameters_json,'$.snapshot_id')=?
            ORDER BY created_at_ms DESC,rowid DESC LIMIT 1""",
            (day, status.get('snapshot_id'))).fetchone()
    result['last_job'] = tasks.task_view(row[0]) if row else None
    return result
