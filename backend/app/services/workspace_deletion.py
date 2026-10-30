"""Reversible list visibility; immutable runs, results, pins and rounds stay intact."""
import json
from fastapi import HTTPException
from app.core import connect, new_id, now_ms
from app.services.task_service import TERMINAL


def require_strategy_present(connection, strategy_id):
    from .workspace_purge import was_purged, require_not_purging
    if was_purged(connection, "strategy", strategy_id):
        raise HTTPException(409,"策略已永久删除，请使用新的策略")
    require_not_purging(connection,"strategy",strategy_id)
    if connection.execute('SELECT 1 FROM strategy_deletion WHERE strategy_id=?',(strategy_id,)).fetchone():
        raise HTTPException(409,'策略已移入回收站，请先恢复策略')


def backtest_block_reason(row):
    """A cancelled queued task has a stale calculation state by design."""
    task=row.get('job_status')
    if task not in TERMINAL:
        return '回测任务尚未结束，请先取消任务，再移入回收站'
    if row.get('lease_owner') is not None or row.get('lease_expires_at_ms') is not None:
        return '回测执行租约尚未释放，请等待任务停止后再操作'
    state=row['status']
    if state in ('completed','failed','cancelled') or task=='cancelled':return None
    return '回测计算与任务状态尚未同时结束，请先核对原任务'


def _parameters(raw):
    try:
        value=json.loads(raw)
        return value if isinstance(value,dict) else None
    except (TypeError,ValueError):return None


def strategy_block_reason(connection,strategy_id,*,ignored_job_id=None,facts=None):
    # Cleanup task parameters intentionally contain only purge_id; follow the
    # authorization registry rather than guessing strategy references in jobs.
    purging=(any(row['status'] in ('queued','cleaning') and row['job_id']!=ignored_job_id
                 and (row['target_kind']=='backtest' or row['target_kind']=='strategy' and row['target_id']==strategy_id)
                 for row in facts['purges']) if facts is not None else connection.execute("SELECT 1 FROM workspace_purge p LEFT JOIN backtest_run b ON p.target_kind='backtest' AND b.backtest_id=p.target_id WHERE p.status IN ('queued','cleaning') AND p.job_id IS NOT ? AND ((p.target_kind='strategy' AND p.target_id=?) OR b.strategy_id=?) LIMIT 1",(ignored_job_id,strategy_id,strategy_id)).fetchone())
    if purging:
        return '所属策略或关联报告已有永久清理任务，请先等待原任务结束'
    backtests=facts['backtests'] if facts is not None else connection.execute('SELECT b.*,j.status job_status,j.lease_owner,j.lease_expires_at_ms FROM backtest_run b JOIN job j USING(job_id) WHERE b.strategy_id=?',(strategy_id,))
    for row in backtests:
        reason=backtest_block_reason(dict(row))
        if reason:return reason
    has_tracking=bool(facts['active']) if facts is not None else connection.execute("SELECT 1 FROM tracking_round WHERE strategy_id=? AND status='tracking' LIMIT 1",(strategy_id,)).fetchone() is not None
    related_jobs=facts['related_jobs'] if facts is not None else {row[0] for row in connection.execute('SELECT job_id FROM selection_run WHERE strategy_id=?',(strategy_id,))}
    jobs=facts['jobs'] if facts is not None else connection.execute("SELECT * FROM job WHERE status NOT IN ('succeeded','no_change','partial','failed','cancelled') OR lease_owner IS NOT NULL OR lease_expires_at_ms IS NOT NULL")
    for row in jobs:
        if row['job_id']==ignored_job_id:continue
        parameters=_parameters(row['parameters_json'])
        if parameters is None:
            if row['kind'] in ('selection','tracking_review','source_check'):
                return '存在无法核对策略引用的活动任务，请先核对或取消任务'
            continue
        target=parameters.get('target_type','stock')
        if target=='sector':continue
        if target!='stock':return '活动任务的策略目标类型无法核对，请先核对或取消任务'
        references=row['job_id'] in related_jobs or parameters.get('strategy_id')==strategy_id
        is_batch=parameters.get('operation') in ('strategy_batch','strategy_batch_item','strategy_batch_tracking')
        ids=parameters.get('strategy_ids',[]) if is_batch else []
        items=parameters.get('items',[]) if is_batch else []
        if isinstance(ids,list) and all(isinstance(identifier,str) and identifier for identifier in ids):references=references or strategy_id in ids
        else:return '活动批次的策略引用不完整，请先核对或取消批次'
        if isinstance(items,list):
            if any(not isinstance(item,dict) or not isinstance(item.get('strategy_id'),str) or not item['strategy_id'] for item in items):return '活动批次的策略引用不完整，请先核对或取消批次'
            references=references or any(item.get('strategy_id')==strategy_id for item in items)
        else:return '活动批次的策略引用不完整，请先核对或取消批次'
        if is_batch and not ids and not items and not parameters.get('strategy_id'):
            return '活动批次的策略引用不完整，请先核对或取消批次'
        if row['kind']=='tracking_review' and not parameters.get('strategy_id') and has_tracking:references=True
        if references:return '此策略仍被活动筛选、跟踪核验或批量任务引用，请先取消相关任务'
    return None


def strategy_visibility(connection,strategy_id,*,facts=None):
    marker=facts['marker'] if facts is not None else connection.execute('SELECT * FROM strategy_deletion WHERE strategy_id=?',(strategy_id,)).fetchone()
    count=len(facts['backtests']) if facts is not None else connection.execute('SELECT count(*) FROM backtest_run WHERE strategy_id=?',(strategy_id,)).fetchone()[0]
    visible=sum(row['deleted_backtest'] is None for row in facts['backtests']) if facts is not None else connection.execute('SELECT count(*) FROM backtest_run b LEFT JOIN backtest_deletion d USING(backtest_id) WHERE b.strategy_id=? AND d.backtest_id IS NULL',(strategy_id,)).fetchone()[0]
    reason=None if marker else strategy_block_reason(connection,strategy_id,facts=facts)
    from .workspace_purge import active_purge
    purge=(next(({key:row[key] for key in ('purge_id','status','business_deleted')} for row in facts['purges']
                if row['target_kind']=='strategy' and row['target_id']==strategy_id and row['superseded_by'] is None and row['status'] in ('queued','cleaning','failed')),None)
           if facts is not None else active_purge(connection,'strategy',strategy_id))
    related_purge=(any(row['target_kind']=='backtest' and row['status'] in ('queued','cleaning') for row in facts['purges']) if facts is not None
                   else connection.execute("SELECT 1 FROM workspace_purge p JOIN backtest_run b ON b.backtest_id=p.target_id WHERE p.target_kind='backtest' AND b.strategy_id=? AND p.status IN ('queued','cleaning') LIMIT 1",(strategy_id,)).fetchone()) if marker else None
    restore_reason='永久清理已确认，请查看清理任务' if (purge and purge['status'] in ('queued','cleaning')) or related_purge else None
    return {'active_purge':purge,'deleted':marker is not None,'deleted_at_ms':marker['deleted_at_ms'] if marker else None,
            'can_delete':marker is None and reason is None,'delete_blocked_reason':reason,
            'can_restore':marker is not None and not restore_reason,'restore_blocked_reason':restore_reason,
            'backtest_count':count,'related_backtests_count':visible,'cascade_backtest_count':visible}


def delete_strategy(strategy_id):
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT archived FROM strategy WHERE strategy_id=?',(strategy_id,)).fetchone()
        if not row:raise HTTPException(404,'策略不存在')
        old=connection.execute('SELECT * FROM strategy_deletion WHERE strategy_id=?',(strategy_id,)).fetchone()
        if old:
            count=connection.execute('SELECT count(*) FROM backtest_deletion WHERE strategy_deletion_id=?',(old['deletion_id'],)).fetchone()[0]
            return {'strategy_id':strategy_id,'deleted':True,'deleted_at_ms':old['deleted_at_ms'],'affected_backtests':count}
        reason=strategy_block_reason(connection,strategy_id)
        if reason:raise HTTPException(409,reason)
        identifier=new_id('strategy-deletion');stamp=now_ms()
        connection.execute('INSERT INTO strategy_deletion VALUES(?,?,?,?)',(strategy_id,identifier,stamp,row['archived']))
        connection.execute('UPDATE strategy SET archived=1 WHERE strategy_id=?',(strategy_id,))
        changed=connection.execute('INSERT INTO backtest_deletion(backtest_id,deleted_at_ms,strategy_deletion_id) SELECT b.backtest_id,?,? FROM backtest_run b LEFT JOIN backtest_deletion d USING(backtest_id) WHERE b.strategy_id=? AND d.backtest_id IS NULL',(stamp,identifier,strategy_id)).rowcount
        return {'strategy_id':strategy_id,'deleted':True,'deleted_at_ms':stamp,'affected_backtests':changed,
                'tracking_preserved':True,'watchlist_preserved':True}


def restore_strategy(strategy_id):
    from .workspace_purge import require_not_purging
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        require_not_purging(connection,'strategy',strategy_id)
        if connection.execute("SELECT 1 FROM workspace_purge p JOIN backtest_run b ON b.backtest_id=p.target_id WHERE p.target_kind='backtest' AND b.strategy_id=? AND p.status IN ('queued','cleaning') LIMIT 1",(strategy_id,)).fetchone():raise HTTPException(409,'关联报告已确认永久清理，请等待原任务结束后再恢复策略')
        if not connection.execute('SELECT 1 FROM strategy WHERE strategy_id=?',(strategy_id,)).fetchone():raise HTTPException(404,'策略不存在')
        marker=connection.execute('SELECT * FROM strategy_deletion WHERE strategy_id=?',(strategy_id,)).fetchone()
        changed=0
        if marker:
            changed=connection.execute('DELETE FROM backtest_deletion WHERE strategy_deletion_id=?',(marker['deletion_id'],)).rowcount
            connection.execute('UPDATE strategy SET archived=? WHERE strategy_id=?',(marker['previous_archived'],strategy_id))
            connection.execute('DELETE FROM strategy_deletion WHERE strategy_id=?',(strategy_id,))
        return {'strategy_id':strategy_id,'deleted':False,'deleted_at_ms':None,'affected_backtests':changed}
