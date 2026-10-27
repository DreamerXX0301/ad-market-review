"""Durable manual tasks, ordered progress and a single-consumer lease."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from fastapi import HTTPException

from app.core import connect, digest, dumps, new_id, now_ms, raw_dir

TERMINAL = {"succeeded", "no_change", "partial", "failed", "cancelled"}


class TaskCancelled(Exception):
    pass


class TaskPaused(Exception):
    pass


def initialization_state(connection=None):
    """A published main-data head survives failed auxiliary jobs and restarts.

    Inspect SQLite inside the caller's write transaction when guarding task
    creation. No file/network I/O or mutable MarketData cache is involved.
    """
    if connection is None:
        with connect() as current:
            return initialization_state(current)
    row = connection.execute("SELECT s.snapshot_id,s.data_end FROM published_head h JOIN dataset_snapshot s ON s.snapshot_id=h.snapshot_id WHERE h.module IN ('daily_raw','analysis_bars') AND s.state='published' ORDER BY h.changed_at_ms DESC LIMIT 1").fetchone()
    return {'can_initialize': row is None, 'main_published': row is not None,
            'state': 'published' if row else 'empty',
            'published_snapshot_id': row['snapshot_id'] if row else None,
            'data_end': row['data_end'] if row else None,
            'reason': '主行情已成功发布，请使用盘后增量；题材或盘面补充失败请单独重试，不要重复首次初始化' if row else None}


def _guard_initialization(connection, kind):
    if kind == 'initialise':
        state = initialization_state(connection)
        if not state['can_initialize']:
            raise HTTPException(409, state['reason'])


def append_event(connection, job_id: str, stage: str, message: str, level="info", details=None):
    seq = connection.execute("SELECT coalesce(max(seq),0)+1 FROM job_event WHERE job_id=?", (job_id,)).fetchone()[0]
    connection.execute("INSERT INTO job_event(job_id,seq,occurred_at_ms,level,stage,message,details_json) VALUES(?,?,?,?,?,?,?)", (job_id, seq, now_ms(), level, stage, message, dumps(details or {})))


def create_task(kind: str, parameters: dict, target_date: str | None = None, request_key: str | None = None, retry_of: str | None = None, *, chain_parent_id: str | None = None) -> dict:
    request_key = request_key or new_id("request")
    if not isinstance(request_key,str) or not 1<=len(request_key)<=200:
        raise HTTPException(422,'请求标识须为1至200字符')
    logical_key = digest({"kind": kind, "target_date": target_date, "parameters": parameters})
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        from .increment_access import business_http_request
        from . import sync_progress
        locked = sync_progress.lock_record(connection)
        if business_http_request.get() and locked and (kind != 'increment' or locked['request_key'] != request_key):
            sync_progress.require_unlocked(connection)
        if kind == 'increment' and connection.execute('SELECT 1 FROM platform_setting WHERE key=?', ('increment-abandoned:' + request_key,)).fetchone():
            raise HTTPException(409, '本次请求已在服务端确认放弃；迟到的提交不会创建任务，请使用新的请求标识')
        if kind in ('selection','backtest') and parameters.get('strategy_id'):
            from .workspace_deletion import require_strategy_present
            require_strategy_present(connection,parameters['strategy_id'])
        old = connection.execute("SELECT job_id,kind,target_date,parameters_hash FROM job WHERE request_key=?", (request_key,)).fetchone()
        if old:
            if old['kind']!=kind or old['target_date']!=target_date or old['parameters_hash']!=digest(parameters):
                raise HTTPException(409,'此请求标识已用于不同任务参数，请使用新的请求标识')
            identifier = old[0]
        else:
            if business_http_request.get() and not retry_of:
                from .sync_completion import require_available
                require_available(connection, kind, parameters)
            _guard_initialization(connection, kind)
            if kind == 'initialise':
                initial = connection.execute("SELECT job_id,logical_key FROM job WHERE kind='initialise' AND status IN ('queued','running','paused') LIMIT 1").fetchone()
                if initial and initial['logical_key'] != logical_key:
                    raise HTTPException(409, '已有首次初始化任务，请查看、继续或取消原任务后再操作')
            active = connection.execute("SELECT job_id FROM job WHERE logical_key=? AND status IN ('queued','running','paused')", (logical_key,)).fetchone()
            if active:
                if kind == 'increment' and parameters.get('increment_session_version') == sync_progress.LOCK_VERSION:
                    sync_progress.bind_session(connection, connection.execute('SELECT * FROM job WHERE job_id=?', (active[0],)).fetchone())
                return task_view(active[0])
            identifier = new_id("job")
            connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,planned_batches,retry_of_job_id,created_at_ms) VALUES(?,?,?,?,?,?,?,'queued',100,?,?)", (identifier, request_key, logical_key, kind, target_date, dumps(parameters), digest(parameters), retry_of, now_ms()))
            append_event(connection, identifier, "queued", "任务已提交，等待工作进程", details={"kind": kind, "target_date": target_date})
            if kind == 'increment' and parameters.get('increment_session_version') == sync_progress.LOCK_VERSION:
                sync_progress.bind_session(connection, connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone())
            elif parameters.get('parent_sync_job_id') and not (retry_of and business_http_request.get()) and (stop_action := sync_progress.chain_stop_action(connection, connection.execute('SELECT * FROM job WHERE job_id=?', (identifier,)).fetchone(),parent_task_id=chain_parent_id)):
                # A stop can arrive between worker planning and committing a
                # child. Freeze that late child stopped before it is runnable.
                stopped = 'paused' if stop_action == 'pause' else 'cancelled'
                connection.execute('UPDATE job SET status=?,finished_at_ms=? WHERE job_id=?', (stopped, now_ms() if stopped == 'cancelled' else None, identifier))
                append_event(connection, identifier, stopped, '本次数据链已请求停止，后续步骤未开始执行')
            if retry_of and business_http_request.get() and kind != 'increment':
                root = sync_progress.root_for_task(connection, retry_of)
                if root is not None:
                    sync_progress.bind_session(connection, root, attempt_task_id=identifier)
                    append_event(connection, retry_of, 'increment_retry_queued', '用户手动重试本步骤，重新锁定完整增量结果', details={'next_task_id': identifier})
    return task_view(identifier)


def task_view(identifier: str, after_seq: int = 0) -> dict:
    with connect() as connection:
        row = connection.execute("SELECT * FROM job WHERE job_id=?", (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        events = connection.execute("SELECT * FROM job_event WHERE job_id=? AND seq>? ORDER BY seq", (identifier, after_seq)).fetchall()
    item = dict(row)
    item.update(task_id=item["job_id"], parameters=json.loads(item.pop("parameters_json")), progress=100 if item["status"] in {"succeeded", "no_change", "partial"} else min(99, item["accepted_batches"]), stage=item["phase"], stage_progress=None)
    item["logs"] = [{**dict(e), "details": json.loads(e["details_json"])} for e in events]
    for event in item["logs"]:
        event.pop("details_json", None)
        event["time"] = event["occurred_at_ms"]
    item["duration_ms"] = (item["finished_at_ms"] or now_ms()) - item["created_at_ms"]
    item['message'] = item['logs'][-1]['message'] if item['logs'] else item['error_summary']
    for event in reversed(item['logs']):
        if 'actual_total_batches' in event['details']:
            for key in ('actual_completed_batches','actual_total_batches'):
                item[key]=event['details'].get(key)
            break
    return item


def list_tasks(limit=50,summary=False):
    if summary:
        with connect() as connection:
            connection.execute('BEGIN')
            saved=connection.execute('SELECT * FROM job ORDER BY created_at_ms DESC,rowid DESC LIMIT ?', (max(1,min(limit,200)),)).fetchall()
            total=connection.execute('SELECT count(*) FROM job').fetchone()[0]
            observed=now_ms()
            return {'items':[_summary_task_view(connection,row,observed) for row in saved],'total':total,'summary':True}
    with connect() as connection:
        ids = [r[0] for r in connection.execute("SELECT job_id FROM job ORDER BY created_at_ms DESC LIMIT ?", (max(1, min(limit, 200)),))]
        total = connection.execute("SELECT count(*) FROM job").fetchone()[0]
    return {"items": [task_view(i) for i in ids], "total": total}


def _activity_view(connection, row, *, expand_batch=True, parameters=None):
    """Small display-only receipt; never serialize credentials or full event history."""
    item = dict(row)
    parameters = json.loads(item['parameters_json']) if parameters is None else parameters
    event = connection.execute(
        'SELECT stage,message,details_json FROM job_event WHERE job_id=? ORDER BY seq DESC LIMIT 1',
        (item['job_id'],)).fetchone()
    details = json.loads(event['details_json']) if event else {}
    target = 'sector' if parameters.get('target_type') == 'sector' else 'stock'
    strategy_id = parameters.get('strategy_id')
    strategy_name = None
    if strategy_id:
        table = 'sector_strategy' if target == 'sector' else 'strategy'
        strategy = connection.execute('SELECT name FROM '+table+' WHERE strategy_id=?', (strategy_id,)).fetchone()
        strategy_name = strategy['name'] if strategy else None
    operation = parameters.get('operation')
    title = {
        'initialise': '首次初始化', 'increment': '盘后增量', 'research': '个股研究',
        'backtest': '策略回测', 'topic_init': '本地题材导入', 'topic_refresh': '题材库同步',
        'tracking_review': '板块跟踪核对' if target == 'sector' else '个股跟踪核对',
        'selection': '板块策略筛选' if target == 'sector' else '个股策略选股',
        'source_check': {'sector_chart': '单板块历史K线', 'sector_charts': '板块K线尾部增量' if parameters.get('mode') == 'increment' else '全库板块历史K线',
                         'market_supplement': '盘面补充', 'dragon_tiger':'龙虎榜补齐', 'local_storage_conversion':'本地存储升级', 'local_coverage_check':'本地数据核验','line_alert_check':'个股划线提醒核验','listing_status_check':'上市身份核验','quote_gap_source_check':'报价缺口来源核验', 'strategy_batch': '板块策略批量筛选' if target == 'sector' else '个股策略批量选股',
                         'strategy_batch_tracking': '板块命中自动入池' if target == 'sector' else '个股命中自动入池'}.get(operation, '数据来源任务'),
    }.get(item['kind'], '后台任务')
    # accepted_batches is a weighted percentage only after progress() reports.
    # A freshly started job with the schema default 0 has no measured progress.
    reported = item['accepted_batches'] > 0 or any(key in details for key in
        ('weighted_stage_progress', 'actual_total_batches', 'total_count'))
    progress = 100 if item['status'] in {'succeeded', 'no_change', 'partial'} else (
        min(99, item['accepted_batches']) if reported or item['status'] == 'queued' else None)
    receipt = {
        'job_id': item['job_id'], 'task_id': item['job_id'], 'request_key': item['request_key'],
        'kind': item['kind'], 'operation': operation, 'title': title, 'target_type': target,
        'strategy_id': strategy_id, 'strategy_name': strategy_name,
        'status': item['status'], 'stage': item['phase'], 'progress': progress,
        'message': item['error_summary'] if item['status'] == 'failed' else (event['message'] if event else None),
        'actual_completed_batches': details.get('actual_completed_batches'),
        'actual_total_batches': details.get('actual_total_batches'),
        'target_date': item['target_date'], 'created_at_ms': item['created_at_ms'],
        'finished_at_ms': item['finished_at_ms'],
    }
    if expand_batch and operation == 'strategy_batch' and item['kind'] == 'source_check':
        from .strategy_batches import _view as batch_view
        batch = batch_view(connection, row)
        receipt.update(status=batch['status'], progress=batch['progress'],
            actual_completed_batches=batch['completed_steps'], actual_total_batches=batch['total_steps'])
        if batch['auto_add']:
            receipt['title'] = '板块策略筛选并跟踪' if target == 'sector' else '个股策略选股并跟踪'
        if batch['current_strategy']:
            current_item = batch['current_strategy']
            receipt.update(strategy_id=current_item['strategy_id'], strategy_name=current_item['name'],
                stage=current_item['stage'], message=f'已完成策略 {batch["completed_count"]}/{batch["total_count"]} · 步骤 {batch["completed_steps"]}/{batch["total_steps"]} · 正在运行：{current_item["name"]}')
    return receipt


_SUMMARY_PARAMETERS = ('mode','operation','target_type','strategy_id','window','data_chain_version',
                       'initialization_chain','increment_chain','parent_sync_job_id','previous_chain_job_id',
                       'topic_code','source_code','source_name','source_id','period','version','plan_version','auto_add')


def _summary_task_view(connection,row,observed):
    """A bounded display receipt; selected-task detail retains the full audit."""
    item=dict(row)
    parameters=json.loads(item['parameters_json'])
    receipt=_activity_view(connection,row,expand_batch=False,parameters=parameters)
    for key in ('message','strategy_name','title'):
        if isinstance(receipt.get(key),str):receipt[key]=receipt[key][:160]
    public={key:value[:200] if isinstance(value,str) else value
            for key,value in parameters.items() if key in _SUMMARY_PARAMETERS and
            (value is None or isinstance(value,(str,bool,int,float)))}
    instrument=parameters.get('instrument')
    if isinstance(instrument,dict):
        public['instrument']={key:value[:120] for key,value in instrument.items()
                              if key in ('name','topic_code','source_code','kind') and isinstance(value,str)}
    # One latest event per state is enough for taskChainState, preserving their
    # sequence. Large source SHA maps and progress payloads never enter history.
    events=connection.execute("""SELECT seq,occurred_at_ms,stage,message,details_json FROM job_event
        WHERE job_id=? AND seq IN (SELECT max(seq) FROM job_event WHERE job_id=?
          AND stage IN ('chain_authorized','followup_queued','chain_blocked') GROUP BY stage)
        ORDER BY seq LIMIT 3""",(item['job_id'],item['job_id'])).fetchall()
    logs=[]
    for event in events:
        details=json.loads(event['details_json'])
        small={key:value[:500] for key,value in details.items()
               if key in ('next_task_id','reason') and isinstance(value,str)}
        logs.append({'seq':event['seq'],'time':event['occurred_at_ms'],'stage':event['stage'],
                     'message':event['message'][:160],'details':small})
    receipt.update(parameters=public,logs=logs,duration_ms=max(0,(item['finished_at_ms'] or observed)-item['created_at_ms']),
                   row_count=None,accepted_rows=None,summary=True)
    return receipt


def task_activity():
    """Active work is never hidden behind a history page; recent terminals are bounded."""
    with connect() as connection:
        active_filter = "status IN ('running','queued') AND NOT (status='queued' AND coalesce(json_extract(parameters_json,'$.operation'),'') IN ('strategy_batch_item','strategy_batch_tracking'))"
        active = connection.execute('SELECT * FROM job WHERE '+active_filter+" ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,created_at_ms LIMIT 200").fetchall()
        recent = connection.execute("SELECT * FROM job WHERE status NOT IN ('running','queued') ORDER BY coalesce(finished_at_ms,created_at_ms) DESC,created_at_ms DESC,rowid DESC LIMIT 5").fetchall()
        return {'items': [_activity_view(connection, row) for row in active+recent],
                'active_count': connection.execute('SELECT count(*) FROM job WHERE '+active_filter).fetchone()[0],
                'observed_at_ms': now_ms()}


def active_task_conflict(connection, fallback='已有任务正在运行，请查看任务进度后重试'):
    row = connection.execute("SELECT * FROM job WHERE status='running' LIMIT 1").fetchone()
    if row:
        active = _activity_view(connection, row)
        label = active['title'] + (' · '+active['strategy_name'] if active['strategy_name'] else '')
        raise HTTPException(409, {'message': f'正在运行：{label}。请查看上方任务进度，完成后再运行。', 'active_task': active})
    raise HTTPException(409, fallback)


def _increment_frozen_target(identifier, declared_target):
    """Read a small checkpoint outside the SQLite write transaction."""
    path=raw_dir()/'sync'/identifier/'checkpoint.json'
    if not path.is_file():return None
    try:
        plan=json.loads(path.read_text()).get('plan')
        if plan is None:return None
        if not isinstance(plan,dict) or plan.get('kind')!='increment':raise ValueError()
        target=plan.get('target')
        from datetime import date
        if not isinstance(target,str) or date.fromisoformat(target).isoformat()!=target:raise ValueError()
        if declared_target is not None and target!=declared_target:raise ValueError()
        return target
    except (OSError,ValueError,TypeError,AttributeError):
        raise HTTPException(409,'增量任务检查点缺失有效冻结日期或与任务冲突，请核对后在18:00后新建重试')


def change_task(identifier: str, action: str):
    with connect() as batch_check:
        batch = batch_check.execute("SELECT kind,parameters_json FROM job WHERE job_id=?", (identifier,)).fetchone()
    if batch and json.loads(batch['parameters_json']).get('operation')=='local_storage_conversion' and action in ('pause','resume','retry'):
        raise HTTPException(422,'离线维护记录，请停止服务后使用存储转换工具，不在同步中心重跑')
    if batch and batch['kind']=='cleanup' and json.loads(batch['parameters_json']).get('operation')=='workspace_purge':
        if action=='retry':
            from .workspace_purge import retry
            return retry(json.loads(batch['parameters_json'])['purge_id'])
        raise HTTPException(422,'永久删除授权不能暂停、取消或复制；失败后请重试原清理任务')
    if batch and batch['kind']=='source_check' and json.loads(batch['parameters_json']).get('operation')=='strategy_batch':
        from .strategy_batches import change_batch
        return change_batch(identifier, action)
    if batch and json.loads(batch['parameters_json']).get('operation') in ('strategy_batch_item','strategy_batch_tracking'):
        raise HTTPException(422, '请在策略页控制整个批次；重试需新建批次，不能单独复制或恢复内部步骤')
    frozen_target=None
    if action in ('retry','resume'):
        with connect() as preparation:
            prepared=preparation.execute('SELECT kind,status,target_date,parameters_json FROM job WHERE job_id=?',(identifier,)).fetchone()
        if prepared and prepared['kind']=='source_check':
            prepared_parameters=json.loads(prepared['parameters_json'])
            if (prepared_parameters.get('operation')=='sector_charts'
                    and prepared_parameters.get('mode','initial')=='initial'):
                from .sector_chart_batch_jobs import require_initialization_allowed
                require_initialization_allowed()
        if prepared and prepared['kind']=='increment' and ((action=='resume' and prepared['status']=='paused') or (action=='retry' and prepared['status'] in TERMINAL)):
            frozen_target=_increment_frozen_target(identifier,prepared['target_date'])
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM job WHERE job_id=?", (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        from .increment_access import business_http_request
        if business_http_request.get() and action in ('resume','retry'):
            from .sync_progress import require_unlocked
            require_unlocked(connection)
        if row['kind'] in ('selection','tracking_review') and action in ('pause','resume','retry'):
            raise HTTPException(422,'本地即时计算支持取消；请在策略或跟踪页面重新运行')
        if action == "cancel" and row["status"] not in TERMINAL:
            from .sync_progress import mark_stop
            mark_stop(connection, identifier, row['status'] == 'running')
            connection.execute("UPDATE job SET status='cancelled',finished_at_ms=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (now_ms(), identifier))
            append_event(connection, identifier, "cancelled", "用户取消任务，已发布数据保持有效")
        elif action == "pause" and row["status"] in {"queued", "running"}:
            from .sync_progress import mark_stop
            mark_stop(connection, identifier, row['status'] == 'running')
            connection.execute("UPDATE job SET status='paused',lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (identifier,))
            append_event(connection, identifier, "paused", "用户暂停任务，需手动继续")
        elif action == "resume" and row["status"] == "paused":
            from .increment_access import business_http_request
            if business_http_request.get():
                from .sync_progress import root_for_task, bind_session
                root = root_for_task(connection, identifier)
                if root is not None:
                    bind_session(connection, root, attempt_task_id=identifier)
            _guard_initialization(connection, row['kind'])
            if row['kind']=='increment':
                from .sync_clock import require_resume
                try:require_resume(frozen_target)
                except ValueError as error:raise HTTPException(409,str(error)) from error
            connection.execute("UPDATE job SET status='queued',phase='queued' WHERE job_id=?", (identifier,))
            append_event(connection, identifier, "queued", "用户继续原任务，已确认批次将幂等复用")
        elif action == "retry" and row["status"] in TERMINAL:
            if row['kind'] not in ('initialise','increment','research','topic_init','topic_refresh','source_check'):
                raise HTTPException(422,'请回到对应功能页重新运行，以冻结新的策略与输入；同步中心仅重试同步和研究任务')
            if row['kind']=='increment':
                from .sync_clock import require_new_increment
                try:require_new_increment()
                except ValueError as error:raise HTTPException(409,str(error)) from error
            parameters = json.loads(row["parameters_json"])
            target, kind = frozen_target or row["target_date"], row["kind"]
        else:
            return task_view(identifier)
    if action == "retry":
        from .increment_access import business_http_request
        if business_http_request.get() and kind == 'increment':
            from .sync_progress import LOCK_VERSION
            parameters = {**parameters, 'increment_session_version': LOCK_VERSION}
        return create_task(kind, parameters, target, retry_of=identifier)
    return task_view(identifier)


def claim_next(owner: str):
    """Only recover existing authorized running work after its lease expires."""
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        running = connection.execute("SELECT * FROM job WHERE status='running' LIMIT 1").fetchone()
        if running:
            if running["lease_expires_at_ms"] is not None and running["lease_expires_at_ms"] < now_ms():
                recoverable_selection = running['kind']=='selection' and json.loads(running['parameters_json']).get('operation')=='strategy_batch_item'
                if running['kind'] in ('selection','tracking_review') and not recoverable_selection:
                    connection.execute("UPDATE job SET status='failed',phase='interrupted',finished_at_ms=?,error_summary='API计算进程中断，请手动重跑',lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?",(now_ms(),running['job_id']))
                    append_event(connection,running['job_id'],'interrupted','API计算租约已过期，请在原模块手动重跑','error')
                else:
                    if recoverable_selection or (running['kind']=='source_check' and json.loads(running['parameters_json']).get('operation') in ('strategy_batch','strategy_batch_tracking','line_alert_check','listing_status_check','quote_gap_source_check')):
                        owner = owner+':'+new_id('attempt')
                    if recoverable_selection:
                        connection.execute("UPDATE job SET accepted_batches=0,phase='recovery' WHERE job_id=?", (running['job_id'],))
                    connection.execute("UPDATE job SET lease_owner=?,lease_expires_at_ms=?,recovery_count=recovery_count+1 WHERE job_id=?", (owner, now_ms() + 60_000, running["job_id"]))
                    append_event(connection, running["job_id"], "recovery", "恢复已授权的中断任务，复用原任务与幂等批次")
                    return dict(connection.execute("SELECT * FROM job WHERE job_id=?", (running["job_id"],)).fetchone())
            else:return None
        queued = _next_queued(connection)
        # A legacy queue may predate the creation guard. Do not let a second
        # initialization replace an already published main dataset, and do not
        # block unrelated queued work behind that obsolete request.
        while queued and queued['kind'] == 'initialise' and initialization_state(connection)['main_published']:
            reason = initialization_state(connection)['reason']
            connection.execute("UPDATE job SET status='failed',phase='initialization_blocked',finished_at_ms=?,error_summary=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (now_ms(), reason, queued['job_id']))
            append_event(connection, queued['job_id'], 'initialization_blocked', reason, 'warning')
            queued = _next_queued(connection)
        if not queued:
            return None
        if json.loads(queued['parameters_json']).get('operation') in ('strategy_batch','strategy_batch_item','strategy_batch_tracking','line_alert_check','listing_status_check','quote_gap_source_check'):
            owner = owner+':'+new_id('attempt')
        connection.execute("UPDATE job SET status='running',phase='starting',lease_owner=?,lease_expires_at_ms=? WHERE job_id=?", (owner, now_ms() + 60_000, queued["job_id"]))
        append_event(connection, queued["job_id"], "starting", "工作进程已领取任务")
        return dict(connection.execute("SELECT * FROM job WHERE job_id=?", (queued["job_id"],)).fetchone())


def _next_queued(connection):
    # Batch parents summarize only after their persisted children are terminal.
    # Explicit rowid ordering keeps siblings ordered even within one millisecond.
    return connection.execute("""SELECT j.* FROM job j WHERE j.status='queued'
        AND NOT (j.kind='source_check' AND json_extract(j.parameters_json,'$.operation')='strategy_batch'
            AND EXISTS (SELECT 1 FROM json_each(j.parameters_json,'$.items') i
                LEFT JOIN job child ON child.job_id=json_extract(i.value,'$.job_id')
                LEFT JOIN job tracked ON tracked.job_id=json_extract(i.value,'$.tracking_job_id')
                WHERE child.status IN ('queued','running','paused') OR tracked.status IN ('queued','running','paused')))
        AND (coalesce(json_extract(j.parameters_json,'$.operation'),'') NOT IN ('strategy_batch_item','strategy_batch_tracking')
            OR EXISTS (SELECT 1 FROM job parent WHERE parent.job_id=json_extract(j.parameters_json,'$.batch_job_id')
                AND parent.status IN ('queued','running')
                AND NOT EXISTS (SELECT 1 FROM json_each(parent.parameters_json,'$.items') i
                    LEFT JOIN job prior ON prior.job_id=json_extract(i.value,'$.job_id')
                    LEFT JOIN job tracked ON tracked.job_id=json_extract(i.value,'$.tracking_job_id')
                    WHERE json_extract(i.value,'$.sequence') < json_extract(j.parameters_json,'$.sequence')
                        AND (prior.status IS NULL OR prior.status NOT IN ('succeeded','no_change','partial','failed','cancelled')
                            OR json_extract(i.value,'$.tracking_job_id') IS NOT NULL AND (tracked.status IS NULL OR tracked.status NOT IN ('succeeded','no_change','partial','failed','cancelled'))))))
        AND (coalesce(json_extract(j.parameters_json,'$.operation'),'')!='strategy_batch_tracking'
            OR EXISTS (SELECT 1 FROM job selected WHERE selected.job_id=json_extract(j.parameters_json,'$.selection_job_id')
                AND selected.status IN ('succeeded','no_change','partial','failed','cancelled')))
        ORDER BY j.created_at_ms,j.rowid LIMIT 1""").fetchone()


def progress(identifier: str, stage: str, percent: int, message=None, details=None, *, lease_owner=None):
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status,accepted_batches,lease_owner FROM job WHERE job_id=?", (identifier,)).fetchone()
        if lease_owner is not None and (not row or row['status']!='running' or row['lease_owner']!=lease_owner):
            raise TaskCancelled()
        if not row or row["status"] == "cancelled":
            raise TaskCancelled()
        if row["status"] == "paused":
            raise TaskPaused()
        percent = max(row["accepted_batches"], min(99, max(0, int(percent))))
        connection.execute("UPDATE job SET phase=?,accepted_batches=?,lease_expires_at_ms=? WHERE job_id=?", (stage, percent, now_ms() + 60_000, identifier))
        append_event(connection, identifier, stage, message or stage, details=details or {"weighted_stage_progress": percent})


def finish(identifier: str, status="succeeded", message="任务完成", error=None, *, lease_owner=None):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT status,lease_owner FROM job WHERE job_id=?", (identifier,)).fetchone()
        if lease_owner is not None and (not row or row['status']!='running' or row['lease_owner']!=lease_owner):
            return
        if not row or row["status"] in TERMINAL or row['status']=='paused':
            return
        connection.execute("UPDATE job SET status=?,phase=?,accepted_batches=CASE WHEN ? IN ('succeeded','no_change','partial') THEN 100 ELSE accepted_batches END,finished_at_ms=?,error_summary=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (status, status, status, now_ms(), error, identifier))
        append_event(connection, identifier, status, message, "error" if status == "failed" else "warning" if status == "partial" else "info")
