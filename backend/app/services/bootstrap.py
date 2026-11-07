"""Explicit imports and publication of separately covered data modules."""
from __future__ import annotations

import json
from pathlib import Path

from app.core import (business_date, connect, data_dir, get_market, now_ms,
                      publish_snapshot, published_dir)
from app.services import task_service as tasks
from app.analytics.market_storage import load_reference


def register_market(status: dict, job_id: str, callback=None, *, completion=None):
    start, end = status["data_begin"], status["data_end"]
    previous_day = business_date()
    if previous_day and previous_day > end:
        raise ValueError("导入数据早于当前业务日，不能覆盖最新数据；请使用独立恢复目录")
    generation = status["snapshot_id"]
    physical = {}; shared = {}
    for file in status["files"]:
        path = Path(file["path"])
        if path.parts and path.parts[0] == "published":
            path = Path(*path.parts[1:])
        physical[file["role"]] = {"object_key": str(path), "sha256": file["sha256"], "byte_count": file["bytes"], "role": file["role"]}
    for role in ('raw-daily.parquet','daily.parquet'):
        prefix=role.removesuffix('.parquet')+'-partition/'
        shared[role]=[value for key,value in physical.items() if key.startswith(prefix)]
    reference_parts=[value for key,value in physical.items() if key.startswith('reference-part/')]
    shared['reference.json']=reference_parts
    reference = load_reference(published_dir()/generation/'reference.json')
    calendar = reference["calendar"]
    actual_end = max(calendar)
    if end != actual_end:
        raise ValueError("行情截止日与已完成交易日历不一致")
    action_rows = None
    if 'corporate-actions.parquet' in physical:
        import duckdb
        connection=duckdb.connect(':memory:')
        try:
            connection.execute("SET memory_limit='64MB'")
            connection.execute('SET threads=1')
            action_rows=connection.execute("SELECT count(*) FROM read_parquet(?) WHERE strftime(to_timestamp(ex_date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai','%Y-%m-%d') BETWEEN ? AND ?",[str(published_dir()/generation/'corporate-actions.parquet'),start,end]).fetchone()[0]
        finally: connection.close()
    declarations = [
        ("calendar", "reference.json", len(calendar), min(calendar), end, "partial" if status.get('coverage',{}).get('calendar_provenance',{}).get('basis')=='source_derived_cross_checked' else "ready", 0),
        ("security_identity", "reference.json", len(reference.get("master", {})), start, end, "partial", 0),
        ("daily_raw", "raw-daily.parquet", status["rows"], start, end, "unknown" if status.get('coverage',{}).get('identity_unknown_count') else "partial", int(status.get("unexplained_missing") or status.get('coverage',{}).get('confirmed_missing_lower_bound') or 0)),
        ("corporate_actions", "corporate-actions.parquet", action_rows, start, end, "partial", 0),
        ("analysis_bars", "daily.parquet", status["rows"], start, end, "partial", 0),
        ("indicators", "daily.parquet", status["rows"], start, end, "partial", 0),
        ("topics", "reference.json", sum(len(t.get("instrument_ids", [])) for t in reference.get("topics", [])), end, end, "partial", 0),
        ("market_review", "reference.json", sum(len(reference.get(k, [])) for k in ("limitup", "limitdown", "breakpool", "abnormal")), end, end, "partial", 0),
    ]
    snapshots = {}
    for index, (module, file, count, begin, finish, quality, missing) in enumerate(declarations):
        if file not in physical:
            continue
        if callback:
            callback("登记" + module, 82 + index * 2)
        artifact = {**physical[file], "row_count": count}
        lineage = {"market_snapshot_id": generation, "source": status.get("source"), "collected_at": status.get("collected_at"), "warnings": status.get("warnings", []), "algorithm": status.get("algorithm_version"), "input_snapshots": dict(snapshots)}
        if module in ('daily_raw','security_identity','calendar'):lineage['coverage']=status.get('coverage',{})
        if module in ("analysis_bars", "indicators"):
            lineage["derived_view"] = {"calculation": "统一后端日周聚合和指标；基于此不可变输入按需计算", "algorithm": status.get("algorithm_version"), "materialized_indicator_rows": False}
        identifier = module + "-" + generation.removeprefix("market-")
        snapshots[module] = publish_snapshot(module=module, job_id=job_id, start=begin, end=finish, artifacts=[artifact,*shared.get(file,[])], quality=quality, missing=missing, lineage=lineage, price_basis="none" if module in ("daily_raw", "analysis_bars", "indicators") else "not_applicable", algorithm=status.get("algorithm_version", "raw-v1"), snapshot_id=identifier, activate=False)
    receipt = completion or {'status': 'partial', 'message': '行情模块已成组发布；辅助字段缺口保留待核验', 'details': {}}
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        state = connection.execute("SELECT status FROM job WHERE job_id=?", (job_id,)).fetchone()
        if not state or state[0] in ('cancelled','paused','failed'):
            raise ValueError('任务已停止，保留原发布水位')
        for module, snapshot in snapshots.items():
            connection.execute("INSERT INTO published_head(module,scope_key,snapshot_id,changed_at_ms) VALUES(?,?,?,?) ON CONFLICT(module,scope_key) DO UPDATE SET snapshot_id=excluded.snapshot_id,changed_at_ms=excluded.changed_at_ms", (module,'CN_A',snapshot,now_ms()))
        connection.execute("INSERT INTO business_clock(singleton,business_date,calendar_snapshot_id,changed_at_ms) VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET business_date=excluded.business_date,calendar_snapshot_id=excluded.calendar_snapshot_id,changed_at_ms=excluded.changed_at_ms", (end, snapshots["calendar"], now_ms()))
        connection.execute("UPDATE job SET status=?,phase='published',accepted_batches=100,finished_at_ms=?,lease_owner=NULL,lease_expires_at_ms=NULL WHERE job_id=?",(receipt['status'],now_ms(),job_id))
        tasks.append_event(connection,job_id,'published',receipt['message'],details=receipt['details'])
    from app.domain.workspace import seed_workspace
    try:
        seed_workspace()
    except Exception as error:
        with connect() as connection:
            tasks.append_event(connection,job_id,'workspace_seed','行情发布成功；题材与默认策略初始化可在相应页面重试','warning',{'error_type':type(error).__name__})
    return {"snapshots": snapshots, "business_date": end, "generation": generation}


def import_validated_audit(audit_dir: str | Path):
    """Developer/user CLI operation, never run from application import/startup."""
    from app.analytics.market import MarketData
    task = tasks.create_task("initialise", {"source": "validated-import", "window": "near-one-year"})
    identifier = task["task_id"]
    with connect() as connection:
        connection.execute("UPDATE job SET status='running',phase='import',lease_owner='manual-import',lease_expires_at_ms=? WHERE job_id=?", (now_ms() + 60_000, identifier))
    callback = lambda stage, percent: tasks.progress(identifier, stage, min(80, int(percent * .8)))
    try:
        status = MarketData(data_dir()).import_audit(audit_dir, callback)
        registration = register_market(status, identifier, lambda stage, percent: tasks.progress(identifier, stage, percent))
        tasks.finish(identifier, "partial", "真实年度行情已导入并发布；缺口与未覆盖字段保留待核验")
        return {"task_id": identifier, "status": status, **registration}
    except Exception as error:
        tasks.finish(identifier, "failed", "初始化未完成，旧发布数据保持有效", type(error).__name__)
        raise
