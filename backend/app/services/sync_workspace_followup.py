"""Versioned post-data workspace phase; no provider access or new collection scope.

The phase lives on the increment ledger because selection/tracking calculations
already own the sole running-job slot. It is not a second nested worker job.
"""
import json

from app.core import connect
from app.services import task_service as tasks

VERSION = 'after-data-chain-v1'
MARKER = 'workspace_followup_version'
COMPLETE = 'workspace_followup_completed'
BLOCKED = 'workspace_followup_blocked'
RELEASED = 'workspace_followup_released'


def enabled(parameters):
    return parameters.get(MARKER) == VERSION


def _event(connection, identifier, stage):
    row = connection.execute('SELECT details_json FROM job_event WHERE job_id=? AND stage=? ORDER BY seq DESC LIMIT 1',
                             (identifier, stage)).fetchone()
    return json.loads(row[0]) if row else None


def active(connection, root_id):
    started = connection.execute("SELECT max(seq) FROM job_event WHERE job_id=? AND stage='workspace_followup_started'", (root_id,)).fetchone()[0]
    released = connection.execute('SELECT max(seq) FROM job_event WHERE job_id=? AND stage=?', (root_id, RELEASED)).fetchone()[0]
    return bool(started and (not released or started > released))


def dependency_state(connection, root):
    """Only original frozen links/date and small SQLite receipts may authorize it."""
    params = json.loads(root['parameters_json'])
    if root['kind'] != 'increment' or not enabled(params):
        return {'state': 'legacy'}
    from .sync_progress import chain_stop_action
    if chain_stop_action(connection, root):
        return {'state': 'stopped', 'reason': '本次数据链已显式停止，不恢复策略或提醒'}
    complete = _event(connection, root['job_id'], COMPLETE)
    blocked = _event(connection, root['job_id'], BLOCKED)
    if complete is not None:
        return {**complete, 'state': 'completed'}
    if blocked is not None:
        return {'state': 'blocked', **blocked}
    frozen = _event(connection, root['job_id'], 'chain_target_frozen')
    target = (frozen or {}).get('target_date')
    dependencies = []

    def result(state, reason=None):
        return {'state': state, 'target_date': target, 'dependencies': dependencies, 'reason': reason}

    if root['status'] in ('queued', 'running'):
        return result('waiting', '主行情尚未完成')
    if root['status'] not in ('succeeded', 'partial', 'no_change'):
        return result('blocked', '主行情未成功发布，策略和提醒未执行；已发布样本仍按实际缺口展示')
    if not target:
        return result('waiting', '原任务完成日期尚未冻结')
    expected = ['sector_charts', 'market_supplement'] if params.get('data_chain_version') == 'sector-charts-increment-v1' else ['market_supplement']
    previous = root
    for operation in expected:
        link = _event(connection, previous['job_id'], 'followup_queued')
        if link is None:
            if _event(connection, previous['job_id'], 'chain_blocked') is not None:
                return result('blocked', '必需数据步骤未能排队，策略和提醒未执行')
            return result('waiting', '正在接续原任务已授权的数据步骤')
        identifier = link.get('next_task_id')
        child = connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone() if isinstance(identifier, str) else None
        if child is None:
            return result('blocked', '必需数据步骤记录缺失，不能以旧补充代替')
        child_params = json.loads(child['parameters_json'])
        dependencies.append({'task_id': identifier, 'operation': operation, 'status': child['status']})
        if (child['kind'] != 'source_check' or child_params.get('operation') != operation
                or child_params.get('parent_sync_job_id') != root['job_id']
                or child['target_date'] != target or link.get('target_date') != target):
            return result('blocked', '后续数据步骤与原根任务、日期或范围不一致')
        if child['status'] in ('queued', 'running'):
            return result('waiting', '必需数据步骤仍在执行，尚未开始选股')
        if child['status'] not in ('succeeded', 'partial', 'no_change'):
            return result('blocked', '必需数据步骤未成功结束，策略和提醒未执行；不能复用旧盘面补充')
        previous = child
    # The identity check is a dependency of board/ST stock filters. It may run
    # early because it only consumes the newly published security directory.
    if params.get('listing_status_check_version'):
        link = _event(connection, root['job_id'], 'listing_status_check_queued')
        if link is None:
            return result('waiting', '上市身份核验尚未接续')
        identifier = link.get('next_task_id')
        if not identifier:
            return result('blocked', '上市身份核验已跳过或缺少原任务，不使用旧身份核验回执')
        if identifier:
            listing = connection.execute('SELECT status FROM job WHERE job_id=?', (identifier,)).fetchone()
            dependencies.append({'task_id': identifier, 'operation': 'listing_status_check',
                                 'status': listing[0] if listing else 'missing'})
            if listing and listing[0] in ('queued', 'running'):
                return result('waiting', '上市身份核验尚未完成')
            if not listing or listing[0] not in ('succeeded', 'partial', 'no_change'):
                return result('blocked', '上市身份核验未成功结束，未执行依赖该身份的策略')
    clock = connection.execute('SELECT business_date FROM business_clock WHERE singleton=1').fetchone()
    if not clock or clock[0] != target:
        return result('blocked', '原任务日期已不是当前业务日，不用新行情追跑旧策略')
    return result('ready')


def verify_publications(root_id, state):
    """Verify only the frozen batch receipt and target-day supplement objects.

    Historical gaps keep their original partial/unknown semantics. Successful
    acquisition of today's frozen inputs, rather than a complete five-year
    history or annual universe, is the prerequisite for local calculations.
    """
    from app.core import get_market
    target = state['target_date']
    current = get_market().status()
    snapshot = current.get('snapshot_id')
    if not snapshot or current.get('data_end') != target:
        raise ValueError('本次目标日主行情尚未发布，不能使用旧日行情选股')
    with connect() as connection:
        root_status = connection.execute('SELECT status FROM job WHERE job_id=?', (root_id,)).fetchone()
        publication = connection.execute("SELECT snapshot_id,data_end,lineage_json FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1", (root_id,)).fetchone()
        children = {item['operation']: dict(connection.execute('SELECT * FROM job WHERE job_id=?', (item['task_id'],)).fetchone())
                    for item in state['dependencies'] if item['operation'] in ('sector_charts', 'market_supplement')}
    if not publication and (not root_status or root_status[0] != 'no_change'):
        raise ValueError('本次主行情缺少目标日发布回执，不用其他任务的旧发布替代')
    if publication and (publication['data_end'] != target
                        or json.loads(publication['lineage_json']).get('market_snapshot_id') != snapshot):
        raise ValueError('当前主行情与本次增量发布代际不一致，不追跑旧任务')
    proof = {'target_date': target, 'market_snapshot_id': snapshot, 'historical_gaps_remain_unknown': True}
    if 'sector_charts' in children:
        from .sector_chart_batch import read_frozen_sector_chart_batch_report
        parameters = json.loads(children['sector_charts']['parameters_json'])
        report = read_frozen_sector_chart_batch_report(parameters, mode='increment')
        coverage = (report or {}).get('coverage', {})
        if (not report or report.get('date') != target or report.get('snapshot_id') != snapshot
                or coverage.get('collection_complete') is not True
                or coverage.get('failed_count') != 0 or coverage.get('unrequested_count') != 0):
            raise ValueError('本次板块K线采集回执缺失、未通过SHA核验或仍有未完成请求；未开始策略')
        proof['sector'] = {'quality': report.get('quality'), 'coverage': {key: coverage.get(key) for key in
                           ('collection_complete','available_count','total_count','failed_count','unrequested_count','complete_count','partial_count')},
                           'request_signature': parameters.get('request_signature')}
    from .market_supplement import load_supplement_with_fingerprint
    doc, fingerprint = load_supplement_with_fingerprint(snapshot, dates=[target],
        fields=['public_pools', 'annual_pools', 'annual_down_pools', 'sector_index_history'])
    supplement = children.get('market_supplement')
    if (not supplement or not fingerprint or doc.get('target_date') != target
            or doc.get('snapshot_id') != snapshot or doc.get('job_id') != supplement['job_id']
            or doc.get('quality') not in ('complete', 'partial')):
        raise ValueError('本次目标日盘面补充尚未发布或未通过SHA核验；不能回退旧日补充选股')
    if not any(target in doc.get(field, {}) for field in ('public_pools', 'annual_pools', 'annual_down_pools')):
        raise ValueError('本次目标日来源池未留存，不能以历史来源池代替')
    coverage_fields=('covered_days','observed_days','expected_days','complete','state')
    failures=doc.get('failures') or []
    proof['supplement'] = {'quality': doc['quality'],
        'annual_coverage': {key:(doc.get('annual_coverage') or {}).get(key) for key in coverage_fields},
        'annual_down_coverage': {key:(doc.get('annual_down_coverage') or {}).get(key) for key in coverage_fields},
        'failure_count': len(failures), 'failures': failures[:10],
        'input_fingerprint': {'contract': fingerprint[0], 'snapshot_id': snapshot,
                              'index_sha256': fingerprint[3]}}
    return proof


def guard(root_id):
    with connect() as connection:
        root = connection.execute('SELECT * FROM job WHERE job_id=?', (root_id,)).fetchone()
        if root is None:
            raise ValueError('同步根任务不存在')
        from .sync_progress import chain_stop_action
        stop = chain_stop_action(connection, root)
        if stop == 'pause':
            raise tasks.TaskPaused()
        if stop:
            raise tasks.TaskCancelled()
        state = dependency_state(connection, root)
    if state['state'] != 'ready':
        raise ValueError(state.get('reason') or '原任务数据依赖尚未完整完成')


def _block(connection, root, state):
    if _event(connection, root['job_id'], BLOCKED) is None:
        tasks.append_event(connection, root['job_id'], BLOCKED,
            state.get('reason') or '同步后计算未完成，请检查缺口后手动执行', 'warning', state)
    # A skipped reminder is explicit, rather than an indefinitely missing link.
    params = json.loads(root['parameters_json'])
    if params.get('line_alert_check_version') and _event(connection, root['job_id'], 'line_alert_check_queued') is None:
        tasks.append_event(connection, root['job_id'], 'line_alert_check_queued',
            '必需数据或后置计算未完整成功，本次不执行划线提醒', 'warning',
            {'skipped': True, 'target_date': state.get('target_date'), 'reason': state.get('reason')})


def run(root_id):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        root = connection.execute('SELECT * FROM job WHERE job_id=?', (root_id,)).fetchone()
        if root is None:
            return None
        state = dependency_state(connection, root)
        if state['state'] in ('waiting', 'legacy', 'stopped'):
            return None
        if state['state'] == 'blocked':
            _block(connection, root, state)
            return state
        if state['state'] == 'completed':
            completed = True
        else:
            # Existing API calculations retain their own lease/recovery rules;
            # never start a nested calculation while another job owns the slot.
            if connection.execute("SELECT 1 FROM job WHERE status='running' LIMIT 1").fetchone():
                return None
            completed = False
    if not completed:
        try:
            proof = verify_publications(root_id, state)
        except (ValueError, OSError, KeyError, TypeError) as error:
            state.update(state='blocked', reason=str(error))
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                _block(connection, root, state)
            return state
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            tasks.append_event(connection, root_id, 'workspace_followup_started',
                '本次目标日数据步骤已结束并核验发布，开始选股及跟踪；历史缺口仍逐只待核验',
                details={**state, 'publication_proof': proof})
        try:
            from .workspace_automation import run_after_sync
            results = run_after_sync(root_id)
            guard(root_id)
            failed = any('error' in item or 'tracking_review_error' in item for item in results)
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                saved = connection.execute('SELECT * FROM job WHERE job_id=?', (root_id,)).fetchone()
                from .sync_progress import chain_stop_action
                if chain_stop_action(connection, saved):
                    return None
                if failed:
                    state.update(state='blocked', reason='数据已完成，但选股或跟踪计算未完整成功；请查看任务后手动重试', results=results)
                    _block(connection, saved, state)
                    return state
                if _event(connection, root_id, COMPLETE) is None:
                    tasks.append_event(connection, root_id, COMPLETE,
                        '数据链完成后的选股与跟踪已结束，接续划线提醒',
                        details={**state, 'strategy_count': sum('strategy_id' in item for item in results)})
        except (tasks.TaskPaused, tasks.TaskCancelled):
            raise
        except Exception as error:
            state.update(state='blocked', reason='同步后计算中断，需检查任务后手动执行；未自动反复重试',
                         error_type=type(error).__name__)
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                _block(connection, root, state)
            return state
        finally:
            with connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                tasks.append_event(connection, root_id, RELEASED, '本次同步后计算执行已退出，不再写入策略或跟踪')
    from .line_alerts import after_sync
    after_sync(dict(root))
    return {'state': 'completed'}


def recover(limit=20):
    """Only repair already authorized phase gaps; never resume an explicit stop."""
    with connect() as connection:
        roots = [row[0] for row in connection.execute("""SELECT job_id FROM job j WHERE kind='increment'
            AND status IN ('succeeded','partial','no_change')
            AND json_extract(parameters_json,'$.workspace_followup_version')=?
            AND EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='chain_authorized')
            AND coalesce((SELECT e.stage FROM job_event e WHERE e.job_id=j.job_id
                AND e.stage IN ('increment_session_opened','increment_chain_stop_requested')
                AND CASE WHEN e.stage='increment_session_opened'
                    THEN coalesce(json_extract(e.details_json,'$.attempt_task_id'),j.job_id)
                    ELSE coalesce(json_extract(e.details_json,'$.scope_task_id'),j.job_id) END=j.job_id
                ORDER BY e.seq DESC LIMIT 1),'')!='increment_chain_stop_requested'
            AND NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='workspace_followup_blocked')
            AND (NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='workspace_followup_completed')
                OR json_extract(parameters_json,'$.line_alert_check_version') IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='line_alert_check_queued'))
            ORDER BY created_at_ms LIMIT ?""", (VERSION, max(1, min(limit, 100))))]
    results=[]
    for identifier in roots:
        try:results.append(run(identifier))
        except (tasks.TaskPaused,tasks.TaskCancelled):results.append(None)
    return results
