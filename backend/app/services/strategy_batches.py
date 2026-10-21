"""Durable ordered strategy batches over existing jobs; no browser-owned execution loop."""
import json

from fastapi import HTTPException

from app.core import connect, digest, dumps, new_id, now_ms
from app.services import task_service as tasks
from app.services.workspace_common import current, freeze_inputs

MAX_STRATEGIES = 200
OPERATION = 'strategy_batch'
ITEM_OPERATION = 'strategy_batch_item'
TRACKING_OPERATION = 'strategy_batch_tracking'
PLAN_VERSION = 'selection-and-tracking-v1'
TRACKING_CHUNK_SIZE = {'stock': 500, 'sector': 800}


def _parameters(row):
    return json.loads(row['parameters_json'])


def is_item(job):
    return job['kind'] == 'selection' and _parameters(job).get('operation') == ITEM_OPERATION


def _topic_revision_signature(connection):
    # Both tables are append-only; indexed rowid maxima detect local member or
    # catalog edits without materializing all topic members/history in a batch.
    return digest([connection.execute('SELECT max(rowid) FROM '+table).fetchone()[0]
                   for table in ('topic_revision', 'topic_override')])


def _request(payload):
    if not isinstance(payload, dict) or set(payload)-{'target_type', 'scope', 'strategy_ids', 'request_key'}:
        raise HTTPException(422, '批量运行只接受目标类型、执行范围、策略ID和请求标识')
    target = payload.get('target_type', 'stock'); scope = payload.get('scope', 'selected')
    if target not in ('stock', 'sector') or scope not in ('selected', 'all'):
        raise HTTPException(422, '批量目标为个股或板块，范围为所选或当前类型全部策略')
    ids = payload.get('strategy_ids', [])
    if not isinstance(ids, list) or any(not isinstance(value, str) or not value or len(value) > 200 for value in ids):
        raise HTTPException(422, '策略ID需要是有效字符串列表')
    if len(ids) > MAX_STRATEGIES or len(ids) != len(set(ids)):
        raise HTTPException(422, f'每批最多{MAX_STRATEGIES}个策略，不能重复')
    if (scope == 'selected' and not ids) or (scope == 'all' and ids):
        raise HTTPException(422, '执行所选需要至少一个策略；执行全部不接受另一个策略列表')
    key = payload.get('request_key') if payload.get('request_key') is not None else new_id('strategy-batch-request')
    if not isinstance(key, str) or not 1 <= len(key) <= 200:
        raise HTTPException(422, '请求标识需要1至200字符')
    return target, scope, ids, key


def create_batch(payload):
    target, scope, ids, key = _request(payload)
    request = {'target_type': target, 'scope': scope, 'strategy_ids': ids}
    signature = digest({**request, 'auto_add': True, 'plan_version': PLAN_VERSION})
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        previous = connection.execute('SELECT * FROM job WHERE request_key=?', (key,)).fetchone()
        if previous:
            previous_parameters = _parameters(previous)
            # Replaying an old request retains its original selection-only intent.
            expected = signature if previous_parameters.get('plan_version') == PLAN_VERSION else digest(request)
            if previous['kind'] != 'source_check' or previous_parameters.get('operation') != OPERATION or previous_parameters.get('request_signature') != expected:
                raise HTTPException(409, '此请求标识已用于不同任务，请使用新的请求标识')
            return _view(connection, previous)
        running = connection.execute("SELECT 1 FROM job WHERE status='running' LIMIT 1").fetchone()
        if running: tasks.active_task_conflict(connection)
        existing = connection.execute("SELECT * FROM job WHERE kind='source_check' AND status IN ('queued','running','paused') AND json_extract(parameters_json,'$.operation')=? ORDER BY created_at_ms LIMIT 1", (OPERATION,)).fetchone()
        if existing:
            active = _view(connection, existing)
            raise HTTPException(409, {'message': f'已有策略批次未结束：{active["completed_count"]}/{active["total_count"]}。请查看原批次，继续或取消后再新建。', 'batch_id': existing['job_id']})
        table = 'strategy' if target == 'stock' else 'sector_strategy'
        if scope == 'all':
            ids = [row['strategy_id'] for row in connection.execute('SELECT strategy_id FROM '+table+' WHERE archived=0 ORDER BY created_at_ms,strategy_id')]
        if not ids or len(ids) > MAX_STRATEGIES:
            raise HTTPException(422, f'当前类型需要1至{MAX_STRATEGIES}个可运行策略')
        day = current(connection); dependencies, primary = freeze_inputs(connection)
        local_inputs = {'topic_revision_signature': _topic_revision_signature(connection)} if target == 'stock' else {}
        if target == 'stock':
            from .strategies import ensure_evaluator_version
            read_strategy = lambda identifier: ensure_evaluator_version(connection, identifier)
        else:
            from .sector_strategies import _view as sector_view, _version, EVALUATOR_VERSION
            def read_strategy(identifier):
                strategy = sector_view(connection, identifier)
                if strategy['evaluator_version'] != EVALUATOR_VERSION:
                    _version(connection, identifier, strategy['version']+1, strategy)
                    strategy = sector_view(connection, identifier)
                return strategy
        frozen = []
        for identifier in ids:
            strategy = read_strategy(identifier)
            if strategy['archived']: raise HTTPException(409, '已归档策略不能加入批量运行')
            frozen.append({'strategy_id': identifier, 'name': strategy['name'], 'version': strategy['version']})
        identifier = new_id('strategy-batch'); stamp = now_ms()
        for index, strategy in enumerate(frozen):
            job_id = new_id('job'); request_key = f'{identifier}:{index+1}'
            tracking_id = new_id('job')
            strategy.update(job_id=job_id, tracking_job_id=tracking_id, sequence=index+1)
            parameters = {'operation': ITEM_OPERATION, 'batch_job_id': identifier, 'target_type': target,
                          'sequence': index+1,
                          'strategy_id': strategy['strategy_id'], 'version': strategy['version'],
                          'business_date': day, 'input_snapshots': dependencies, 'primary_snapshot_id': primary, **local_inputs}
            connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'selection',?,?,?,'queued',100,?)",
                               (job_id, request_key, request_key, day, dumps(parameters), digest(parameters), stamp))
            tasks.append_event(connection, job_id, 'queued', f'批量选股 {index+1}/{len(frozen)} · {strategy["name"]}，等待顺序执行')
            tracking_parameters = {**parameters, 'operation': TRACKING_OPERATION, 'selection_job_id': job_id,
                                   'auto_add': True, 'plan_version': PLAN_VERSION}
            tracking_key = request_key+':tracking'
            connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',100,?)",
                               (tracking_id, tracking_key, tracking_key, day, dumps(tracking_parameters), digest(tracking_parameters), stamp))
            tasks.append_event(connection, tracking_id, 'queued', '已授权自动入池；等待本策略筛选结果，跳过已跟踪与手工忽略项')
        parameters = {'operation': OPERATION, 'target_type': target, 'scope': scope, 'auto_add': True, 'plan_version': PLAN_VERSION,
                      'request_signature': signature, 'business_date': day, 'input_snapshots': dependencies,
                      'primary_snapshot_id': primary, 'items': frozen, **local_inputs}
        # Parent is inserted last. The queue also explicitly withholds parents
        # until every persisted child is terminal, independent of timestamps.
        connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,planned_batches,created_at_ms) VALUES(?,?,?,'source_check',?,?,?,'queued',100,?)",
                           (identifier, key, identifier, day, dumps(parameters), digest(parameters), stamp))
        tasks.append_event(connection, identifier, 'queued', f'已保存{len(frozen)}个策略的筛选及自动入池顺序；已跟踪与手工忽略项跳过', details={'actual_completed_batches': 0, 'actual_total_batches': len(frozen)})
        return _view(connection, connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone())


def _items(connection, parent):
    parameters = _parameters(parent); target = parameters['target_type']; frozen = parameters['items']
    automatic = parameters.get('auto_add') is True and parameters.get('plan_version') == PLAN_VERSION
    identifiers = [identifier for item in frozen for identifier in (item['job_id'], item.get('tracking_job_id')) if identifier]
    table = 'selection_run' if target == 'stock' else 'sector_selection_run'
    records = {row['job_id']: dict(row) for row in connection.execute(
        'SELECT j.job_id,j.status,j.phase,j.accepted_batches,j.error_summary,j.finished_at_ms,r.run_id FROM job j LEFT JOIN '+table+' r USING(job_id) WHERE j.job_id IN ('+','.join('?' for _ in identifiers)+')', identifiers)}
    result = []
    for item in frozen:
        selected = records.get(item['job_id'], {}); tracked = records.get(item.get('tracking_job_id'), {})
        selection_status = selected.get('status', 'unknown'); tracking_status = tracked.get('status') if automatic else None
        current = tracked if automatic and selection_status in tasks.TERMINAL else selected
        status = current.get('status', 'unknown')
        # A failed selection still has a dependent no-op to acknowledge before
        # the following strategy is eligible; it never becomes a successful item.
        if automatic and selection_status in tasks.TERMINAL and tracking_status not in tasks.TERMINAL:
            status = 'running' if tracking_status == 'running' else tracking_status or 'queued'
        if automatic and selection_status in ('failed', 'cancelled') and tracking_status in tasks.TERMINAL:
            status = selection_status
        matched = None
        if selected.get('run_id'):
            event = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='completed' ORDER BY seq DESC LIMIT 1", (item['job_id'],)).fetchone()
            if event: matched = json.loads(event[0]).get('matched')
            if matched is None:
                candidates = 'selection_candidate' if target == 'stock' else 'sector_selection_candidate'
                matched = connection.execute('SELECT count(*) FROM '+candidates+" WHERE run_id=? AND decision='matched'", (selected['run_id'],)).fetchone()[0]
        totals = {'added_count': None, 'skipped_count': None, 'ignored_count': None}
        if automatic and tracked:
            receipts = [json.loads(row[0]) for row in connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='auto_tracking_chunk' ORDER BY seq", (item['tracking_job_id'],))]
            if receipts: totals = {key: sum(receipt.get(key, 0) for receipt in receipts) for key in totals}
        result.append({**item, 'status': status, 'stage': current.get('phase'),
             'progress': current.get('accepted_batches'), 'run_id': selected.get('run_id'),
             'selection_status': selection_status, 'tracking_status': tracking_status,
             'matched_count': matched, **totals,
             'error': selected.get('error_summary') or tracked.get('error_summary')})
    return result


def _view(connection, parent):
    parameters = _parameters(parent)
    items = _items(connection, parent)
    automatic = parameters.get('auto_add') is True and parameters.get('plan_version') == PLAN_VERSION
    finished = [item for item in items if item['status'] in tasks.TERMINAL]
    total_steps = len(items)*(2 if automatic else 1)
    completed_steps = sum(item['selection_status'] in tasks.TERMINAL for item in items) + (sum(item['tracking_status'] in tasks.TERMINAL for item in items) if automatic else 0)
    successful = [item for item in items if item['status'] in ('succeeded', 'no_change', 'partial') and item['run_id']]
    current_item = next((item for item in items if item['status'] == 'running'), None)
    status = parent['status']
    if status == 'queued' and (current_item or completed_steps): status = 'running'
    return {'batch_id': parent['job_id'], 'job_id': parent['job_id'], 'request_key': parent['request_key'], 'target_type': parameters['target_type'],
            'status': status, 'business_date': parameters['business_date'], 'items': items,
            'auto_add': automatic,
            'plan_version': parameters.get('plan_version'),
            **{key: (sum(item[key] or 0 for item in items) if any(item[key] is not None for item in items) else None)
               for key in ('matched_count', 'added_count', 'skipped_count', 'ignored_count')},
            'total_count': len(items), 'completed_count': len(finished), 'succeeded_count': len(successful),
            'failed_count': sum(item['status'] == 'failed' for item in items),
            'cancelled_count': sum(item['status'] == 'cancelled' for item in items),
            'current_strategy': current_item,
            'completed_steps': completed_steps, 'total_steps': total_steps,
            'progress': round(completed_steps/max(1,total_steps)*100, 1),
            'created_at_ms': parent['created_at_ms'], 'finished_at_ms': parent['finished_at_ms']}


def get_batch(identifier):
    with connect() as connection:
        row = connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone()
        if not row or row['kind'] != 'source_check' or _parameters(row).get('operation') != OPERATION:
            raise HTTPException(404, '策略批次不存在')
        return _view(connection, row)


def list_batches(target_type, limit=10):
    if target_type not in ('stock', 'sector'): raise HTTPException(422, '请选择个股或板块批次')
    with connect() as connection:
        records = connection.execute("SELECT * FROM job WHERE kind='source_check' AND json_extract(parameters_json,'$.operation')=? AND json_extract(parameters_json,'$.target_type')=? ORDER BY created_at_ms DESC,rowid DESC LIMIT ?", (OPERATION, target_type, max(1, min(limit, 20)))).fetchall()
        return {'items': [_view(connection, row) for row in records], 'total': len(records)}


def validate_item(connection, job, strategy, day, dependencies, primary):
    actual = check_item_lease(job, connection)
    parameters = _parameters(actual)
    parent = connection.execute('SELECT status FROM job WHERE job_id=?', (parameters['batch_job_id'],)).fetchone()
    if not parent or parent['status'] not in ('queued', 'running'): raise HTTPException(409, '策略批次已停止，不继续执行子任务')
    if (parameters['strategy_id'] != strategy['strategy_id'] or parameters['version'] != strategy['version']):
        raise HTTPException(409, '策略规则版本已变化，本项未运行；其他策略按原顺序继续')
    if parameters['business_date'] != day or parameters['input_snapshots'] != dependencies or parameters['primary_snapshot_id'] != primary:
        raise HTTPException(409, '行情日期或输入快照已变化，本项未运行，请按当前行情新建批次')


def check_item_lease(job, connection):
    """A paused/resumed/recovered job is a different execution attempt."""
    actual = connection.execute('SELECT * FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
    if not actual or _parameters(actual).get('operation') not in (ITEM_OPERATION, TRACKING_OPERATION) or actual['status'] != 'running' or actual['lease_owner'] != job['lease_owner']:
        raise tasks.TaskCancelled()
    parent = connection.execute('SELECT status,parameters_json FROM job WHERE job_id=?', (_parameters(actual)['batch_job_id'],)).fetchone()
    if not parent or parent['status'] not in ('queued', 'running'): raise tasks.TaskCancelled()
    parameters = _parameters(actual)
    slot = 'job_id' if parameters['operation'] == ITEM_OPERATION else 'tracking_job_id'
    if not any(item.get(slot) == job['job_id'] and item['strategy_id'] == parameters['strategy_id']
               and item['sequence'] == parameters['sequence'] for item in _parameters(parent)['items']):
        raise HTTPException(409, '内部步骤不属于此冻结批次，不能新增自动入池授权')
    if 'topic_revision_signature' in parameters and parameters['topic_revision_signature'] != _topic_revision_signature(connection):
        raise HTTPException(409, '本地题材或成员修改已变化，本项未保存混合输入；请按当前成员新建批次')
    return actual


def _record_progress(identifier):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        parent = connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone()
        if not parent or parent['status'] not in ('queued', 'running'): return
        batch = _view(connection, parent); current_item = batch['current_strategy']
        message = f'已完成 {batch["completed_count"]}/{batch["total_count"]} 个策略'
        if batch['auto_add']: message += f' · 筛选/入池步骤 {batch["completed_steps"]}/{batch["total_steps"]}'
        if current_item: message += ' · 当前：'+current_item['name']
        percentage = min(99, round(batch['progress']))
        connection.execute('UPDATE job SET accepted_batches=?,phase=? WHERE job_id=?', (percentage, '顺序执行策略', identifier))
        tasks.append_event(connection, identifier, '顺序执行策略', message, details={'actual_completed_batches': batch['completed_steps'], 'actual_total_batches': batch['total_steps']})


def execute_item(job):
    parameters = _parameters(job)
    parent_id = parameters['batch_job_id']
    _record_progress(parent_id)
    try:
        if parameters['target_type'] == 'sector':
            from .sector_strategies import run_strategy
        else:
            from .strategies import run_strategy
        return run_strategy(parameters['strategy_id'], {'request_key': job['request_key']}, queued_job=job)
    except Exception as error:
        reason = str(getattr(error, 'detail', error))[:500]
        tasks.finish(job['job_id'], 'failed', '本项策略失败，其他项继续', reason, lease_owner=job['lease_owner'])
        raise
    finally:
        _record_progress(parent_id)


def _tracking_guard(connection, job):
    actual = check_item_lease(job, connection); parameters = _parameters(actual)
    parent = connection.execute('SELECT parameters_json FROM job WHERE job_id=?', (parameters['batch_job_id'],)).fetchone()
    authorized = _parameters(parent)
    if (parameters.get('operation') != TRACKING_OPERATION or parameters.get('auto_add') is not True
            or parameters.get('plan_version') != PLAN_VERSION or authorized.get('auto_add') is not True
            or authorized.get('plan_version') != PLAN_VERSION):
        raise HTTPException(409, '此历史批次没有自动入池授权，不能追加跟踪')
    if current(connection) != parameters['business_date'] or freeze_inputs(connection) != (parameters['input_snapshots'], parameters['primary_snapshot_id']):
        raise HTTPException(409, '行情或输入已变化，保留已保存筛选结果，本项未继续自动入池')
    table = 'strategy' if parameters['target_type'] == 'stock' else 'sector_strategy'
    strategy = connection.execute('SELECT archived,current_version FROM '+table+' WHERE strategy_id=?', (parameters['strategy_id'],)).fetchone()
    if not strategy or strategy['archived'] or strategy['current_version'] != parameters['version']:
        raise HTTPException(409, '策略已修改或归档，保留筛选结果，本项未继续自动入池')


def _tracking_receipt(connection, job, index, signature, count, result, total_count):
    """Write accounting in the same transaction as its tracking rounds."""
    _tracking_guard(connection, job)
    details = {'chunk_index': index, 'chunk_signature': signature, 'matched_count': count,
               'added_count': result['added_count'], 'skipped_count': len(result['skipped']),
               'ignored_count': sum(item.get('reason_code') == 'ignored' for item in result['skipped']),
               'skipped': result['skipped']}
    tasks.append_event(connection, job['job_id'], 'auto_tracking_chunk',
        f'自动入池批次 {index+1}/{max(1,total_count)}：新增 {details["added_count"]}，跳过 {details["skipped_count"]}（含忽略 {details["ignored_count"]}）', details=details)
    connection.execute("UPDATE job SET phase='自动加入跟踪池',accepted_batches=?,lease_expires_at_ms=? WHERE job_id=?",
        (min(99, round((index+1)/max(1,total_count)*99)), now_ms()+60_000, job['job_id']))


def execute_tracking(job):
    parameters = _parameters(job); parent_id = parameters['batch_job_id']
    _record_progress(parent_id)
    try:
        target = parameters['target_type']; run_table = 'selection_run' if target == 'stock' else 'sector_selection_run'
        candidate_table = 'selection_candidate' if target == 'stock' else 'sector_selection_candidate'
        id_field = 'instrument_id' if target == 'stock' else 'topic_code'
        with connect() as connection:
            check_item_lease(job, connection)
            selection = connection.execute('SELECT status FROM job WHERE job_id=?', (parameters['selection_job_id'],)).fetchone()
            if not selection or selection['status'] not in tasks.TERMINAL:
                raise HTTPException(409, '筛选步骤尚未结束，不能自动入池')
            run = connection.execute('SELECT run_id FROM '+run_table+" WHERE job_id=? AND status='completed'", (parameters['selection_job_id'],)).fetchone()
            if selection['status'] not in ('succeeded','no_change','partial') or not run:
                tasks.finish(job['job_id'], 'no_change', '筛选未成功，没有自动加入跟踪', lease_owner=job['lease_owner'])
                return
            _tracking_guard(connection, job)
            run_id = run['run_id']
            identifiers = [row[0] for row in connection.execute('SELECT '+id_field+' FROM '+candidate_table+" WHERE run_id=? AND decision='matched' ORDER BY "+id_field, (run_id,))]
        size = TRACKING_CHUNK_SIZE[target]
        chunks = [identifiers[offset:offset+size] for offset in range(0,len(identifiers),size)] or [[]]
        for index, chunk in enumerate(chunks):
            signature = digest({'run_id': run_id, 'ids': chunk})
            with connect() as connection:
                _tracking_guard(connection, job)
                saved = connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='auto_tracking_chunk' AND json_extract(details_json,'$.chunk_index')=?", (job['job_id'],index)).fetchone()
                if saved:
                    if json.loads(saved[0]).get('chunk_signature') != signature:
                        raise HTTPException(409, '自动入池已提交分块的依据不一致，拒绝重复入池')
                    continue
            if not chunk:
                with connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    _tracking_receipt(connection, job, index, signature, 0, {'added_count':0,'skipped':[]}, len(chunks))
            else:
                if target == 'stock':
                    from .tracking import add_tracking
                    payload = {'run_id':run_id, 'instrument_ids':chunk}
                else:
                    from .sector_tracking import add_tracking
                    payload = {'run_id':run_id, 'target_ids':chunk}
                add_tracking(payload, automatic=True,
                    transaction_guard=lambda connection: _tracking_guard(connection,job),
                    on_commit=lambda connection,result: _tracking_receipt(connection,job,index,signature,len(chunk),result,len(chunks)))
        tasks.finish(job['job_id'], 'succeeded', '本策略命中已处理：已跟踪与手工忽略项跳过，原轮次基准保留', lease_owner=job['lease_owner'])
    except Exception as error:
        reason = str(getattr(error,'detail',error))[:500]
        tasks.finish(job['job_id'], 'failed', '自动入池未完成；筛选结果保留，其他策略继续', reason, lease_owner=job['lease_owner'])
        raise
    finally:
        _record_progress(parent_id)


def finish_batch(job):
    with connect() as connection:
        parent = connection.execute('SELECT * FROM job WHERE job_id=?', (job['job_id'],)).fetchone()
        batch = _view(connection, parent)
    if any(item['status'] not in tasks.TERMINAL for item in batch['items']):
        raise ValueError('批次仍有未完成策略，不能提前汇总')
    status = 'succeeded' if batch['succeeded_count'] == batch['total_count'] else 'partial' if batch['succeeded_count'] else 'failed'
    tasks.finish(job['job_id'], status,
        f'批量筛选结束：{batch["succeeded_count"]}项完成，{batch["failed_count"]}项失败，{batch["cancelled_count"]}项取消；结果分别保存' + (f'；已入池{batch["added_count"] or 0}，跳过{batch["skipped_count"] or 0}（其中忽略{batch["ignored_count"] or 0}）' if batch['auto_add'] else '；仅筛选的历史批次，不自动入池'), lease_owner=job['lease_owner'])


def change_batch(identifier, action):
    if action == 'retry': raise HTTPException(422, '请在策略页重新执行所选或全部，以冻结新的规则和行情；已完成结果不会自动重跑')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        parent = connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone()
        if not parent: raise HTTPException(404, '策略批次不存在')
        parameters = _parameters(parent); stamp = now_ms()
        if action == 'resume' and parent['status'] == 'paused':
            day = current(connection); dependencies, primary = freeze_inputs(connection)
            if day != parameters['business_date'] or dependencies != parameters['input_snapshots'] or primary != parameters['primary_snapshot_id']:
                raise HTTPException(409, '行情日期或输入已变化，请新建批次；原暂停批次不会自动继续')
            if 'topic_revision_signature' in parameters and parameters['topic_revision_signature'] != _topic_revision_signature(connection):
                raise HTTPException(409, '本地题材或成员修改已变化，请新建批次；原暂停批次不会自动继续')
            target, phase = 'queued', 'queued'
        elif action == 'pause' and parent['status'] in ('queued', 'running'): target, phase = 'paused', 'paused'
        elif action == 'cancel' and parent['status'] not in tasks.TERMINAL: target, phase = 'cancelled', 'cancelled'
        else: return tasks.task_view(identifier)
        children = [identifier for item in parameters['items'] for identifier in (item['job_id'], item.get('tracking_job_id')) if identifier]
        for child_id in children:
            child = connection.execute('SELECT status FROM job WHERE job_id=?', (child_id,)).fetchone()
            if not child or child['status'] in tasks.TERMINAL: continue
            if action == 'resume' and child['status'] != 'paused': continue
            connection.execute('UPDATE job SET status=?,phase=?,accepted_batches=CASE WHEN ?=\'queued\' THEN 0 ELSE accepted_batches END,finished_at_ms=CASE WHEN ?=\'cancelled\' THEN ? ELSE finished_at_ms END,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?',
                               (target, phase, target, target, stamp, child_id))
            tasks.append_event(connection, child_id, phase, '用户'+{'queued': '继续', 'paused': '暂停', 'cancelled': '取消'}[target]+'策略批次；已完成子项不重跑')
        connection.execute('UPDATE job SET status=?,phase=?,finished_at_ms=CASE WHEN ?=\'cancelled\' THEN ? ELSE finished_at_ms END,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?', (target, phase, target, stamp, identifier))
        tasks.append_event(connection, identifier, phase, '策略批次状态已更新；已保存运行结果保留')
    return tasks.task_view(identifier)
