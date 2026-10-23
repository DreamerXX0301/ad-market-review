"""Confirmed, worker-only physical cleanup of recycled research objects.

Market data, independent watchlists/annotations and immutable publication/task
catalogues are retained. A watchlisted symbol in *any* strategy tracking round
protects that whole strategy, irrespective of how the watchlist item was added.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import PurePosixPath
import stat
from fastapi import HTTPException
from app.core import connect, digest, dumps, new_id, now_ms, published_dir
from . import task_service as tasks
from .workspace_deletion import backtest_block_reason, strategy_block_reason

RETENTION = [
    '独立自选、划线和划线触发事件、真实全市场行情保留。',
    '其他对象共享的报告文件、快照和保留依据保留。',
    '任务及发布目录元数据、永久删除审计和已有备份保留；不会自动压缩 SQLite。',
    '文件大小仅指可清理的专属报告文件，实际移除大小以工作进程结果为准。未在发布目录登记的文件保留。',
]


def active_purge(c, kind, identifier):
    row = c.execute("SELECT purge_id,status,business_deleted FROM workspace_purge WHERE target_kind=? AND target_id=? AND superseded_by IS NULL AND status IN ('queued','cleaning','failed') ORDER BY created_at_ms DESC,rowid DESC LIMIT 1", (kind, identifier)).fetchone()
    return dict(row) if row else None


def require_not_purging(c, kind, identifier):
    item = active_purge(c, kind, identifier)
    if item and item['status'] in ('queued', 'cleaning'):
        raise HTTPException(409, '永久清理任务已确认，等待清理完成后再操作')


def was_purged(c, kind, identifier):
    return c.execute('SELECT 1 FROM workspace_purge WHERE target_kind=? AND target_id=? AND business_deleted=1 LIMIT 1', (kind, identifier)).fetchone() is not None


def _rows(c, table, column, identifiers):
    return [dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE '+column+' IN (SELECT value FROM json_each(?)) ORDER BY rowid', (dumps(identifiers),))] if identifiers else []


def _contains_snapshot(c, table, column, sid, exclude_column=None, excluded=()):
    query = 'SELECT 1 FROM '+table+' t,json_tree(t.'+column+") j WHERE j.type='text' AND j.value=?"
    values = [sid]
    if exclude_column:
        query += ' AND t.'+exclude_column+' NOT IN (SELECT value FROM json_each(?))'
        values.append(dumps(excluded))
    return c.execute(query+' LIMIT 1', values).fetchone() is not None


def _shared_snapshot(c, sid, owned, pin_ids):
    """Reference checks cover FK anchors and the persisted JSON dependency contracts."""
    for row in c.execute('SELECT module,scope_key FROM published_head WHERE snapshot_id=?', (sid,)):
        if row['module'] != 'backtest_result' or row['scope_key'] not in owned['backtest']:
            return True
    if c.execute('SELECT 1 FROM retention_pin WHERE snapshot_id=? AND pin_id NOT IN (SELECT value FROM json_each(?)) LIMIT 1', (sid, dumps(pin_ids))).fetchone():
        return True
    contracts = [
        ('backtest_run','result_snapshot_id','backtest_id',owned['backtest']),
        ('selection_run','primary_snapshot_id','run_id',owned['selection']),
        ('tracking_round','entry_snapshot_id','round_id',owned['round']),
        ('tracking_event','data_snapshot_id','round_id',owned['round']),
        ('dataset_snapshot','parent_snapshot_id','snapshot_id',()),
        ('listing_status_confirmation','primary_snapshot_id',None,()),
        ('listing_status_summary','primary_snapshot_id',None,()),
    ]
    for table, column, key, excluded in contracts:
        sql = 'SELECT 1 FROM '+table+' WHERE '+column+'=?'
        params = [sid]
        if key:
            sql += ' AND '+key+' NOT IN (SELECT value FROM json_each(?))'; params.append(dumps(excluded))
        if c.execute(sql+' LIMIT 1', params).fetchone(): return True
    for table, column, key, excluded in [
        ('backtest_run','input_snapshots_json','backtest_id',owned['backtest']),
        ('selection_run','input_snapshots_json','run_id',owned['selection']),
        ('research_report','snapshot_refs_json',None,()),
        ('watchlist_item','event_basis_json',None,()),
        ('watchlist_item','watchlist_entry_basis_json',None,()),
        ('tracking_event','evidence_json','round_id',owned['round']),
        ('tracking_round','entry_basis_json','round_id',owned['round']),
        ('line_alert_event','event_json',None,()),
        ('sector_selection_run','cache_refs_json',None,()),
        ('sector_tracking_round','entry_cache_refs_json',None,()),
        ('sector_tracking_round','entry_basis_json',None,()),
        ('dataset_snapshot','lineage_json','snapshot_id',(sid,)),
        ('job','parameters_json','job_id',owned['job']),
    ]:
        if _contains_snapshot(c, table, column, sid, key, excluded): return True
    return False


def _file_info(key, sha, byte_count, *, snapshot_id, backtest_id, manifest=False):
    """A preview uses lstat only; worker verifies content outside SQLite locks."""
    parts = PurePosixPath(key).parts
    expected = ('catalog', snapshot_id, 'manifest.json') if manifest else ('backtests', backtest_id)
    safe = (not PurePosixPath(key).is_absolute() and '..' not in parts and
            ((parts == expected) if manifest else (len(parts) == 3 and parts[:2] == expected)))
    if not safe: raise ValueError('报告目录含越界或无法确认归属的文件，未执行永久清理')
    path = published_dir()
    present = True
    for index, part in enumerate(parts):
        path = path / part
        try: info = path.lstat()
        except FileNotFoundError:
            present = False; break
        if stat.S_ISLNK(info.st_mode): raise ValueError('报告路径含符号链接，未执行永久清理')
        if index < len(parts)-1 and not stat.S_ISDIR(info.st_mode): raise ValueError('报告路径不是安全目录')
        if index == len(parts)-1 and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError('报告文件类型或硬链接无法安全核对')
    if present and byte_count is not None and info.st_size != byte_count:
        raise ValueError('报告文件大小与已发布目录不一致，请先核对文件')
    return {'snapshot_id': snapshot_id, 'object_key': key, 'sha256': sha,
            'byte_count': info.st_size if present and byte_count is None else (byte_count or 0),
            'present': present}


def _plan(c, kind, identifier, *, own_job=None):
    if kind not in ('strategy','backtest'): raise HTTPException(422, '不支持此清理类型')
    table, key = ('strategy','strategy_id') if kind == 'strategy' else ('backtest_run','backtest_id')
    target = c.execute('SELECT * FROM '+table+' WHERE '+key+'=?', (identifier,)).fetchone()
    if not target: raise HTTPException(404, '对象不存在或已永久删除')
    target = dict(target)
    marker = c.execute('SELECT * FROM '+('strategy_deletion' if kind == 'strategy' else 'backtest_deletion')+' WHERE '+key+'=?', (identifier,)).fetchone()
    reason = None if marker else '请先移入回收站，再永久删除'
    pending = c.execute("SELECT job_id FROM workspace_purge WHERE target_kind=? AND target_id=? AND status IN ('queued','cleaning')", (kind, identifier)).fetchone()
    if pending and pending['job_id'] != own_job: reason = '此对象已有永久清理任务，请查看任务进度'
    sid = identifier if kind == 'strategy' else target['strategy_id']
    backtests = _rows(c, 'backtest_run', 'strategy_id' if kind == 'strategy' else 'backtest_id', [identifier])
    runs = _rows(c,'selection_run','strategy_id',[sid]) if kind == 'strategy' else []
    rounds = _rows(c,'tracking_round','strategy_id',[sid]) if kind == 'strategy' else []
    owned = {'strategy': [sid] if kind == 'strategy' else [], 'backtest': sorted(r['backtest_id'] for r in backtests),
             'selection': sorted(r['run_id'] for r in runs), 'round': sorted(r['round_id'] for r in rounds),
             'job': sorted({r['job_id'] for r in backtests+runs})}
    protected = [dict(r) for r in c.execute('SELECT DISTINCT w.instrument_id,w.instrument_id name FROM watchlist_item w JOIN tracking_round r USING(instrument_id) WHERE r.strategy_id=? ORDER BY w.instrument_id', (sid,))] if kind == 'strategy' else []
    overlap=c.execute("SELECT 1 FROM workspace_purge WHERE status IN ('queued','cleaning') AND job_id IS NOT ? AND ((target_kind='strategy' AND target_id=?) OR (target_kind='backtest' AND target_id IN (SELECT value FROM json_each(?)))) LIMIT 1",(own_job,sid,dumps(owned['backtest']))).fetchone()
    if overlap:reason='所属策略或关联报告已有永久清理任务，请先等待原任务结束'
    if protected: reason = f'自选保护：该策略的 {len(protected)} 只历史或当前跟踪股票已加入自选，必须保留完整事件和基准，不能永久删除'
    if not reason:
        if kind == 'strategy': reason = strategy_block_reason(c, sid, ignored_job_id=own_job)
        else:
            job = dict(c.execute('SELECT status job_status,lease_owner,lease_expires_at_ms FROM job WHERE job_id=?', (target['job_id'],)).fetchone())
            reason = backtest_block_reason({**target, **job})
    events = _rows(c,'tracking_event','round_id',owned['round'])
    applications = _rows(c,'tracking_policy_application','round_id',owned['round'])
    versions = _rows(c,'strategy_version','strategy_id',owned['strategy'])
    policies = _rows(c,'tracking_policy_version','strategy_id',owned['strategy'])
    ignores = _rows(c,'tracking_ignore','strategy_id',owned['strategy'])
    candidates = {run_id: c.execute('SELECT count(*) FROM selection_candidate WHERE run_id=?',(run_id,)).fetchone()[0] for run_id in owned['selection']}
    pins = [dict(r) for r in c.execute("SELECT * FROM retention_pin WHERE (owner_kind='backtest' AND owner_id IN (SELECT value FROM json_each(?))) OR (owner_kind='selection' AND owner_id IN (SELECT value FROM json_each(?))) OR (owner_kind='tracking' AND owner_id IN (SELECT value FROM json_each(?))) OR (owner_kind='request' AND owner_id IN (SELECT value FROM json_each(?))) ORDER BY pin_id", (dumps(owned['backtest']),dumps(owned['selection']),dumps(owned['round']),dumps(owned['job'])))]
    files = []; snapshots = []; shared_count = 0
    for backtest in backtests:
        for snapshot in c.execute("SELECT s.*,g.state gc_state FROM dataset_snapshot s LEFT JOIN snapshot_gc g USING(snapshot_id) WHERE s.module='backtest_result' AND s.scope_key=? AND s.job_id=? ORDER BY s.snapshot_id",(backtest['backtest_id'],backtest['job_id'])):
            artifacts = [dict(a) for a in c.execute('SELECT * FROM snapshot_artifact WHERE snapshot_id=? ORDER BY object_key',(snapshot['snapshot_id'],))]
            shared = snapshot['state'] != 'published' or snapshot['gc_state'] not in (None,'available') or _shared_snapshot(c,snapshot['snapshot_id'],owned,[p['pin_id'] for p in pins])
            # Never unlink a path catalogued for a different snapshot, even if it
            # appears under this report's directory.
            if not shared:
                shared = any(c.execute('SELECT 1 FROM snapshot_artifact WHERE object_key=? AND snapshot_id<>? LIMIT 1',(a['object_key'],snapshot['snapshot_id'])).fetchone() for a in artifacts)
                shared = shared or c.execute('SELECT 1 FROM dataset_snapshot WHERE manifest_path=? AND snapshot_id<>? LIMIT 1',(snapshot['manifest_path'],snapshot['snapshot_id'])).fetchone() is not None
            if shared:
                shared_count += len(artifacts)+1; continue
            try:
                planned = [_file_info(a['object_key'],a['sha256'],a['byte_count'],snapshot_id=snapshot['snapshot_id'],backtest_id=backtest['backtest_id']) for a in artifacts]
                planned.append(_file_info(snapshot['manifest_path'],snapshot['manifest_sha256'],None,snapshot_id=snapshot['snapshot_id'],backtest_id=backtest['backtest_id'],manifest=True))
            except ValueError as error:
                reason = reason or str(error); continue
            snapshots.append(snapshot['snapshot_id']); files.extend(planned)
    counts = {'strategies': len(owned['strategy']), 'backtests': len(backtests), 'tracking_rounds': len(rounds),
              'tracking_events': len(events), 'selection_runs': len(runs), 'selection_candidates': sum(candidates.values()),
              'strategy_versions': len(versions), 'tracking_policies': len(policies), 'policy_applications': len(applications), 'tracking_ignores': len(ignores)}
    # Frozen rows make appends/edits invalidate confirmation, without copying
    # business evidence into the permanent audit. Candidate sets are sealed.
    settings=[dict(r) for r in c.execute('SELECT * FROM platform_setting WHERE key=?',('strategy-execution:'+sid,))] if kind=='strategy' else []
    counts['execution_settings']=len(settings)
    evidence_hash = digest([target,dict(marker) if marker else None,backtests,runs,rounds,events,applications,versions,policies,ignores,candidates,pins,settings])
    scope = {'target_kind':kind,'target_id':identifier,'owned':owned,'counts':counts,'evidence_hash':evidence_hash,
             'pin_ids':[p['pin_id'] for p in pins], 'snapshots':snapshots,'files':sorted(files,key=lambda f:f['object_key']),
             'shared_count':shared_count,'protected':protected}
    name = target['name'] if kind == 'strategy' else '回测 '+identifier
    return scope, name, reason


def preview(kind, identifier):
    with connect() as c:
        c.execute('BEGIN')
        scope, name, reason = _plan(c, kind, identifier)
    expires = now_ms()+600_000
    token = str(expires)+'.'+digest({'scope':scope,'expires':expires})
    return {'target_kind':kind,'target_id':identifier,'name':name,'can_purge':reason is None,'blocked_reason':reason,
            'confirmation_token':token if not reason else None,'confirmation_text':name,'expires_at_ms':expires,
            'counts':scope['counts'],'files':{'exclusive_count':len(scope['files']),'exclusive_bytes':sum(f['byte_count'] for f in scope['files'] if f['present']),'shared_count':scope['shared_count']},
            'protected_watchlist_count':len(scope['protected']),'protected_watchlist':scope['protected'][:20], 'retained':RETENTION}


def submit(kind, identifier, payload):
    token = payload.get('confirmation_token','')
    try: expires = int(token.split('.')[0])
    except (ValueError,AttributeError): raise HTTPException(409,'确认已失效，请重新查看永久删除范围')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        active = c.execute("SELECT * FROM workspace_purge WHERE target_kind=? AND target_id=? AND status IN ('queued','cleaning')",(kind,identifier)).fetchone()
        if active:
            if payload.get('confirmation_text') != active['target_name']: raise HTTPException(422,'请输入完整名称确认永久删除')
            return _view(c,active)
        scope, name, reason = _plan(c,kind,identifier)
        if reason: raise HTTPException(409,reason)
        if expires < now_ms() or expires > now_ms()+600_000 or token != str(expires)+'.'+digest({'scope':scope,'expires':expires}):
            raise HTTPException(409,'对象、引用或文件范围已变化，确认已失效，请重新查看永久删除范围')
        if payload.get('confirmation_text') != name: raise HTTPException(422,'请输入完整名称确认永久删除')
        purge_id = new_id('purge'); job_id = new_id('job'); stamp=now_ms()
        parameters = {'operation':'workspace_purge','purge_id':purge_id}
        c.execute("INSERT INTO job(job_id,request_key,logical_key,kind,parameters_json,parameters_hash,status,created_at_ms) VALUES(?,?,?,'cleanup',?,?,'queued',?)",(job_id,new_id('permanent-delete-request'),'workspace-purge:'+purge_id,dumps(parameters),digest(parameters),stamp))
        c.execute("INSERT INTO workspace_purge(purge_id,target_kind,target_id,target_name,job_id,scope_sha256,scope_json,status,created_at_ms) VALUES(?,?,?,?,?,?,?,'queued',?)",(purge_id,kind,identifier,name,job_id,digest(scope),dumps(scope),stamp))
        # A newly confirmed parent scope may take over an uncommitted failed
        # child authorization. The old frozen scope remains unchanged, its audit
        # points to the new confirmation, and only the new task may execute.
        superseded=[r['purge_id'] for r in c.execute("SELECT purge_id FROM workspace_purge WHERE status='failed' AND business_deleted=0 AND superseded_by IS NULL AND ((target_kind=? AND target_id=?) OR (?='strategy' AND target_kind='backtest' AND target_id IN (SELECT value FROM json_each(?))))",(kind,identifier,kind,dumps(scope['owned']['backtest'])))]
        for old_id in superseded:
            c.execute('UPDATE workspace_purge SET superseded_by=? WHERE purge_id=?',(purge_id,old_id))
            c.execute('INSERT INTO workspace_purge_audit VALUES(?,?,?,?)',(old_id,'superseded',stamp,dumps({'by_purge_id':purge_id,'new_scope_sha256':digest(scope)})))
        c.execute('INSERT INTO workspace_purge_audit VALUES(?,?,?,?)',(purge_id,'confirmed',stamp,dumps({'counts':scope['counts'],'scope_sha256':digest(scope)})))
        tasks.append_event(c,job_id,'queued','永久删除已确认，等待工作进程清理专属业务和报告文件')
        return _view(c,c.execute('SELECT * FROM workspace_purge WHERE purge_id=?',(purge_id,)).fetchone())


def _view(c,row):
    result = {k:row[k] for k in ('purge_id','target_kind','target_id','status','created_at_ms','completed_at_ms','error')}
    files = c.execute("SELECT count(*) total,sum(CASE WHEN status='removed' THEN 1 ELSE 0 END) removed,sum(removed_bytes) bytes,sum(CASE WHEN status IN ('pending','failed') THEN 1 ELSE 0 END) remaining FROM workspace_purge_file WHERE purge_id=?",(row['purge_id'],)).fetchone()
    scope=json.loads(row['scope_json'])
    can_retry=row['status']=='failed' and row['superseded_by'] is None
    can_reconfirm=can_retry and not row['business_deleted']
    if can_retry and not row['business_deleted']:
        try:
            actual,_,reason=_plan(c,row['target_kind'],row['target_id'],own_job=row['job_id'])
            can_retry=not reason and digest(actual)==row['scope_sha256']
        except HTTPException:
            can_retry=False;can_reconfirm=False
    result.update(task_id=row['job_id'],name=row['target_name'],business_deleted=bool(row['business_deleted']),counts=scope['counts'],
                  removed_file_count=files['removed'] or 0,removed_file_bytes=files['bytes'] or 0,
                  remaining_file_count=(files['remaining'] or 0) if row['business_deleted'] else len(scope['files']),can_retry=can_retry,can_reconfirm=can_reconfirm,superseded_by=row['superseded_by'])
    return result


def get_purge(purge_id):
    with connect() as c:
        row=c.execute('SELECT * FROM workspace_purge WHERE purge_id=?',(purge_id,)).fetchone()
        if not row: raise HTTPException(404,'永久清理任务不存在')
        return _view(c,row)


def list_purges(limit=20,offset=0,status=None,target_kind=None):
    if status not in (None,'unfinished','queued','cleaning','failed','completed') or target_kind not in (None,'strategy','backtest'):raise HTTPException(422,'清理列表筛选无效')
    clauses=[];params=[]
    if status=='unfinished':clauses.append("status<>'completed' AND superseded_by IS NULL")
    elif status:clauses.append('status=?');params.append(status)
    if target_kind:clauses.append('target_kind=?');params.append(target_kind)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    with connect() as c:
        return {'items':[_view(c,r) for r in c.execute('SELECT * FROM workspace_purge'+where+' ORDER BY created_at_ms DESC,rowid DESC LIMIT ? OFFSET ?',(*params,limit,offset))],
                'total':c.execute('SELECT count(*) FROM workspace_purge'+where,params).fetchone()[0],'limit':limit,'offset':offset}


def retry(purge_id):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT p.*,j.status job_status,j.lease_owner,j.lease_expires_at_ms FROM workspace_purge p JOIN job j USING(job_id) WHERE purge_id=?',(purge_id,)).fetchone()
        if not row: raise HTTPException(404,'永久清理任务不存在')
        if row['status']=='completed': return _view(c,row)
        if row['superseded_by'] or (not row['business_deleted'] and was_purged(c,row['target_kind'],row['target_id'])):raise HTTPException(409,'原确认已由新授权取代，不能重放旧清理任务')
        if c.execute("SELECT 1 FROM workspace_purge WHERE target_kind=? AND target_id=? AND purge_id<>? AND status IN ('queued','cleaning')",(row['target_kind'],row['target_id'],purge_id)).fetchone():raise HTTPException(409,'此对象已有新清理授权，请查看最新任务')
        if row['job_status'] not in tasks.TERMINAL or row['lease_owner'] is not None or row['lease_expires_at_ms'] is not None: raise HTTPException(409,'清理任务尚未停止，请等待租约释放')
        if not row['business_deleted']:
            scope,_,reason=_plan(c,row['target_kind'],row['target_id'],own_job=row['job_id'])
            if reason or digest(scope)!=row['scope_sha256']: raise HTTPException(409,reason or '清理范围已变化，请重新预览并确认永久删除')
        c.execute("UPDATE workspace_purge SET status='queued',error=NULL WHERE purge_id=?",(purge_id,))
        c.execute("UPDATE job SET status='queued',phase='queued',error_summary=NULL,finished_at_ms=NULL WHERE job_id=?",(row['job_id'],))
        tasks.append_event(c,row['job_id'],'queued','用户重试原永久清理授权，不扩大清理范围')
        return _view(c,c.execute('SELECT * FROM workspace_purge WHERE purge_id=?',(purge_id,)).fetchone())


def _open_file(key):
    """Open each component without following symlinks; caller owns returned FDs."""
    parts=PurePosixPath(key).parts
    if not parts or PurePosixPath(key).is_absolute() or '..' in parts: raise ValueError('清理文件路径不安全')
    parent=os.open(published_dir(),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent);parent=child
        descriptor=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
        return parent,descriptor,parts[-1]
    except BaseException:
        os.close(parent);raise


def _verify_file(file, *, remove=False):
    try: parent,descriptor,name=_open_file(file['object_key'])
    except FileNotFoundError: return 'absent',0
    try:
        before=os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size!=file['byte_count']: raise ValueError('专属报告文件类型或大小变化，已保留文件')
        sha=hashlib.sha256()
        while chunk:=os.read(descriptor,1024*1024):sha.update(chunk)
        if sha.hexdigest()!=file['sha256']:raise ValueError('专属报告校验和变化，已保留文件')
        after=os.stat(name,dir_fd=parent,follow_symlinks=False)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('专属报告在核验期间发生变化，已保留文件')
        if remove:
            os.unlink(name,dir_fd=parent);os.fsync(parent)
            return 'removed',before.st_size
        return 'verified',0
    finally:
        os.close(descriptor);os.close(parent)


def _delete_business(c,scope):
    owned=scope['owned']
    allowed={kind:set(identifiers) for kind,identifiers in owned.items()}
    c.create_function('workspace_purge_allowed',2,lambda kind,identifier:int(identifier in allowed.get(kind,())))
    try:
        for bid in owned['backtest']:
            c.execute('DELETE FROM backtest_deletion WHERE backtest_id=?',(bid,))
            c.execute('DELETE FROM backtest_run WHERE backtest_id=?',(bid,))
        for rid in owned['round']:
            c.execute('DELETE FROM tracking_event WHERE round_id=?',(rid,))
            c.execute('DELETE FROM tracking_policy_application WHERE round_id=?',(rid,))
            c.execute('DELETE FROM tracking_round WHERE round_id=?',(rid,))
        for run_id in owned['selection']:
            c.execute('DELETE FROM selection_candidate WHERE run_id=?',(run_id,))
            c.execute('DELETE FROM selection_run WHERE run_id=?',(run_id,))
        for sid in owned['strategy']:
            c.execute('DELETE FROM platform_setting WHERE key=?',('strategy-execution:'+sid,))
            c.execute('DELETE FROM tracking_ignore WHERE strategy_id=?',(sid,))
            c.execute('DELETE FROM strategy_deletion WHERE strategy_id=?',(sid,))
            c.execute('UPDATE strategy SET current_version=NULL,current_policy_version=NULL WHERE strategy_id=?',(sid,))
            c.execute('DELETE FROM tracking_policy_version WHERE strategy_id=?',(sid,))
            c.execute('DELETE FROM strategy_version WHERE strategy_id=?',(sid,))
            c.execute('DELETE FROM strategy WHERE strategy_id=?',(sid,))
        for pin_id in scope['pin_ids']:c.execute('DELETE FROM retention_pin WHERE pin_id=?',(pin_id,))
        for snapshot in scope['snapshots']:
            c.execute("DELETE FROM published_head WHERE snapshot_id=? AND module='backtest_result' AND scope_key IN (SELECT value FROM json_each(?))",(snapshot,dumps(owned['backtest'])))
            c.execute("INSERT INTO snapshot_gc VALUES(?,'gc_pending',?,'confirmed workspace permanent deletion') ON CONFLICT(snapshot_id) DO UPDATE SET state='gc_pending',changed_at_ms=excluded.changed_at_ms,reason=excluded.reason",(snapshot,now_ms()))
    finally:
        c.create_function('workspace_purge_allowed',2,lambda *_:0)


def execute(job):
    """Single-worker execution; SQL commit precedes resumable file unlink ledger."""
    purge_id=json.loads(job['parameters_json'])['purge_id'];owner=job['lease_owner']
    try:
        with connect() as c:
            row=c.execute('SELECT * FROM workspace_purge WHERE purge_id=?',(purge_id,)).fetchone()
            if not row or row['job_id']!=job['job_id']:raise ValueError('清理任务缺少原授权')
            scope=json.loads(row['scope_json'])
        if row['status']=='completed':
            tasks.finish(job['job_id'],'succeeded','永久删除已完成，恢复任务终态',lease_owner=owner)
            return get_purge(purge_id)
        if not row['business_deleted']:
            for file in scope['files']:_verify_file(file)
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            _require_lease(c,job['job_id'],owner)
            row=c.execute('SELECT * FROM workspace_purge WHERE purge_id=?',(purge_id,)).fetchone()
            if not row['business_deleted']:
                actual,_,reason=_plan(c,row['target_kind'],row['target_id'],own_job=job['job_id'])
                if reason:raise ValueError(reason)
                if digest(actual)!=row['scope_sha256']:raise ValueError('确认后对象、引用或文件范围已变化，请重新预览并确认')
                _delete_business(c,scope)
                for file in scope['files']:
                    c.execute('INSERT INTO workspace_purge_file(purge_id,snapshot_id,object_key,sha256,byte_count) VALUES(?,?,?,?,?)',(purge_id,file['snapshot_id'],file['object_key'],file['sha256'],file['byte_count']))
                c.execute('INSERT INTO workspace_purge_audit VALUES(?,?,?,?)',(purge_id,'business_deleted',now_ms(),dumps({'counts':scope['counts'],'retained':RETENTION})))
            c.execute("UPDATE workspace_purge SET business_deleted=1,status='cleaning',error=NULL WHERE purge_id=?",(purge_id,))
            tasks.append_event(c,job['job_id'],'cleaning','专属业务记录已永久清理，正在核验并移除专属报告文件')
        with connect() as c:
            files=[dict(r) for r in c.execute("SELECT * FROM workspace_purge_file WHERE purge_id=? AND status IN ('pending','failed') ORDER BY object_key",(purge_id,))]
        for file in files:
            # Never unlink after a lost lease/cancel. This task's controls cannot
            # pause or cancel once accepted, but recovery still verifies ownership.
            with connect() as c:_require_lease(c,job['job_id'],owner)
            try:
                state,removed=_verify_file(file,remove=True)
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE');_require_lease(c,job['job_id'],owner)
                    c.execute('UPDATE workspace_purge_file SET status=?,removed_bytes=?,error=NULL WHERE purge_id=? AND object_key=?',(state,removed,purge_id,file['object_key']))
                    c.execute('UPDATE job SET lease_expires_at_ms=? WHERE job_id=?',(now_ms()+60_000,job['job_id']))
            except Exception as error:
                with connect() as c:c.execute("UPDATE workspace_purge_file SET status='failed',error=? WHERE purge_id=? AND object_key=?",(str(error)[:500],purge_id,file['object_key']))
                raise
        with connect() as c:
            c.execute('BEGIN IMMEDIATE');_require_lease(c,job['job_id'],owner)
            for sid in scope['snapshots']:c.execute("UPDATE snapshot_gc SET state='purged',changed_at_ms=? WHERE snapshot_id=? AND state='gc_pending'",(now_ms(),sid))
            c.execute("UPDATE workspace_purge SET status='completed',completed_at_ms=?,error=NULL WHERE purge_id=?",(now_ms(),purge_id))
            c.execute('INSERT OR IGNORE INTO workspace_purge_audit VALUES(?,?,?,?)',(purge_id,'completed',now_ms(),dumps({'files':len(scope['files'])})))
        tasks.finish(job['job_id'],'succeeded','永久删除完成；共享数据、任务审计和已有备份保留',lease_owner=owner)
    except Exception as error:
        reason=str(getattr(error,'detail',error))[:500]
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current=c.execute('SELECT lease_owner,status FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
            if current and current['lease_owner']==owner and current['status']=='running':
                c.execute("UPDATE workspace_purge SET status='failed',error=? WHERE purge_id=?",(reason,purge_id))
        tasks.finish(job['job_id'],'failed','永久清理未完成，请查看原因后重试原授权',reason,lease_owner=owner)
        raise
    return get_purge(purge_id)


def _require_lease(c,job_id,owner):
    row=c.execute('SELECT status,lease_owner,lease_expires_at_ms FROM job WHERE job_id=?',(job_id,)).fetchone()
    if not row or row['status']!='running' or row['lease_owner']!=owner or not row['lease_expires_at_ms'] or row['lease_expires_at_ms']<=now_ms():raise tasks.TaskCancelled()
