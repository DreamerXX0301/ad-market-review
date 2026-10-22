"""Opt-in local computations after a manual sync, separate from data fetching."""
import json
from fastapi import HTTPException
from app.core import connect,now_ms,setting,dumps
from app.services import strategies,tracking,task_service as tasks


def get_settings(strategy_id):
    strategies.get_strategy(strategy_id)
    value=setting('strategy-execution:'+strategy_id,{})
    return {'strategy_id':strategy_id,'refresh_after_sync':bool(value.get('refresh_after_sync',False)),'auto_track':bool(value.get('auto_track',False))}


def configure(strategy_id,payload):
    for field in ('refresh_after_sync','auto_track'):
        if field in payload and not isinstance(payload[field],bool):raise HTTPException(422,'执行开关必须为true或false')
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        from .workspace_deletion import require_strategy_present
        require_strategy_present(connection,strategy_id)
        strategies.strategy_view(connection,strategy_id)
        key='strategy-execution:'+strategy_id
        row=connection.execute('SELECT value_json FROM platform_setting WHERE key=?',(key,)).fetchone()
        value=json.loads(row[0]) if row else {}
        current={'strategy_id':strategy_id,**{field:bool(value.get(field,False)) for field in ('refresh_after_sync','auto_track')}}
        current.update({field:payload[field] for field in ('refresh_after_sync','auto_track') if field in payload})
        if current['auto_track'] and not current['refresh_after_sync']:raise HTTPException(422,'自动入池需要同时开启同步后重新筛选')
        connection.execute('INSERT INTO platform_setting(key,value_json,updated_at_ms) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at_ms=excluded.updated_at_ms',(key,dumps({**current,'updated_at_ms':now_ms()}),now_ms()))
    return current


def run_after_sync(parent_job_id):
    """Defaults do nothing. User configured flags are the authority for auto-joins."""
    results=[]
    with connect() as c:
        state=c.execute('SELECT status FROM job WHERE job_id=?',(parent_job_id,)).fetchone()
        count=c.execute("SELECT count(*) FROM tracking_round WHERE status='tracking'").fetchone()[0]
    if not state or state[0] not in ('succeeded','no_change','partial'):return []
    for strategy in strategies.list_strategies()['items']:
        if strategy['archived']:continue
        result={'strategy_id':strategy['strategy_id'],'name':strategy['name']}
        try:
            config=get_settings(strategy['strategy_id'])
            if not config['refresh_after_sync']:continue
            run=strategies.run_strategy(strategy['strategy_id'],{'request_key':parent_job_id+':selection:'+strategy['strategy_id']})
            result.update(run_id=run['run_id'],matched=run['matched_count'],unknown=run['unknown_count'],added_count=0)
            if config['auto_track']:
                ids=[r['instrument_id'] for r in run['items'] if r['decision']=='matched']
                for offset in range(0,len(ids),500):
                    joined=tracking.add_tracking({'run_id':run['run_id'],'instrument_ids':ids[offset:offset+500]})
                    result['added_count']+=joined['added_count']
                count+=result['added_count']
        except Exception as error:
            result['error']=str(getattr(error,'detail','本地策略计算未完成，请手动重试'))
        results.append(result)
    if count:
        try:results.append({'tracking_review':tracking.review_tracking({'request_key':parent_job_id+':tracking-review'})})
        except Exception as error:results.append({'tracking_review_error':str(getattr(error,'detail','跟踪核对未完成，请手动重试'))})
    if results:
        with connect() as c:
            c.execute('BEGIN IMMEDIATE')
            tasks.append_event(c,parent_job_id,'workspace_followup','同步后的本地策略筛选与跟踪核对已执行',details={'results':results})
    return results
