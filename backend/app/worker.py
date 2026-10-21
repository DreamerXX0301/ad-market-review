"""One analytics writer; consumes only explicitly authorized durable tasks."""
from __future__ import annotations
import argparse
from datetime import date
import fcntl
import logging
import json
import os
import signal
import threading
import time
from app.core import connect,data_dir,work_dir,ensure_ready,now_ms,set_setting
from app.services import task_service as tasks
from app.adapters.providers import ProviderError

logger=logging.getLogger('shixu.worker')


def _parameters(job):return json.loads(job.get('parameters_json','{}'))


def _chart_chain(parameters):
    return (parameters.get('operation')=='sector_charts'
            and parameters.get('initialization_chain') is True
            and parameters.get('data_chain_version')=='sector-charts-v1')


def _increment_chart_chain(parameters):
    return (parameters.get('operation')=='sector_charts'
            and parameters.get('mode')=='increment' and parameters.get('increment_chain') is True
            and parameters.get('data_chain_version')=='sector-charts-increment-v1')


def _chain_authorized(connection,job):
    parameters=_parameters(job)
    recorded=connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='chain_authorized' LIMIT 1",(job['job_id'],)).fetchone()
    return bool(recorded) and (job['kind'] in ('initialise','increment') or (job['kind'] in ('topic_init','topic_refresh') and parameters.get('initialization_chain') is True) or (job['kind']=='source_check' and (_chart_chain(parameters) or _increment_chart_chain(parameters))))


def _authorize_chain(job):
    parameters=_parameters(job)
    if job['kind'] not in ('initialise','increment') and not (job['kind'] in ('topic_init','topic_refresh') and parameters.get('initialization_chain') is True) and not (job['kind']=='source_check' and (_chart_chain(parameters) or _increment_chart_chain(parameters))):return
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        state=connection.execute('SELECT status FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
        if not state or state[0] in tasks.TERMINAL or state[0]=='paused':return
        if not connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='chain_authorized'",(job['job_id'],)).fetchone():
            tasks.append_event(connection,job['job_id'],'chain_authorized','记录本次手动任务的后续数据链授权；中断后只补漏，不创建每日调度')


def _chain_target(job,completed_result=None):
    """Recover the parent's own completed date, never a newer global clock."""
    with connect() as connection:
        frozen=connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_target_frozen' ORDER BY seq LIMIT 1",(job['job_id'],)).fetchone()
        publication=connection.execute("SELECT data_end FROM dataset_snapshot WHERE job_id=? AND module='daily_raw' AND state='published' ORDER BY published_at_ms DESC LIMIT 1",(job['job_id'],)).fetchone()
    target=json.loads(frozen[0]).get('target_date') if frozen else job.get('target_date')
    if not target and publication:target=publication[0]
    if not target and job['kind'] in ('initialise','increment'):
        from app.core import raw_dir
        checkpoint=raw_dir()/'sync'/job['job_id']/'checkpoint.json'
        if checkpoint.is_file():
            plan=json.loads(checkpoint.read_text()).get('plan',{})
            if plan.get('kind')!=job['kind']:raise ValueError('后续链检查点任务类型冲突')
            target=plan.get('target')
    if not target and completed_result is not None:
        # Only the just-completed dispatch may freeze its execution result's
        # current clock. Recovery has no permission to substitute a newer date.
        target=completed_result.get('business_date') or completed_result.get('data_status',{}).get('data_end')
        if not target:
            from app.core import get_market
            target=get_market().status().get('data_end')
    if not isinstance(target,str) or date.fromisoformat(target).isoformat()!=target:raise ValueError('已完成任务缺少可核验的冻结日期，后续链未排队')
    if publication and target!=publication[0]:raise ValueError('后续链冻结日期与父任务已发布行情日期冲突')
    if not frozen:
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='chain_target_frozen'",(job['job_id'],)).fetchone():
                tasks.append_event(connection,job['job_id'],'chain_target_frozen','后续数据链日期已冻结',details={'target_date':target})
    return target


def _supplement_child(parameters, target, request_key):
    """New chains include board quotes; already frozen children keep their scope."""
    with connect() as connection:
        saved=connection.execute('SELECT * FROM job WHERE request_key=?',(request_key,)).fetchone()
    if saved and saved['kind']=='source_check' and saved['target_date']==target:
        old=json.loads(saved['parameters_json'])
        legacy={key:value for key,value in parameters.items() if key!='sector_history'}
        if old==legacy or old=={**legacy,'recent_days':20}:
            return tasks.task_view(saved['job_id'])
    return tasks.create_task('source_check',parameters,target_date=target,request_key=request_key)


def _queue_followup_once(job,completed_result=None):
    """Idempotent repair of the commit-to-child window of an authorized chain."""
    with connect() as connection:
        row=connection.execute('SELECT * FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
        if not row:return None
        job=dict(row)
        if job['status'] not in ('succeeded','partial','no_change') or not _chain_authorized(connection,job):return None
        if connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='followup_queued'",(job['job_id'],)).fetchone():return None
        if connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='chain_blocked'",(job['job_id'],)).fetchone():return None
        parameters=_parameters(job)
        if job['kind'] in ('topic_init','topic_refresh') or _chart_chain(parameters) or _increment_chart_chain(parameters):
            parent=parameters.get('parent_sync_job_id')
            ancestor=connection.execute('SELECT status FROM job WHERE job_id=?',(parent,)).fetchone()
            if not ancestor or ancestor[0] not in ('succeeded','partial','no_change'):return None
            if _chart_chain(parameters):
                previous=connection.execute('SELECT status FROM job WHERE job_id=?',(parameters.get('previous_chain_job_id'),)).fetchone()
                if not previous or previous[0] not in ('succeeded','partial','no_change'):return None
    target=_chain_target(job,completed_result)
    if job['kind']=='initialise':
        extra={'data_chain_version':'sector-charts-v1'} if parameters.get('data_chain_version')=='sector-charts-v1' else {}
        child=tasks.create_task('topic_refresh',{'source_id':'fuyao','scope':'current-full-catalog','initialization_chain':True,'parent_sync_job_id':job['job_id'],**extra},target_date=target,request_key='topics-after-initialise:'+job['job_id'])
        message='历史行情已发布，完整题材库已排队；新初始化将接续板块历史K线，再补充年内统计、目标日封板与资金' if extra else '历史行情已发布，完整题材库已排队；题材完成后一次回补年内统计，并补充目标日封板与资金'
    elif job['kind']=='increment' and parameters.get('data_chain_version')=='sector-charts-increment-v1':
        from app.core import get_market
        from app.services.sector_chart_batch_jobs import create_batch_job
        child=create_batch_job(get_market(),target,request_key='sector-charts-after-sync:'+job['job_id'],mode='increment',
            chain_parameters={'increment_chain':True,'parent_sync_job_id':job['job_id'],
                              'data_chain_version':'sector-charts-increment-v1'})
        message='主行情已发布，板块K线尾部增量已排队；只追加缺少交易日，再接续目标日盘面补充'
    elif job['kind']=='increment':
        child=_supplement_child({'operation':'market_supplement','year_history':False,'sector_history':True,'recent_days':1,'source_id':'fuyao','parent_sync_job_id':job['job_id']},target,'supplement-after-sync:'+job['job_id'])
        message='本次手动同步的封板、资金及板块日行情补充已排队；失败保留缺口，不阻止行情查询'
    elif job['kind'] in ('topic_init','topic_refresh') and parameters.get('data_chain_version')=='sector-charts-v1':
        from app.core import get_market
        from app.services.sector_chart_batch_jobs import create_batch_job
        child=create_batch_job(get_market(),target,request_key='sector-charts-after-initialise:'+parent,
            chain_parameters={'initialization_chain':True,'parent_sync_job_id':parent,
                              'previous_chain_job_id':job['job_id'],'data_chain_version':'sector-charts-v1'})
        message='完整题材已发布，行业概念历史K线全库补齐已排队；逐板块缓存后接续盘面补充'
    elif _increment_chart_chain(parameters):
        child=_supplement_child({'operation':'market_supplement','year_history':False,'sector_history':True,
            'recent_days':1,'source_id':'fuyao','parent_sync_job_id':parent},target,'supplement-after-sync:'+parent)
        message='板块K线尾部增量已核验，目标日封板、资金及板块日行情补充已排队；历史缺口继续保留'
    else:
        request_key='full-supplement-after-initialise:'+parent
        parameters={'operation':'market_supplement','year_history':True,'sector_history':True,'recent_days':1,'source_id':'fuyao','parent_sync_job_id':parent,'previous_chain_job_id':job['job_id']}
        child=_supplement_child(parameters,target,request_key)
        message='完整题材已发布，年内涨跌停统计一次回补、目标日封板、资金及板块日行情补充已排队；日后增量会自动补当日，失败可单独补缺'
    with connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute("SELECT 1 FROM job_event WHERE job_id=? AND stage='followup_queued'",(job['job_id'],)).fetchone():
            tasks.append_event(connection,job['job_id'],'followup_queued',message,details={'next_task_id':child['job_id'],'target_date':target,'child_status':child['status']})
    return child


def _queue_followup(job,completed_result=None):
    try:return _queue_followup_once(job,completed_result)
    except Exception as error:
        reason=str(error) if isinstance(error,ValueError) else type(error).__name__
        with connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            previous=connection.execute("SELECT details_json FROM job_event WHERE job_id=? AND stage='chain_blocked' ORDER BY seq DESC LIMIT 1",(job['job_id'],)).fetchone()
            if not previous or json.loads(previous[0]).get('reason')!=reason:
                tasks.append_event(connection,job['job_id'],'chain_blocked','后续数据链排队失败；不会自动反复尝试，请核对后手动重试父任务','error',{'reason':reason})
        raise


def recover_followups(limit=20):
    """Repair only already authorized successful parents; never resume a stop."""
    with connect() as connection:
        rows=connection.execute("""SELECT j.* FROM job j WHERE j.status IN ('succeeded','partial','no_change')
            AND NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='followup_queued')
            AND NOT EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='chain_blocked')
            AND EXISTS(SELECT 1 FROM job_event e WHERE e.job_id=j.job_id AND e.stage='chain_authorized')
            AND (j.kind IN ('initialise','increment')
                OR ((j.kind IN ('topic_init','topic_refresh') AND json_extract(j.parameters_json,'$.initialization_chain')=1
                      OR (j.kind='source_check' AND json_extract(j.parameters_json,'$.operation')='sector_charts'
                          AND json_extract(j.parameters_json,'$.initialization_chain')=1
                          AND json_extract(j.parameters_json,'$.data_chain_version')='sector-charts-v1')
                      OR (j.kind='source_check' AND json_extract(j.parameters_json,'$.operation')='sector_charts'
                          AND json_extract(j.parameters_json,'$.mode')='increment'
                          AND json_extract(j.parameters_json,'$.increment_chain')=1
                          AND json_extract(j.parameters_json,'$.data_chain_version')='sector-charts-increment-v1'))
                    AND EXISTS(SELECT 1 FROM job p WHERE p.job_id=json_extract(j.parameters_json,'$.parent_sync_job_id') AND p.status IN ('succeeded','partial','no_change'))))
            ORDER BY j.created_at_ms LIMIT ?""",(max(1,min(limit,100)),)).fetchall()
    result=[]
    for row in rows:
        try:
            child=_queue_followup(dict(row))
            if child is not None:result.append(child)
        except Exception as error:
            # _queue_followup has persisted a visible blocked state. Isolate a
            # broken historical parent so other authorized work can continue.
            logger.error('后续链已阻断 %s %s；需手动重试父任务',row['job_id'],type(error).__name__)
    return result


def dispatch(job):
    with connect() as connection:state=connection.execute('SELECT status FROM job WHERE job_id=?',(job['job_id'],)).fetchone()
    if not state or state[0] in tasks.TERMINAL:
        if state and state[0] in ('succeeded','partial','no_change'):_queue_followup(job)
        return
    if state[0]=='paused':raise tasks.TaskPaused()
    _authorize_chain(job)
    identifier=job['job_id']
    callback=lambda stage,percent,message=None,details=None:tasks.progress(identifier,stage,percent,message,details)
    kind=job['kind']
    if kind in ('initialise','increment'):
        from app.services.sync_service import execute_sync
        result=execute_sync(identifier,callback)
        status='no_change' if result.get('no_change') or result.get('status')=='no_change' else 'partial' if result.get('quality')=='partial' else 'succeeded'
        tasks.finish(identifier,status,result.get('message','行情数据已校验并发布'))
        from app.services.workspace_automation import run_after_sync
        run_after_sync(identifier)
        _queue_followup(job,result)
        from app.services.line_alerts import after_sync
        after_sync(job)
        from app.services.listing_status import after_sync as listing_after_sync
        listing_after_sync(job)
    elif kind=='backtest':
        from app.analytics.backtest_store import execute_backtest
        execute_backtest(identifier,callback)
        tasks.finish(identifier,'succeeded','回测账本与七项指标已发布')
    elif kind=='research':
        from app.services.research_service import execute_research
        result=execute_research(identifier,callback)
        tasks.finish(identifier,'partial' if result.get('status')=='partial' else 'succeeded','指定标的研究已留存；材料缺口见报告' if result.get('status')=='partial' else '指定标的研究已留存')
    elif kind in ('topic_init','topic_refresh'):
        from app.services.topic_collection import execute_topic_collection
        result=execute_topic_collection(identifier,callback)
        tasks.finish(identifier,'partial','真实当前题材库已发布')
        _queue_followup(job,result)
    elif kind=='source_check' and _parameters(job).get('operation')=='local_coverage_check':
        from app.services.local_coverage_check import execute_check
        result=execute_check(identifier,callback)
        tasks.finish(identifier,'partial' if result.get('quality')!='complete' else 'succeeded',result['message'])
    elif kind=='source_check' and _parameters(job).get('operation')=='line_alert_check':
        from app.services.line_alerts import execute_check
        execute_check(job)
    elif kind=='source_check' and _parameters(job).get('operation')=='listing_status_check':
        from app.services.listing_status import execute_check
        execute_check(job)
    elif kind=='source_check' and _parameters(job).get('operation')=='quote_gap_source_check':
        from app.services.quote_gap_source_check import execute_check
        execute_check(job)
    elif kind=='source_check' and json.loads(job.get('parameters_json','{}')).get('operation')=='market_supplement':
        from app.services.market_supplement import execute_supplement
        result=execute_supplement(identifier,callback)
        tasks.finish(identifier,'partial' if result.get('quality')!='complete' else 'succeeded',result.get('message','盘面补充数据已核验'))
    elif kind=='source_check' and _parameters(job).get('operation')=='sector_chart':
        from app.services.sector_chart_jobs import execute_chart_job
        result=execute_chart_job(identifier,callback)
        tasks.finish(identifier,'succeeded' if result.get('quality')=='complete' else 'partial',result.get('message','板块历史K线已核验并缓存'))
    elif kind=='source_check' and _parameters(job).get('operation')=='sector_charts':
        from app.services.sector_chart_batch_jobs import execute_batch_job
        result=execute_batch_job(identifier,callback)
        tasks.finish(identifier,'succeeded' if result.get('quality')=='complete' else 'partial',result.get('message','全量板块历史K线已核验并缓存'))
        _queue_followup(job,result)
    elif kind=='selection' and _parameters(job).get('operation')=='strategy_batch_item':
        from app.services.strategy_batches import execute_item
        execute_item(job)
    elif kind=='source_check' and _parameters(job).get('operation')=='strategy_batch_tracking':
        from app.services.strategy_batches import execute_tracking
        execute_tracking(job)
    elif kind=='source_check' and _parameters(job).get('operation')=='strategy_batch':
        from app.services.strategy_batches import finish_batch
        finish_batch(job)
    else:raise ValueError('该任务类型尚未实现工作进程执行，请通过对应模块手动运行')


def run(once=False):
    ensure_ready(); stop=threading.Event();owner='worker-'+str(os.getpid())
    lock_path=work_dir()/'worker.lock'
    with lock_path.open('a+b') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('已有工作进程运行，拒绝第二个分析写入者')
        for signum in (signal.SIGTERM,signal.SIGINT):
            signal.signal(signum,lambda *_:stop.set())
        next_research_cleanup=0
        while not stop.is_set():
            if time.monotonic()>=next_research_cleanup:
                try:
                    from app.services.research_service import cleanup_expired
                    cleanup_expired(limit=100)
                except Exception:
                    logger.warning('个股研究缓存清理稍后重试')
                next_research_cleanup=time.monotonic()+3600
            set_setting('worker_heartbeat',{'owner':owner,'at_ms':now_ms()})
            recover_followups()
            from app.services.line_alerts import recover_sync_followups
            recover_sync_followups()
            from app.services.listing_status import recover_sync_followups as recover_listing_followups
            recover_listing_followups()
            job=tasks.claim_next(owner)
            if not job:
                if once:return
                stop.wait(1.5);continue
            heartbeat_stop=threading.Event()
            def heartbeat(identifier=job['job_id'],stop_event=heartbeat_stop,lease_owner=job['lease_owner']):
                while not stop_event.wait(15):
                    with connect() as c:
                        c.execute("UPDATE job SET lease_expires_at_ms=? WHERE job_id=? AND status='running' AND lease_owner=?",(now_ms()+60_000,identifier,lease_owner))
                    set_setting('worker_heartbeat',{'owner':owner,'at_ms':now_ms(),'task_id':identifier})
            thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
            try:
                logger.info('执行 %s %s',job['kind'],job['job_id'])
                dispatch(job)
            except tasks.TaskPaused:logger.info('任务已暂停 %s',job['job_id'])
            except tasks.TaskCancelled:logger.info('任务已取消 %s',job['job_id'])
            except Exception as error:
                # Never log URLs, signed downloads, request headers or arbitrary remote bodies.
                message=str(error) if isinstance(error,(ProviderError,ValueError)) else '任务未完成：'+type(error).__name__
                logger.error('%s %s',job['job_id'],message)
                tasks.finish(job['job_id'],'failed',message,message,lease_owner=job['lease_owner'])
            finally:
                heartbeat_stop.set();thread.join(timeout=2)
            if once:return


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--once',action='store_true')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    run(parser.parse_args().once)
