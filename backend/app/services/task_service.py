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


def create_task(kind: str, parameters: dict, target_date: str | None = None, request_key: str | None = None, retry_of: str | None = None) -> dict:
    request_key = request_key or new_id("request")
    logical_key = digest({"kind": kind, "target_date": target_date, "parameters": parameters})
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        old = connection.execute("SELECT job_id,kind,target_date,parameters_hash FROM job WHERE request_key=?", (request_key,)).fetchone()
        if old:
            if old['kind']!=kind or old['target_date']!=target_date or old['parameters_hash']!=digest(parameters):
                raise HTTPException(409,'此请求标识已用于不同任务参数，请使用新的请求标识')
            identifier = old[0]
        else:
            _guard_initialization(connection, kind)
            if kind == 'initialise':
                initial = connection.execute("SELECT job_id,logical_key FROM job WHERE kind='initialise' AND status IN ('queued','running','paused') LIMIT 1").fetchone()
                if initial and initial['logical_key'] != logical_key:
                    raise HTTPException(409, '已有首次初始化任务，请查看、继续或取消原任务后再操作')
            active = connection.execute("SELECT job_id FROM job WHERE logical_key=? AND status IN ('queued','running','paused')", (logical_key,)).fetchone()
            if active:
                return task_view(active[0])
            identifier = new_id("job")
            connection.execute("INSERT INTO job(job_id,request_key,logical_key,kind,target_date,parameters_json,parameters_hash,status,planned_batches,retry_of_job_id,created_at_ms) VALUES(?,?,?,?,?,?,?,'queued',100,?,?)", (identifier, request_key, logical_key, kind, target_date, dumps(parameters), digest(parameters), retry_of, now_ms()))
            append_event(connection, identifier, "queued", "任务已提交，等待工作进程", details={"kind": kind, "target_date": target_date})
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


def list_tasks(limit=50):
    with connect() as connection:
        ids = [r[0] for r in connection.execute("SELECT job_id FROM job ORDER BY created_at_ms DESC LIMIT ?", (max(1, min(limit, 200)),))]
        total = connection.execute("SELECT count(*) FROM job").fetchone()[0]
    return {"items": [task_view(i) for i in ids], "total": total}


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
        if row['kind'] in ('selection','tracking_review') and action in ('pause','resume','retry'):
            raise HTTPException(422,'本地即时计算支持取消；请在策略或跟踪页面重新运行')
        if action == "cancel" and row["status"] not in TERMINAL:
            connection.execute("UPDATE job SET status='cancelled',finished_at_ms=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (now_ms(), identifier))
            append_event(connection, identifier, "cancelled", "用户取消任务，已发布数据保持有效")
        elif action == "pause" and row["status"] in {"queued", "running"}:
            connection.execute("UPDATE job SET status='paused',lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (identifier,))
            append_event(connection, identifier, "paused", "用户暂停任务，需手动继续")
        elif action == "resume" and row["status"] == "paused":
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
        return create_task(kind, parameters, target, retry_of=identifier)
    return task_view(identifier)


def claim_next(owner: str):
    """Only recover existing authorized running work after its lease expires."""
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        running = connection.execute("SELECT * FROM job WHERE status='running' LIMIT 1").fetchone()
        if running:
            if running["lease_expires_at_ms"] is not None and running["lease_expires_at_ms"] < now_ms():
                if running['kind'] in ('selection','tracking_review'):
                    connection.execute("UPDATE job SET status='failed',phase='interrupted',finished_at_ms=?,error_summary='API计算进程中断，请手动重跑',lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?",(now_ms(),running['job_id']))
                    append_event(connection,running['job_id'],'interrupted','API计算租约已过期，请在原模块手动重跑','error')
                else:
                    connection.execute("UPDATE job SET lease_owner=?,lease_expires_at_ms=?,recovery_count=recovery_count+1 WHERE job_id=?", (owner, now_ms() + 60_000, running["job_id"]))
                    append_event(connection, running["job_id"], "recovery", "恢复已授权的中断任务，复用原任务与幂等批次")
                    return dict(connection.execute("SELECT * FROM job WHERE job_id=?", (running["job_id"],)).fetchone())
            else:return None
        queued = connection.execute("SELECT * FROM job WHERE status='queued' ORDER BY created_at_ms LIMIT 1").fetchone()
        # A legacy queue may predate the creation guard. Do not let a second
        # initialization replace an already published main dataset, and do not
        # block unrelated queued work behind that obsolete request.
        while queued and queued['kind'] == 'initialise' and initialization_state(connection)['main_published']:
            reason = initialization_state(connection)['reason']
            connection.execute("UPDATE job SET status='failed',phase='initialization_blocked',finished_at_ms=?,error_summary=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (now_ms(), reason, queued['job_id']))
            append_event(connection, queued['job_id'], 'initialization_blocked', reason, 'warning')
            queued = connection.execute("SELECT * FROM job WHERE status='queued' ORDER BY created_at_ms LIMIT 1").fetchone()
        if not queued:
            return None
        connection.execute("UPDATE job SET status='running',phase='starting',lease_owner=?,lease_expires_at_ms=? WHERE job_id=?", (owner, now_ms() + 60_000, queued["job_id"]))
        append_event(connection, queued["job_id"], "starting", "工作进程已领取任务")
        return dict(connection.execute("SELECT * FROM job WHERE job_id=?", (queued["job_id"],)).fetchone())


def progress(identifier: str, stage: str, percent: int, message=None, details=None):
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT status,accepted_batches FROM job WHERE job_id=?", (identifier,)).fetchone()
        if not row or row["status"] == "cancelled":
            raise TaskCancelled()
        if row["status"] == "paused":
            raise TaskPaused()
        percent = max(row["accepted_batches"], min(99, max(0, int(percent))))
        connection.execute("UPDATE job SET phase=?,accepted_batches=?,lease_expires_at_ms=? WHERE job_id=?", (stage, percent, now_ms() + 60_000, identifier))
        append_event(connection, identifier, stage, message or stage, details=details or {"weighted_stage_progress": percent})


def finish(identifier: str, status="succeeded", message="任务完成", error=None):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT status FROM job WHERE job_id=?", (identifier,)).fetchone()
        if not row or row["status"] in TERMINAL or row['status']=='paused':
            return
        connection.execute("UPDATE job SET status=?,phase=?,accepted_batches=CASE WHEN ? IN ('succeeded','no_change','partial') THEN 100 ELSE accepted_batches END,finished_at_ms=?,error_summary=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?", (status, status, status, now_ms(), error, identifier))
        append_event(connection, identifier, status, message, "error" if status == "failed" else "warning" if status == "partial" else "info")
